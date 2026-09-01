import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.lease_renewal_tenant_response_router as response


def run(coro): return asyncio.run(coro)


class InsertResult:
    def __init__(self): self.inserted_id = ObjectId()


class Cursor:
    def __init__(self, docs): self.docs = list(docs)
    def limit(self, n): self.docs = self.docs[:n]; return self
    async def to_list(self, n): return self.docs[:n]


class Collection:
    def __init__(self, docs=None): self.docs = list(docs or [])
    async def find_one(self, query):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in query.items())), None)
    def find(self, query):
        return Cursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])
    async def insert_one(self, doc): self.docs.append(doc); return InsertResult()


def fixture(recommendation='renew'):
    cid, pid, tid, qid = [ObjectId() for _ in range(4)]
    end = datetime.now(timezone.utc) + timedelta(days=30)
    contract = {'_id': cid, 'status': 'active', 'property_id': str(pid), 'tenant_id': str(tid), 'rent_amount': 1200, 'end_date': end.isoformat()}
    proposal = {'_id': qid, 'status': 'approved', 'lease_id': str(cid), 'property_id': str(pid), 'tenant_id': str(tid), 'current_rent': 1200, 'proposed_rent': 1250, 'lease_end_date': end.isoformat(), 'recommendation': recommendation}
    class DB: pass
    db = DB(); db.tenants = Collection([{'_id': tid}]); db.rental_contracts = Collection([contract]); db.properties = Collection([{'_id': pid, 'address': '121 Oak Ave'}]); db.lease_renewal_proposals = Collection([proposal]); db.lease_renewal_notification_outbox = Collection([{'proposal_id': str(qid), 'tenant_id': str(tid), 'status': 'sent'}]); db.lease_renewal_responses = Collection()
    return db, proposal, {'_id': str(tid), 'id': str(tid), 'role': 'tenant'}


def offer_digest(db, proposal, tenant):
    async def get():
        canonical, terms, digest = await response._offer(db, proposal, tenant['_id'])
        return digest
    return run(get())


def test_acceptance_records_intent_without_contractual_authority(monkeypatch):
    db, proposal, tenant = fixture()
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': ObjectId(tenant['_id'])}))
    digest = offer_digest(db, proposal, tenant)
    result = run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': digest}, db, tenant))
    assert result['ok'] is True and result['idempotent'] is False
    saved = db.lease_renewal_responses.docs[0]
    assert saved['authority'] == 'tenant_authenticated_intent_only'
    assert saved['_id'] == proposal['_id']
    assert saved['creates_contract'] is False
    assert saved['activates_occupancy'] is False
    assert db.rental_contracts.docs[0]['status'] == 'active'


async def async_value(value): return value


def test_stale_digest_and_extra_payload_fail_closed(monkeypatch):
    db, proposal, tenant = fixture()
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': ObjectId(tenant['_id'])}))
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': '0' * 64}, db, tenant))
    assert exc.value.detail == 'renewal_response_terms_changed'
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': '0' * 64, 'rent': 1}, db, tenant))
    assert exc.value.detail == 'renewal_response_payload_invalid'


def test_nonrenew_can_only_be_acknowledged(monkeypatch):
    db, proposal, tenant = fixture('non_renew')
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': ObjectId(tenant['_id'])}))
    digest = offer_digest(db, proposal, tenant)
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': digest}, db, tenant))
    assert exc.value.detail == 'renewal_response_decision_invalid'
    assert run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'acknowledge', 'terms_digest': digest}, db, tenant))['ok']


def test_cross_tenant_and_unsent_offer_are_hidden(monkeypatch):
    db, proposal, tenant = fixture(); other = ObjectId()
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': other}))
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': '0' * 64}, db, {'_id': str(other), 'role': 'tenant'}))
    assert exc.value.detail == 'renewal_offer_not_owned'
    db, proposal, tenant = fixture(); db.lease_renewal_notification_outbox.docs[0]['status'] = 'failed'
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': ObjectId(tenant['_id'])}))
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': '0' * 64}, db, tenant))
    assert exc.value.detail == 'renewal_offer_not_released'


def test_missing_proposal_tenant_snapshot_fails_closed(monkeypatch):
    db, proposal, tenant = fixture(); proposal['tenant_id'] = ''
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': ObjectId(tenant['_id'])}))
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'accept', 'terms_digest': '0' * 64}, db, tenant))
    assert exc.value.detail == 'renewal_offer_tenant_snapshot_invalid'


def test_repeated_identical_response_is_idempotent_but_change_is_blocked(monkeypatch):
    db, proposal, tenant = fixture()
    monkeypatch.setattr(response, 'resolve_authenticated_tenant', lambda _user: async_value({'_id': ObjectId(tenant['_id'])}))
    digest = offer_digest(db, proposal, tenant)
    body = {'decision': 'accept', 'terms_digest': digest}
    assert run(response.respond_to_renewal(str(proposal['_id']), body, db, tenant))['idempotent'] is False
    assert run(response.respond_to_renewal(str(proposal['_id']), body, db, tenant))['idempotent'] is True
    with pytest.raises(HTTPException) as exc:
        run(response.respond_to_renewal(str(proposal['_id']), {'decision': 'decline', 'terms_digest': digest}, db, tenant))
    assert exc.value.detail == 'renewal_response_already_recorded'


def test_source_does_not_mutate_contract_occupancy_payment_or_signature():
    source = open('rental/lease_renewal_tenant_response_router.py', encoding='utf-8').read()
    for forbidden in ('rental_contracts.update', 'properties.update', 'rent_payments.update', 'signatures.update', 'force_activate'):
        assert forbidden not in source
