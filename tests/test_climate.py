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


@pytest.mark.parametrize('command', [{'heatSetpoint': float('nan')}, {'coolSetpoint': float('inf')}, {'heatSetpoint': 100}, {'heatSetpoint': 73}, {'fan': 'On'}, {}])
def test_invalid_commands(command):
    with pytest.raises(HTTPException):
        validate_change(RAW, command)


def test_emergency_heat_is_capability_gated():
    # This fixture explicitly advertises EmergencyHeat, so the command is valid.
    accepted = validate_change(RAW, {'mode': 'EmergencyHeat'})
    assert accepted['mode'] == 'EmergencyHeat'

    # Never expose/accept EmergencyHeat on a device that did not advertise it.
    without_emergency = deepcopy(RAW)
    without_emergency['allowedModes'] = ['Off', 'Heat', 'Cool', 'Auto']
    with pytest.raises(HTTPException):
        validate_change(without_emergency, {'mode': 'EmergencyHeat'})


def test_offline_and_unknown_units_blocked():
    for change in [{'isAlive': False}, {'units': 'unknown'}, {'isUpgrading': True}]:
        with pytest.raises(HTTPException):
            validate_change({**RAW, **change}, {'mode': 'Off'})


@pytest.mark.parametrize('change', [{'isAlive': False}, {'isAlive': None}, {'isUpgrading': True}])
def test_offline_snapshots_do_not_expose_cached_live_values(change):
    state = snapshot({**RAW, 'indoorTemperature': 74, 'indoorHumidity': 40,
                      'holdStatus': 'permanent', 'fan': {'mode': 'On', 'running': True}, **change})
    assert state['online'] is False
    assert state['units'] == 'Fahrenheit'
    assert 'Heat' in state['modes']
    for field in ('temperature', 'humidity', 'mode', 'heatSetpoint', 'coolSetpoint',
                  'fanMode', 'fanRunning', 'holdStatus', 'activity'):
        assert state[field] is None


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
    change=AsyncMock(side_effect=HTTPException(503,'climate_provider_unavailable'));monkeypatch.setattr(provider,'change',change)
    result=run(routes.issue_command({'_id':'device'}, {'id':'admin'},routes.Command(request_id=uuid4(),mode='Off')))
    assert result == {'status':'unknown', 'reason':'climate_provider_unavailable'}
    assert change.await_count == 1
    saved = db.climate_commands.update_one.call_args.args[1]['$set']
    assert saved['status']=='unknown'
    assert saved['failure_reason']=='climate_provider_unavailable'
    assert saved['failure_http_status']==503
    assert saved.get('finished_at') is not None


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



def test_command_failure_detail_is_sanitized(monkeypatch):
    monkeypatch.setattr(routes, '_rate_limit_physical_command', AsyncMock())
    monkeypatch.setenv('CLIMATE_ENABLED','true'); monkeypatch.setenv('CLIMATE_CONTROL_ENABLED','true')
    db=command_db(); monkeypatch.setattr(routes,'get_db',lambda:db)
    monkeypatch.setattr(provider,'device',AsyncMock(return_value=deepcopy(RAW)))
    secret_detail='upstream cookie=super-secret'
    monkeypatch.setattr(provider,'change',AsyncMock(side_effect=HTTPException(503,secret_detail)))
    result=run(routes.issue_command({'_id':'device'}, {'id':'admin'},routes.Command(request_id=uuid4(),mode='Off')))
    assert result == {'status':'unknown', 'reason':'climate_command_unconfirmed'}
    saved = db.climate_commands.update_one.call_args.args[1]['$set']
    assert saved['failure_reason']=='climate_command_unconfirmed'
    assert secret_detail not in repr(saved)


