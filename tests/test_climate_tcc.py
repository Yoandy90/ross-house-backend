import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from aiosomecomfort.exceptions import APIError
from yarl import URL
from rental import climate_tcc as tcc, climate_provider as provider, climate_router as routes
from rental.climate_policy import validate_change

RESPONSE = {'success': True, 'deviceLive': True, 'communicationLost': False,
    'latestData': {'uiData': {'DisplayUnits': 'F', 'DispTemperature': 73,
        'IndoorHumidity': 39, 'IndoorHumiditySensorAvailable': True, 'IndoorHumiditySensorNotFault': True,
        'SystemSwitchPosition': 4, 'EquipmentOutputStatus': 1, 'HeatSetpoint': 68, 'CoolSetpoint': 74,
        'SwitchOffAllowed': True, 'SwitchHeatAllowed': True, 'SwitchCoolAllowed': True, 'SwitchAutoAllowed': True,
        'HeatLowerSetptLimit': 40, 'HeatUpperSetptLimit': 90,
        'CoolLowerSetptLimit': 50, 'CoolUpperSetptLimit': 99, 'Deadband': 3}}}


def test_normalization_and_limits():
    raw = tcc.normalize(RESPONSE)
    assert raw['indoorTemperature'] == 73 and raw['indoorHumidity'] == 39
    assert raw['units'] == 'Fahrenheit' and raw['isAlive'] is True
    assert raw['activity'] == 'heating'
    with pytest.raises(HTTPException):
        validate_change(raw, {'heatSetpoint': 73})
    for key in ('Deadband', 'HeatLowerSetptLimit'):
        response = deepcopy(RESPONSE); response['latestData']['uiData'].pop(key)
        with pytest.raises(HTTPException):
            validate_change(tcc.normalize(response), {'heatSetpoint': 69})


def test_missing_live_status_is_not_online():
    response = deepcopy(RESPONSE); response.pop('communicationLost')
    assert tcc.normalize(response)['isAlive'] is False
    response = deepcopy(RESPONSE); response['success'] = False
    with pytest.raises(HTTPException): tcc.normalize(response)


def test_tcc_encryption_independent_of_oauth(monkeypatch):
    monkeypatch.setenv('CLIMATE_TOKEN_KEY', Fernet.generate_key().decode())
    monkeypatch.delenv('CLIMATE_RESIDEO_CLIENT_ID', raising=False)
    assert provider.tcc_configured() and not provider.configured()
    secret = provider.seal({'password': 'private'})
    assert 'private' not in secret and provider.unseal(secret)['password'] == 'private'
    assert 'private' not in repr(routes.TCCConnect(username='owner', password='private'))


def test_provider_routing_and_legacy_default(monkeypatch):
    db = SimpleNamespace(climate_connections=SimpleNamespace(find_one=AsyncMock(return_value={'_id': 'a'})))
    assert asyncio.run(provider.connection_kind(db, 'a')) == 'first_alert'
    db.climate_connections.find_one.return_value = {'provider': 'tcc_us'}
    adapter = AsyncMock(return_value={'ok': True}); monkeypatch.setattr(tcc, 'device', adapter)
    binding = {'connection_id': 'a'}
    assert asyncio.run(provider.device(db, binding)) == {'ok': True}
    adapter.assert_awaited_once_with(db, binding)
    db.climate_connections.find_one.return_value = {'provider': 'unrecognized'}
    with pytest.raises(HTTPException): asyncio.run(provider.device(db, binding))


def test_single_atomic_command_and_no_retry(monkeypatch):
    client = SimpleNamespace(get_thermostat_data=AsyncMock(return_value=RESPONSE),
                             set_thermostat_settings=AsyncMock(side_effect=TimeoutError))
    @asynccontextmanager
    async def fake_account(*args): yield client
    monkeypatch.setattr(tcc, 'account', fake_account)
    values = validate_change(tcc.normalize(RESPONSE), {'mode': 'Cool', 'coolSetpoint': 75})
    with pytest.raises(TimeoutError):
        asyncio.run(tcc.change(None, {'connection_id':'a', 'provider_device_id':'123'}, values))
    client.set_thermostat_settings.assert_awaited_once_with('123', {
        'SystemSwitch': 3, 'HeatSetpoint': 68, 'CoolSetpoint': 75,
        'StatusCool': 2, 'StatusHeat': 2})


