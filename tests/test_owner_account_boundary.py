"""Owner account HTTP tests with isolated in-memory collections."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import re

import bcrypt
import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rental import owner_router, security

OWNER_ID = ObjectId('64a000000000000000000001')


def matches(doc, query):
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if '$in' in expected and actual not in expected['$in']:
                return False
            if '$ne' in expected and actual == expected['$ne']:
                return False
            if '$regex' in expected and not re.search(expected['$regex'], str(actual or ''), re.I):
                return False
        elif actual != expected:
            return False
    return True


class Users:
    def __init__(self):
        self.docs = []
        self.inserted = []
        self.updates = []
        self.match_update = True

    async def find_one(self, query):
        return next((deepcopy(doc) for doc in self.docs if matches(doc, query)), None)

    async def insert_one(self, doc):
        saved = deepcopy(doc)
        saved['_id'] = ObjectId()
        self.docs.append(saved)
        self.inserted.append(saved)
        return SimpleNamespace(inserted_id=saved['_id'])

    async def update_one(self, query, update):
        self.updates.append((deepcopy(query), deepcopy(update)))
        for doc in self.docs:
            if self.match_update and matches(doc, query):
                doc.update(update['$set'])
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class Cursor:
    def __init__(self, docs):
        self.docs = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.docs)
        except StopIteration:
            raise StopAsyncIteration


@pytest.fixture
def endpoint(monkeypatch):
    users = Users()
    props = SimpleNamespace(find=lambda *_: Cursor([]), update_many=AsyncMock(return_value=SimpleNamespace(modified_count=0)))
    contracts = SimpleNamespace(find_one=AsyncMock(return_value=None))
    db = SimpleNamespace(app_users=users, properties=props, rental_contracts=contracts)
    auth = AsyncMock(return_value={'_id': 'admin-test'})
    audit = AsyncMock()
    monkeypatch.setattr(owner_router, 'get_db', lambda: db)
    monkeypatch.setattr(owner_router, 'auth_admin', auth)
    monkeypatch.setattr(security, 'audit_log', audit)
    app = FastAPI()
    app.include_router(owner_router.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, users=users, db=db, auth=auth, audit=audit)


def add_owner(endpoint, **overrides):
    doc = {'_id': OWNER_ID, 'name': 'Test Owner', 'email': 'owner@example.test', 'role': 'landlord', 'status': 'active'}
    doc.update(overrides)
    endpoint.users.docs.append(doc)
    return doc


def test_generated_password_is_random_hashed_and_never_stored_plaintext(endpoint, monkeypatch):
    generated = ['first-random-test-password', 'second-random-test-password']
    calls = []

    def random_password(size):
        calls.append(size)
        return generated[len(calls) - 1]

    monkeypatch.setattr(owner_router.secrets, 'token_urlsafe', random_password)
    for index, expected in enumerate(generated):
        response = endpoint.client.post('/admin/owners', json={'name': 'Test Owner', 'email': f'owner{index}@example.test'})
        assert response.status_code == 200
        assert response.json()['temp_password'] == expected
        doc = endpoint.users.inserted[index]
        assert bcrypt.checkpw(expected.encode(), doc['password_hash'].encode())
        assert '_temp_password' not in doc
        assert expected not in doc.values()
    assert calls == [24, 24]


def test_custom_password_is_not_returned_or_persisted_plaintext(endpoint):
    response = endpoint.client.post('/admin/owners', json={'name': ' Test Owner ', 'email': ' OWNER@EXAMPLE.TEST ', 'password': 'safe-custom-password'})
    assert response.status_code == 200
    assert response.json()['temp_password'] is None
    doc = endpoint.users.inserted[0]
    assert doc['email'] == 'owner@example.test'
    assert doc['name'] == 'Test Owner'
    assert bcrypt.checkpw(b'safe-custom-password', doc['password_hash'].encode())
    assert 'safe-custom-password' not in doc.values()


@pytest.mark.parametrize('body', [b'', b'{', b'null', b'[]', b'"text"'])
@pytest.mark.parametrize('method', ['POST', 'PUT'])
def test_malformed_bodies_are_rejected_without_writes(endpoint, body, method):
    add_owner(endpoint)
    path = '/admin/owners' if method == 'POST' else f'/admin/owners/{OWNER_ID}'
    response = endpoint.client.request(method, path, content=body)
    assert response.status_code == 400
    assert not endpoint.users.inserted and not endpoint.users.updates


@pytest.mark.parametrize('patch', [
    {'name': ''}, {'name': {}}, {'email': 'invalid'}, {'email': 'a@@example.test'},
    {'phone': []}, {'state': 'TEXAS'}, {'password': 'short'},
    {'password': 'x' * 73}, {'password': 'é' * 37}, {'password': {}},
    {'role': 'admin'}, {'deleted': True}, {'password_hash': 'injected'},
])
def test_create_rejects_invalid_or_privileged_fields(endpoint, patch):
    body = {'name': 'Test', 'email': 'owner@example.test', **patch}
    response = endpoint.client.post('/admin/owners', json=body)
    assert response.status_code == 400
    assert not endpoint.users.inserted


def test_email_duplicate_check_matches_login_case_insensitivity(endpoint):
    add_owner(endpoint, email='OWNER@EXAMPLE.TEST')
    response = endpoint.client.post('/admin/owners', json={'name': 'Test', 'email': 'owner@example.test'})
    assert response.status_code == 409
    assert not endpoint.users.inserted


@pytest.mark.parametrize('role', ['tenant', 'admin', 'contractor'])
@pytest.mark.parametrize('method', ['PUT', 'GET', 'DELETE'])
def test_owner_endpoints_cannot_target_other_account_roles(endpoint, role, method):
    doc = add_owner(endpoint, role=role)
    response = endpoint.client.request(method, f'/admin/owners/{OWNER_ID}', json={'name': 'Changed'})
    assert response.status_code == 404
    assert doc['name'] == 'Test Owner'
    assert not endpoint.users.updates
    endpoint.db.properties.update_many.assert_not_awaited()


@pytest.mark.parametrize('patch', [{'deleted': True}, {'status': 'deleted'}])
@pytest.mark.parametrize('method', ['PUT', 'GET', 'DELETE'])
def test_deleted_owners_are_not_mutated_or_exposed_by_active_profile_routes(endpoint, patch, method):
    add_owner(endpoint, **patch)
    response = endpoint.client.request(method, f'/admin/owners/{OWNER_ID}', json={'name': 'Changed'})
    assert response.status_code == 404
    assert not endpoint.users.updates


@pytest.mark.parametrize('body, status', [
    ({'status': 'deleted'}, 409), ({'status': 'inactive'}, 409),
    ({'email': 'other@example.test'}, 409), ({'role': 'admin'}, 400),
    ({'deleted': True}, 400), ({'password': 'replacement'}, 400),
    ({'kyc_status': 'unknown'}, 400), ({'name': None}, 400),
])
def test_profile_edit_cannot_bypass_lifecycle_or_identity_controls(endpoint, body, status):
    add_owner(endpoint)
    response = endpoint.client.put(f'/admin/owners/{OWNER_ID}', json=body)
    assert response.status_code == status
    assert not endpoint.users.updates


@pytest.mark.parametrize('role', ['owner', 'landlord'])
def test_profile_edit_preserves_login_and_status_and_accepts_both_owner_roles(endpoint, role):
    doc = add_owner(endpoint, role=role)
    response = endpoint.client.put(f'/admin/owners/{OWNER_ID}', json={'name': ' Changed ', 'email': 'OWNER@EXAMPLE.TEST', 'status': 'active', 'company': 'Example LLC'})
    assert response.status_code == 200
    assert doc['name'] == 'Changed'
    assert doc['business_name'] == 'Example LLC'
    update = endpoint.users.updates[0][1]['$set']
    assert 'email' not in update and 'status' not in update
    assert 'updated_at' in endpoint.users.updates[0][0]


def test_concurrent_profile_change_returns_conflict(endpoint):
    doc = add_owner(endpoint)
    endpoint.users.match_update = False
    response = endpoint.client.put(f'/admin/owners/{OWNER_ID}', json={'name': 'Changed'})
    assert response.status_code == 409
    assert doc['name'] == 'Test Owner'


def test_deactivation_still_refuses_active_contracts(endpoint):
    doc = add_owner(endpoint)
    endpoint.db.properties.find = lambda *_: Cursor([{'_id': ObjectId()}])
    endpoint.db.rental_contracts.find_one.return_value = {'status': 'active'}
    response = endpoint.client.delete(f'/admin/owners/{OWNER_ID}')
    assert response.status_code == 409
    assert not endpoint.users.updates
    assert doc['status'] == 'active'
    endpoint.db.properties.update_many.assert_not_awaited()


def test_valid_deactivation_keeps_account_history(endpoint):
    doc = add_owner(endpoint)
    response = endpoint.client.delete(f'/admin/owners/{OWNER_ID}')
    assert response.status_code == 200
    assert doc['deleted'] is True
    assert doc['name'] == 'Test Owner'
    endpoint.db.properties.update_many.assert_awaited_once()
    endpoint.audit.assert_awaited_once()


@pytest.mark.parametrize('method', ['POST', 'PUT', 'GET', 'DELETE'])
@pytest.mark.parametrize('status', [401, 403])
def test_admin_authentication_is_required_before_processing(endpoint, method, status):
    endpoint.auth.side_effect = HTTPException(status_code=status, detail='Unauthorized')
    path = '/admin/owners' if method == 'POST' else f'/admin/owners/{OWNER_ID}'
    response = endpoint.client.request(method, path, content=b'{')
    assert response.status_code == status
    assert not endpoint.users.inserted and not endpoint.users.updates