def test_duplicate_unknown_command_returns_safe_reason_without_reissue(monkeypatch):
    monkeypatch.setattr(routes, '_rate_limit_physical_command', AsyncMock())
    import hashlib, json
    monkeypatch.setenv('CLIMATE_ENABLED','true');monkeypatch.setenv('CLIMATE_CONTROL_ENABLED','true')
    digest=hashlib.sha256(json.dumps({'mode':'Off'},sort_keys=True).encode()).hexdigest()
    previous={'device_id':'device','digest':digest,'status':'unknown','failure_reason':'climate_provider_rejected'}
    db=command_db(previous);monkeypatch.setattr(routes,'get_db',lambda:db)
    change=AsyncMock();monkeypatch.setattr(provider,'change',change)
    result=run(routes.issue_command({'_id':'device'},{'id':'admin'},routes.Command(request_id=uuid4(),mode='Off')))
    assert result == {'status':'unknown', 'reason':'climate_provider_rejected'}
    change.assert_not_awaited()



def test_connection_detail_exposes_only_safe_diagnostics():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    detail = routes._connection_detail({
        '_id': 'conn',
        'provider': 'tcc_us',
        'session': 'encrypted-cookie-material',
        'credentials': 'encrypted-credentials',
        'created_by': 'admin-id',
        'session_until': now,
        'last_success_at': now,
        'last_failure_at': now,
        'last_failure_kind': 'transport',
        'retry_after': now,
    })
    assert detail['id'] == 'conn'
    assert detail['provider'] == 'tcc_us'
    assert detail['session_cached'] is True
    assert detail['last_failure_kind'] == 'transport'
    assert detail['last_success_at'] == now.isoformat()
    assert detail['retry_after'] == now.isoformat()
    assert 'session' not in detail
    assert 'credentials' not in detail
    assert 'created_by' not in detail


import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rental import climate_monitor, climate_router


def test_history_unit_recovery_is_device_scoped_and_does_not_return_live_values():
    for units in ('Fahrenheit', 'Celsius', None):
        find = AsyncMock(return_value={'units': units} if units else None)
        db = SimpleNamespace(climate_readings=SimpleNamespace(find_one=find))
        assert asyncio.run(climate_monitor.historical_units(db, 'device-a')) == units
        args, kwargs = find.call_args
        assert args[0] == {'device_id': 'device-a', 'online': True,
                           'units': {'$in': ['Fahrenheit', 'Celsius']}}
        assert args[1] == {'units': 1}
        assert kwargs['sort'] == [('observed_at', -1)]


