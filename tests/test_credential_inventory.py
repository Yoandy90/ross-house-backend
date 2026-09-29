import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rental import owner_router
from rental.credential_inventory import credential_inventory_pipeline


def row(group='owners', **changes):
    return {'_id': group, 'accounts': 4, 'stored_password_accounts': 2,
            'active_stored_password_accounts': 1, 'malformed_fields': 1, **changes}


@pytest.fixture
def endpoint(monkeypatch):
    cursor = SimpleNamespace(to_list=AsyncMock(return_value=[row()]))
    users = SimpleNamespace(aggregate=Mock(return_value=cursor))
    auth = AsyncMock(return_value={'_id': 'admin'})
    monkeypatch.setattr(owner_router, 'get_db', lambda: SimpleNamespace(app_users=users))
    monkeypatch.setattr(owner_router, 'auth_admin', auth)
    app = FastAPI()
    app.include_router(owner_router.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, users=users, cursor=cursor, auth=auth)


def test_summary_only_returns_counts_and_explicit_limits(endpoint):
    endpoint.cursor.to_list.return_value = [row(_temp_password='NEVER_RETURN', email='private@example.test', password_hash='SECRET_HASH')]
    response = endpoint.client.get('/admin/operations/legacy-credential-summary')
    assert response.status_code == 200
    body = response.json()
    assert body['totals']['accounts'] == 4
    assert body['groups']['owners']['stored_password_accounts'] == 2
    assert body['groups']['investors']['accounts'] == 0
    assert body['requires_review'] is True
    assert body['password_strength_assessed'] is False
    assert body['credential_rotation_performed'] is False
    for secret in ['NEVER_RETURN', 'private@example.test', 'SECRET_HASH', '_temp_password']:
        assert secret not in response.text
    endpoint.users.aggregate.assert_called_once_with(credential_inventory_pipeline(), maxTimeMS=5000, allowDiskUse=False)
    endpoint.cursor.to_list.assert_awaited_once_with(length=4)


def test_counts_are_separated_by_account_group(endpoint):
    endpoint.cursor.to_list.return_value = [row('owners'), row('investors'), row('other')]
    body = endpoint.client.get('/admin/operations/legacy-credential-summary').json()
    assert body['totals'] == {'accounts': 12, 'stored_password_accounts': 6, 'active_stored_password_accounts': 3, 'malformed_fields': 3}


def test_empty_database_is_not_a_password_strength_pass(endpoint):
    endpoint.cursor.to_list.return_value = []
    body = endpoint.client.get('/admin/operations/legacy-credential-summary').json()
    assert body['totals']['accounts'] == 0
    assert body['requires_review'] is False
    assert body['password_strength_assessed'] is False


@pytest.mark.parametrize('rows', [None, [None], [row('unexpected')], [row(), row()],
    [row(accounts=True)], [row(accounts=-1)], [row(accounts='4')], [row(accounts=0)],
    [row(active_stored_password_accounts=3)], [row(stored_password_accounts=None)]])
def test_incomplete_or_invalid_aggregation_fails_closed(endpoint, rows):
    endpoint.cursor.to_list.return_value = rows
    response = endpoint.client.get('/admin/operations/legacy-credential-summary')
    assert response.status_code == 503
    assert 'totals' not in response.json()


def test_database_failure_does_not_leak_details_in_response_or_logs(endpoint, caplog):
    endpoint.cursor.to_list.side_effect = RuntimeError('PRIVATE_DATABASE_DIAGNOSTIC')
    response = endpoint.client.get('/admin/operations/legacy-credential-summary')
    assert response.status_code == 503
    assert 'PRIVATE_DATABASE_DIAGNOSTIC' not in response.text
    assert 'PRIVATE_DATABASE_DIAGNOSTIC' not in caplog.text


@pytest.mark.parametrize('status', [401, 403])
def test_authentication_is_required_before_aggregation(endpoint, status):
    endpoint.auth.side_effect = HTTPException(status_code=status, detail='Unauthorized')
    assert endpoint.client.get('/admin/operations/legacy-credential-summary').status_code == status
    endpoint.users.aggregate.assert_not_called()


def test_pipeline_projects_only_flags_before_grouping_and_has_no_write_stages():
    pipeline = credential_inventory_pipeline()
    assert [list(stage) for stage in pipeline] == [['$project'], ['$group']]
    projection = pipeline[0]['$project']
    assert set(projection) == {'_id', 'account_group', 'has_stored_password', 'malformed_field', 'active_account'}
    assert projection['_id'] == 0
    # The string-length operation must be guarded so malformed fields cannot break the scan.
    condition = projection['has_stored_password']['$cond']
    assert condition[0] == {'$eq': [{'$type': '$_temp_password'}, 'string']}
    assert condition[2] is False
    assert projection['malformed_field'] == {'$not': [{'$in': [{'$type': '$_temp_password'}, ['missing', 'null', 'string']]}]}
    grouping = pipeline[1]['$group']
    assert grouping['_id'] == '$account_group'
    for key, accumulator in grouping.items():
        if key != '_id':
            assert list(accumulator) == ['$sum']
    serialized = json.dumps(pipeline)
    for prohibited in ['password_hash', '$push', '$addToSet', '$out', '$merge']:
        assert prohibited not in serialized
