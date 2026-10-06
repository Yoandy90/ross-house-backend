import asyncio
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import ValidationError
from rental.climate_policy import scope_for_contract, snapshot, validate_change
from rental import climate_provider as provider
from rental import climate_router as routes

RAW = {'isAlive': True, 'units': 'Fahrenheit', 'allowedModes': ['Off', 'Heat', 'Cool', 'Auto', 'EmergencyHeat'],
       'minHeatSetpoint': 50, 'maxHeatSetpoint': 90, 'minCoolSetpoint': 50, 'maxCoolSetpoint': 90, 'deadband': 3,
       'changeableValues': {'mode': 'Auto', 'heatSetpoint': 68, 'coolSetpoint': 74, 'autoChangeoverActive': True}}


def run(coro):
    return asyncio.run(coro)


def test_preserves_provider_fields_and_hold():
    changed = validate_change(RAW, {'heatSetpoint': 70})
    assert changed['autoChangeoverActive'] is True
    assert changed['coolSetpoint'] == 74
    assert changed['thermostatSetpointStatus'] == 'PermanentHold'
    assert RAW['changeableValues']['heatSetpoint'] == 68


@pytest.mark.parametrize('command', [{'heatSetpoint': float('nan')}, {'coolSetpoint': float('inf')}, {'heatSetpoint': 100}, {'heatSetpoint': 73}, {'mode': 'EmergencyHeat'}, {'fan': 'On'}, {}])
def test_invalid_commands(command):
    with pytest.raises(HTTPException):
        validate_change(RAW, command)


def test_offline_and_unknown_units_blocked():
    for change in [{'isAlive': False}, {'units': 'unknown'}, {'isUpgrading': True}]:
        with pytest.raises(HTTPException):
            validate_change({**RAW, **change}, {'mode': 'Off'})


def contract(**kwargs):
    return dict(status='active', property_id='home', unit_id='A',
                start_date=str(date.today() - timedelta(days=1)), end_date=str(date.today() + timedelta(days=1)), **kwargs)


def test_unit_scope_is_exact():
    assert scope_for_contract(contract()) == {'property_id': 'home', 'unit_id': 'A'}
    c = contract(); c.pop('unit_id')
    assert scope_for_contract(c)['unit_id'] == ''


@pytest.mark.parametrize('field,value', [('status', 'ended'), ('end_date', '2000-01-01'), ('start_date', '2099-01-01'), ('end_date', None), ('property_id', '')])
def test_invalid_contract_denies_access(field, value):
    c = contract(); c[field] = value
    with pytest.raises(HTTPException):
        scope_for_contract(c)


def test_payload_cannot_override_identity_or_use_nonfinite():
    for payload in [{'property_id':'other'}, {'heatSetpoint':float('nan')}, {'tenant_id':'someone'}]:
        with pytest.raises(ValidationError):
            routes.Command(request_id=uuid4(), **payload)


def test_tenant_cannot_command_another_unit(monkeypatch):
    collection = SimpleNamespace(find_one=AsyncMock(return_value=None))
    monkeypatch.setattr(routes, 'get_db', lambda: SimpleNamespace(climate_bindings=collection))
    monkeypatch.setattr(routes, 'tenant_scope', AsyncMock(return_value=({'id':'tenant'}, {'property_id':'own', 'unit_id':'A'})))
    command = AsyncMock(); monkeypatch.setattr(routes, 'issue_command', command)
    with pytest.raises(HTTPException) as exc:
        run(routes.tenant_command('other-unit', routes.Command(request_id=uuid4(), mode='Off'), None))
    assert exc.value.status_code == 404
    collection.find_one.assert_awaited_once_with({'_id':'other-unit','property_id':'own','unit_id':'A'})
    command.assert_not_awaited()


def config(monkeypatch):
    for name, value in {'CLIMATE_RESIDEO_CLIENT_ID':'test', 'CLIMATE_RESIDEO_CLIENT_SECRET':'test-secret',
                        'CLIMATE_TOKEN_KEY':Fernet.generate_key().decode(), 'CLIMATE_REDIRECT_URI':'https://example.test/admin/climatizacion'}.items():
        monkeypatch.setenv(name, value)


def test_tokens_are_encrypted(monkeypatch):
    config(monkeypatch)
    tokens = {'access_token':'sensitive-access', 'refresh_token':'sensitive-refresh'}
    encrypted = provider.seal(tokens)
    assert 'sensitive' not in encrypted
    assert provider.unseal(encrypted) == tokens


def test_oauth_replay_or_wrong_owner_does_not_exchange(monkeypatch):
    config(monkeypatch)
    states = SimpleNamespace(find_one_and_delete=AsyncMock(return_value=None))
    exchange = AsyncMock(); monkeypatch.setattr(provider, 'token_request', exchange)
    with pytest.raises(HTTPException):
        run(provider.finish_oauth(SimpleNamespace(climate_oauth_states=states), 'other-admin', 'state', 'code'))
    query = states.find_one_and_delete.call_args.args[0]
    assert query['actor'] == 'other-admin' and 'expires_at' in query
    exchange.assert_not_awaited()


def test_commands_disabled_by_default(monkeypatch):
    monkeypatch.delenv('CLIMATE_ENABLED', raising=False)
    with pytest.raises(HTTPException):
        run(routes.issue_command({}, {}, routes.Command(request_id=uuid4(), mode='Off')))


def command_db(previous=None):
    return SimpleNamespace(climate_commands=SimpleNamespace(find_one=AsyncMock(return_value=previous), insert_one=AsyncMock(), update_one=AsyncMock()),
                           climate_bindings=SimpleNamespace(find_one_and_update=AsyncMock(return_value={'_id':'device'}), update_one=AsyncMock()))


def test_timeout_is_unknown_and_never_retried(monkeypatch):
    monkeypatch.setattr(routes, '_rate_limit_physical_command', AsyncMock())
    monkeypatch.setenv('CLIMATE_ENABLED','true'); monkeypatch.setenv('CLIMATE_CONTROL_ENABLED','true')
    db=command_db(); monkeypatch.setattr(routes,'get_db',lambda:db)
    monkeypatch.setattr(provider,'device',AsyncMock(return_value=deepcopy(RAW)))
    change=AsyncMock(side_effect=HTTPException(503,'unavailable'));monkeypatch.setattr(provider,'change',change)
    result=run(routes.issue_command({'_id':'device'}, {'id':'admin'},routes.Command(request_id=uuid4(),mode='Off')))
    assert result == {'status':'unknown'}
    assert change.await_count == 1
    assert db.climate_commands.update_one.call_args.args[1]['$set']['status']=='unknown'


def test_duplicate_request_does_not_reissue(monkeypatch):
    monkeypatch.setattr(routes, '_rate_limit_physical_command', AsyncMock())
    import hashlib, json
    monkeypatch.setenv('CLIMATE_ENABLED','true');monkeypatch.setenv('CLIMATE_CONTROL_ENABLED','true')
    digest=hashlib.sha256(json.dumps({'mode':'Off'},sort_keys=True).encode()).hexdigest()
    db=command_db({'device_id':'device','digest':digest,'status':'confirmed'});monkeypatch.setattr(routes,'get_db',lambda:db)
    change=AsyncMock();monkeypatch.setattr(provider,'change',change)
    assert run(routes.issue_command({'_id':'device'},{'id':'admin'},routes.Command(request_id=uuid4(),mode='Off'))) == {'status':'confirmed'}
    change.assert_not_awaited()
    db.climate_bindings.find_one_and_update.assert_not_awaited()