def test_unavailable_device_keeps_history_units_without_claiming_live_reading(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(climate_router, 'enabled', lambda: True)
    monkeypatch.setattr(climate_router, 'control', lambda: False)
    monkeypatch.setattr(climate_router, 'get_db', lambda: object())
    monkeypatch.setattr(climate_router.provider, 'device', AsyncMock(side_effect=HTTPException(503)))
    monkeypatch.setattr(climate_monitor, 'record_unavailable', AsyncMock())
    monkeypatch.setattr(climate_monitor, 'historical_units', AsyncMock(return_value='Fahrenheit'))
    result = asyncio.run(climate_router.read_binding(
        {'_id': 'device-a', 'name': 'Test', 'property_id': 'property-a'}))
    assert result['online'] is False
    assert result['units'] == 'Fahrenheit'
    assert result['error'] == 'climate_device_unavailable'
    for field in ('temperature', 'humidity', 'observed_at', 'mode', 'heatSetpoint'):
        assert field not in result

@pytest.mark.parametrize('kind,mode', [('fan', 'On'), ('hold', 'schedule')])
@pytest.mark.parametrize('change', [{'isAlive': False}, {'isAlive': None}, {'isUpgrading': True}])
def test_feature_commands_deny_offline_before_insert_or_write(monkeypatch, kind, mode, change):
    monkeypatch.setattr(routes, '_rate_limit_physical_command', AsyncMock())
    monkeypatch.setenv('CLIMATE_ENABLED', 'true')
    monkeypatch.setenv('CLIMATE_CONTROL_ENABLED', 'true')
    db = command_db()
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    raw = {**deepcopy(RAW), 'holdStatus': 'permanent',
           'fan': {'supported': True, 'allowedModes': ['Auto', 'On'], 'mode': 'Auto'}, **change}
    monkeypatch.setattr(provider, 'device', AsyncMock(return_value=raw))
    fan, hold = AsyncMock(), AsyncMock()
    monkeypatch.setattr(provider, 'change_fan', fan)
    monkeypatch.setattr(provider, 'change_hold', hold)
    with pytest.raises(HTTPException) as exc:
        run(routes.issue_feature_command({'_id': 'device'}, {'id': 'admin'}, uuid4(), kind, {'mode': mode}))
    assert exc.value.detail == 'climate_device_offline'
    fan.assert_not_awaited()
    hold.assert_not_awaited()
    db.climate_commands.insert_one.assert_not_awaited()
    db.climate_bindings.update_one.assert_awaited_once()


def test_offline_readback_does_not_confirm_temperature_command(monkeypatch):
    monkeypatch.setattr(routes, '_rate_limit_physical_command', AsyncMock())
    monkeypatch.setenv('CLIMATE_ENABLED', 'true')
    monkeypatch.setenv('CLIMATE_CONTROL_ENABLED', 'true')
    db = command_db()
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    offline = {**deepcopy(RAW), 'isAlive': False, 'changeableValues': {'mode': 'Off'}}
    monkeypatch.setattr(provider, 'device', AsyncMock(side_effect=[deepcopy(RAW), offline]))
    change = AsyncMock()
    monkeypatch.setattr(provider, 'change', change)
    monkeypatch.setattr(routes.climate_monitor, 'record_snapshot', AsyncMock())
    result = run(routes.issue_command({'_id': 'device'}, {'id': 'admin'},
                                      routes.Command(request_id=uuid4(), mode='Off')))
    assert result == {'status': 'pending'}
    assert change.await_count == 1



@pytest.mark.parametrize('exists', [True, False])
def test_tenant_hold_is_scoped_to_active_home(monkeypatch, exists):
    binding = {'_id': 'device', 'property_id': 'home', 'unit_id': 'A'}
    find = AsyncMock(return_value=binding if exists else None)
    monkeypatch.setattr(routes, 'get_db', lambda: SimpleNamespace(
        climate_bindings=SimpleNamespace(find_one=find)))
    monkeypatch.setattr(routes, 'tenant_scope', AsyncMock(
        return_value=({'id': 'tenant'}, {'property_id': 'home', 'unit_id': 'A'})))
    issue = AsyncMock(return_value={'status': 'confirmed'})
    monkeypatch.setattr(routes, 'issue_feature_command', issue)
    body = routes.HoldCommand(request_id=uuid4(), mode='schedule')
    if exists:
        assert run(routes.tenant_hold_command('device', body, None)) == {'status': 'confirmed'}
        issue.assert_awaited_once_with(binding, {'id': 'tenant'}, body.request_id, 'hold', {'mode': 'schedule'})
    else:
        with pytest.raises(HTTPException) as exc:
            run(routes.tenant_hold_command('device', body, None))
        assert exc.value.status_code == 404
        issue.assert_not_awaited()
    find.assert_awaited_once_with({'_id': 'device', 'property_id': 'home', 'unit_id': 'A'})


def test_climate_notifications_use_exact_contract_unit_scope(monkeypatch):
    from rental import climate_notifications as notifications
    from unittest.mock import Mock
    class Cursor:
        def __init__(self, rows): self.rows = rows
        def __aiter__(self):
            async def iterate():
                for row in self.rows: yield row
            return iterate()
    candidates = [{'id': 'account-a'}, {'id': 'account-b'}, {'id': 'expired'}]
    monkeypatch.setattr(notifications, 'resolve', AsyncMock(return_value=candidates))
    monkeypatch.delenv('CLIMATE_ALERT_NOTIFY_ADMINS', raising=False)
    tenants = [{'_id': 'tenant-a', 'app_user_id': 'account-a'},
               {'_id': 'tenant-b', 'app_user_id': 'account-b'},
               {'_id': 'tenant-old', 'app_user_id': 'expired'}]
    contracts = [
        {**contract(), 'tenant_id': 'tenant-a'},
        {**contract(), 'tenant_id': 'tenant-b', 'unit_id': 'B'},
        {**contract(), 'tenant_id': 'tenant-old', 'end_date': '2000-01-01'},
    ]
    db = SimpleNamespace(tenants=SimpleNamespace(find=Mock(return_value=Cursor(tenants))),
                         rental_contracts=SimpleNamespace(find=Mock(return_value=Cursor(contracts))))
    result = run(notifications._recipients(db, {'property_id': 'home', 'unit_id': 'A'}))
    assert result == [{'id': 'account-a'}]
    result = run(notifications._recipients(db, {'property_id': 'home', 'unit_id': ''}))
    assert result == []


# Shared cache tests use a small atomic in-memory collection; the load callback
# represents the public read path, never a thermostat write.
class ReadCacheCollection:
    def __init__(self):
        self.rows = {}
    async def find_one(self, query):
        return deepcopy(self.rows.get(query['_id']))
    async def update_one(self, query, update, upsert=False):
        key = query['_id']
        if key not in self.rows:
            if not upsert: return
            self.rows[key] = {'_id': key, **deepcopy(update.get('$setOnInsert', {}))}
        row = self.rows[key]
        if 'lock_token' in query and row.get('lock_token') != query['lock_token']: return
        row.update(deepcopy(update.get('$set', {})))
        for field in update.get('$unset', {}): row.pop(field, None)
    async def find_one_and_update(self, query, update, **kwargs):
        row = self.rows.get(query['_id'])
        if not row or row['expires_at'] > query['expires_at']['$lte'] or row['lock_until'] > query['lock_until']['$lte']:
            return None
        row.update(deepcopy(update['$set']))
        return deepcopy(row)
    async def delete_one(self, query):
        self.rows.pop(query['_id'], None)


def read_cache_db():
    return SimpleNamespace(climate_read_cache=ReadCacheCollection(),
                           climate_read_metrics=SimpleNamespace(update_one=AsyncMock()))


def test_shared_tcc_read_preserves_timestamp_and_excludes_nonpublic_data():
    from rental import climate_read_cache as cache
    db = read_cache_db()
    binding = {'_id': 'device', 'provider': 'tcc_us', 'connection_id': 'connection',
               'property_id': 'home', 'unit_id': 'A', 'name': 'Test'}
    load = AsyncMock(return_value={'id': 'device', 'online': True, 'temperature': 75,
                                  'observed_at': '2026-11-08T00:00:00+00:00',
                                  'credentials': 'secret', 'request_id': 'old', 'control_enabled': True})
    async def scenario():
        first = await cache.shared_read(db, binding, load)
        second = await cache.shared_read(db, binding, load)
        assert first == second
        assert second['observed_at'] == '2026-11-08T00:00:00+00:00'
        assert not {'credentials', 'request_id', 'control_enabled'} & set(second)
        second['temperature'] = 99
        assert (await cache.shared_read(db, binding, load))['temperature'] == 75
        assert load.await_count == 1
        await cache.invalidate(db, binding)
        await cache.shared_read(db, binding, load)
        assert load.await_count == 2
    run(scenario())


def test_shared_tcc_read_expiration_and_negative_cache(monkeypatch):
    from datetime import datetime, timezone
    from rental import climate_read_cache as cache
    clock = [datetime(2026, 11, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(cache, 'now', lambda: clock[0])
    db = read_cache_db()
    binding = {'_id': 'device'}
    load = AsyncMock(return_value={'online': False, 'error': 'climate_device_unavailable'})
    async def scenario():
        await cache.shared_read(db, binding, load)
        clock[0] += timedelta(seconds=14)
        await cache.shared_read(db, binding, load)
        assert load.await_count == 1
        clock[0] += timedelta(seconds=2)
        await cache.shared_read(db, binding, load)
        assert load.await_count == 2
        load.return_value = {'online': True, 'temperature': 75}
        clock[0] += timedelta(seconds=16)
        await cache.shared_read(db, binding, load)
        clock[0] += timedelta(seconds=59)
        await cache.shared_read(db, binding, load)
        assert load.await_count == 3
        clock[0] += timedelta(seconds=2)
        await cache.shared_read(db, binding, load)
        assert load.await_count == 4
    run(scenario())


def test_shared_tcc_read_coalesces_concurrent_workers(monkeypatch):
    from rental import climate_read_cache as cache
    db = read_cache_db()
    binding = {'_id': 'device'}
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def load():
            calls.append(1)
            entered.set()
            await release.wait()
            return {'online': True, 'temperature': 75}
        first = asyncio.create_task(cache.shared_read(db, binding, load))
        await entered.wait()
        async def join_sleep(seconds):
            release.set()
            await first
        monkeypatch.setattr(cache.asyncio, 'sleep', join_sleep)
        second = await cache.shared_read(db, binding, load)
        assert second['temperature'] == 75
        assert len(calls) == 1
    run(scenario())


def test_cache_identity_changes_with_home_and_connection():
    from rental import climate_read_cache as cache
    binding = {'_id': 'device', 'connection_id': 'a', 'property_id': 'home', 'unit_id': 'A'}
    for patch in ({'connection_id': 'b'}, {'property_id': 'other'}, {'unit_id': 'B'}):
        assert cache.key_for(binding) != cache.key_for({**binding, **patch})


def test_cached_route_refreshes_command_identity_and_current_flags(monkeypatch):
    from rental import climate_read_cache as cache
    binding = {'_id': 'device', 'provider': 'tcc_us'}
    monkeypatch.setattr(routes, 'enabled', lambda: True)
    monkeypatch.setattr(routes, 'control', lambda: False)
    monkeypatch.setattr(routes, 'get_db', lambda: object())
    monkeypatch.setattr(cache, 'shared_read', AsyncMock(return_value={
        'id': 'device', 'online': True, 'observed_at': 'original'}))
    a, b = run(routes.read_binding(binding)), run(routes.read_binding(binding))
    assert a['observed_at'] == b['observed_at'] == 'original'
    assert a['request_id'] != b['request_id']
    assert a['control_enabled'] is False


def test_cache_invalidation_prevents_inflight_read_repopulating_old_state():
    from rental import climate_read_cache as cache
    db = read_cache_db()
    binding = {'_id': 'device', 'provider': 'tcc_us'}
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def old_read():
            entered.set()
            await release.wait()
            return {'online': True, 'temperature': 70}
        pending = asyncio.create_task(cache.shared_read(db, binding, old_read))
        await entered.wait()
        await cache.invalidate(db, binding)
        fresh = AsyncMock(return_value={'online': True, 'temperature': 75})
        await cache.shared_read(db, binding, fresh)
        release.set()
        await pending
        assert (await cache.shared_read(db, binding, fresh))['temperature'] == 75
        assert fresh.await_count == 1
    run(scenario())


def test_shared_read_releases_lock_after_unexpected_failure():
    from rental import climate_read_cache as cache
    db = read_cache_db()
    binding = {'_id': 'device'}
    load = AsyncMock(side_effect=[RuntimeError('safe-test'), {'online': True, 'temperature': 75}])
    async def scenario():
        with pytest.raises(RuntimeError):
            await cache.shared_read(db, binding, load)
        assert (await cache.shared_read(db, binding, load))['temperature'] == 75
        assert load.await_count == 2
    run(scenario())