def test_heat_change_preserves_pair_and_expands_deadband(monkeypatch):
    response = deepcopy(RESPONSE)
    ui = response['latestData']['uiData']
    ui.update(SystemSwitchPosition=1, HeatSetpoint=71, CoolSetpoint=72, Deadband=3)
    client = SimpleNamespace(get_thermostat_data=AsyncMock(return_value=response),
                             set_thermostat_settings=AsyncMock())
    @asynccontextmanager
    async def fake_account(*args): yield client
    monkeypatch.setattr(tcc, 'account', fake_account)
    values = validate_change(tcc.normalize(response), {'heatSetpoint': 72})
    asyncio.run(tcc.change(None, {'connection_id':'a', 'provider_device_id':'123'}, values))
    client.set_thermostat_settings.assert_awaited_once_with('123', {
        'HeatSetpoint': 72, 'CoolSetpoint': 75, 'StatusCool': 2, 'StatusHeat': 2})


def test_provider_rejection_is_explicit_not_timeout(monkeypatch):
    response = deepcopy(RESPONSE)
    response['latestData']['uiData']['SystemSwitchPosition'] = 1
    client = SimpleNamespace(get_thermostat_data=AsyncMock(return_value=response),
                             set_thermostat_settings=AsyncMock(side_effect=APIError('rejected')))
    @asynccontextmanager
    async def fake_account(*args): yield client
    monkeypatch.setattr(tcc, 'account', fake_account)
    values = validate_change(tcc.normalize(response), {'heatSetpoint': 69})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tcc.change(None, {'connection_id':'a', 'provider_device_id':'123'}, values))
    assert exc.value.status_code == 409 and exc.value.detail == 'climate_provider_rejected'
    assert client.set_thermostat_settings.await_count == 1


def test_external_redirect_blocked():
    for destination in ('https://attacker.example/collect', 'http://mytotalconnectcomfort.com/portal'):
        params = SimpleNamespace(url=URL('https://mytotalconnectcomfort.com/portal'),
                                 response=SimpleNamespace(headers={'Location': destination}))
        with pytest.raises(HTTPException): asyncio.run(tcc.safe_redirect(None, None, params))


def test_tcc_setup_is_admin_only(monkeypatch):
    auth = AsyncMock(side_effect=HTTPException(403, 'forbidden'))
    monkeypatch.setattr(routes, 'auth_admin', auth)
    connect = AsyncMock(); monkeypatch.setattr(tcc, 'connect', connect)
    with pytest.raises(HTTPException):
        asyncio.run(routes.tcc_connect(routes.TCCConnect(username='owner', password='private'), None))
    connect.assert_not_awaited()


def test_timeout_clears_session_and_releases_account_lock(monkeypatch):
    monkeypatch.setenv('CLIMATE_TOKEN_KEY', Fernet.generate_key().decode())
    collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value={
        'credentials': provider.seal({'username':'owner','password':'private'})}), update_one=AsyncMock())
    @asynccontextmanager
    async def fake_session(*args):
        yield SimpleNamespace(login=AsyncMock()), None
    monkeypatch.setattr(tcc, 'session_client', fake_session)
    async def fail():
        async with tcc.account(SimpleNamespace(climate_connections=collection), 'a'):
            raise TimeoutError
    with pytest.raises(HTTPException): asyncio.run(fail())
    updates = [call.args[1] for call in collection.update_one.call_args_list]
    assert 'session' in updates[0]['$unset'] and 'retry_after' in updates[0]['$set']
    assert 'session_lock' in updates[-1]['$unset']
