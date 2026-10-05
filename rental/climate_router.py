"""Property-scoped climate integration. Disabled until explicitly configured."""
import hashlib
import json
import os
from datetime import timedelta
from uuid import UUID, uuid4
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pymongo.errors import DuplicateKeyError
from .shared import auth_admin, auth_marketplace, get_db
from .tenant_integrity import resolve_authenticated_tenant, find_active_contract_for_tenant
from . import climate_provider as provider
from .climate_policy import scope_for_contract, snapshot, validate_change
from . import climate_monitor

router = APIRouter(tags=['climate'])

async def ensure_indexes(db):
    await db.climate_oauth_states.create_index('expires_at', expireAfterSeconds=0)
    await db.climate_bindings.create_index([('property_id', 1), ('unit_id', 1)])
    await db.climate_commands.create_index([('device_id', 1), ('created_at', -1)])
    await climate_monitor.ensure_indexes(db)



def enabled():
    return os.getenv('CLIMATE_ENABLED') == 'true'


def control():
    return enabled() and os.getenv('CLIMATE_CONTROL_ENABLED') == 'true'


def require_enabled():
    if not enabled():
        raise HTTPException(503, 'climate_not_enabled')


def actor_id(user):
    value = str(user.get('_id') or user.get('id') or '')
    if not value:
        raise HTTPException(401, 'climate_identity_required')
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class OAuthComplete(StrictModel):
    state: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=1, max_length=4096)


class TCCConnect(StrictModel):
    username: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=512)


class Binding(StrictModel):
    connection_id: str = Field(min_length=1, max_length=100)
    location_id: str = Field(min_length=1, max_length=100)
    provider_device_id: str = Field(min_length=1, max_length=150)
    property_id: str
    unit_id: str = ''
    name: str = Field(min_length=1, max_length=80)


class Command(StrictModel):
    request_id: UUID
    mode: str | None = None
    heatSetpoint: float | None = None
    coolSetpoint: float | None = None


class FanCommand(StrictModel):
    request_id: UUID
    mode: str = Field(min_length=2, max_length=30)


class HoldCommand(StrictModel):
    request_id: UUID
    mode: str = Field(min_length=3, max_length=30)


class SchedulePeriod(StrictModel):
    days: list[int] = Field(min_length=1, max_length=7)
    time: str = Field(min_length=5, max_length=5)
    mode: str = Field(min_length=3, max_length=10)
    heatSetpoint: float | None = None
    coolSetpoint: float | None = None


class ScheduleBody(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    timezone: str = Field(default='America/Chicago', min_length=1, max_length=80)
    periods: list[SchedulePeriod] = Field(min_length=1, max_length=56)


class ScheduleRun(StrictModel):
    period_index: int = Field(ge=0, le=55)


async def tenant_scope(request):
    user = await auth_marketplace(request)
    if user.get('role') != 'tenant':
        raise HTTPException(403, 'climate_tenant_required')
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        raise HTTPException(403, 'climate_no_active_home')
    contract = await find_active_contract_for_tenant(tenant)
    return user, scope_for_contract(contract)


async def read_binding(binding):
    result = {'id': binding['_id'], 'request_id': str(uuid4()), 'name': binding['name'],
              'property_id': binding['property_id'], 'unit_id': binding.get('unit_id', ''),
              'online': False, 'control_enabled': control()}
    if enabled():
        try:
            state = snapshot(await provider.device(get_db(), binding))
            result.update(state)
            result['observed_at'] = provider.now().isoformat()
            await climate_monitor.record_snapshot(get_db(), binding, state, source='read')
        except HTTPException:
            result['error'] = 'climate_device_unavailable'
            await climate_monitor.record_unavailable(get_db(), binding, source='read')
    return result


@router.get('/admin/climate')
async def admin_list(request: Request):
    await auth_admin(request)
    db = get_db()
    bindings = await db.climate_bindings.find({}).limit(200).to_list(200)
    connections = await db.climate_connections.find({}, {'_id': 1, 'provider': 1}).limit(100).to_list(100)
    properties = await db.properties.find({}, {'address': 1}).limit(500).to_list(500)
    units = await db.property_units.find({}, {'property_id': 1, 'unit_name': 1}).limit(2000).to_list(2000)
    return {'enabled': enabled(), 'configured': provider.configured(), 'control_enabled': control(),
            'monitor_enabled': climate_monitor.monitor_enabled(),
            'history_bucket_minutes': climate_monitor._sample_minutes(),
            'providers': {'first_alert': provider.configured(), 'tcc_us': provider.tcc_configured()},
            'connection_details': [{'id': str(c['_id']), 'provider': c.get('provider', 'first_alert')} for c in connections],
            'devices': [await read_binding(b) for b in bindings],
            'connections': [str(c['_id']) for c in connections],
            'properties': [{'id': str(p['_id']), 'name': str(p.get('address') or p['_id'])} for p in properties],
            'units': [{'id': str(u['_id']), 'property_id': str(u.get('property_id', '')), 'name': u.get('unit_name', '')} for u in units]}


@router.get('/tenant/climate')
async def tenant_list(request: Request):
    _, scope = await tenant_scope(request)
    bindings = await get_db().climate_bindings.find(scope).limit(20).to_list(20)
    return {'enabled': enabled(), 'monitor_enabled': climate_monitor.monitor_enabled(),
            'history_bucket_minutes': climate_monitor._sample_minutes(),
            'devices': [await read_binding(b) for b in bindings]}


@router.post('/admin/climate/oauth/start')
async def oauth_start(request: Request):
    user = await auth_admin(request)
    require_enabled()
    return {'url': await provider.start_oauth(get_db(), actor_id(user))}


@router.post('/admin/climate/oauth/complete')
async def oauth_complete(body: OAuthComplete, request: Request):
    user = await auth_admin(request)
    require_enabled()
    return {'connection_id': await provider.finish_oauth(get_db(), actor_id(user), body.state, body.code)}


@router.post('/admin/climate/tcc/connect')
async def tcc_connect(body: TCCConnect, request: Request):
    user = await auth_admin(request)
    require_enabled()
    from . import climate_tcc
    return {'connection_id': await climate_tcc.connect(
        get_db(), actor_id(user), body.username.strip(), body.password.get_secret_value())}


@router.get('/admin/climate/discover/{connection_id}')
async def discover(connection_id: str, request: Request):
    await auth_admin(request)
    require_enabled()
    return {'devices': await provider.discover(get_db(), connection_id)}


@router.post('/admin/climate/bindings')
async def bind(body: Binding, request: Request):
    user = await auth_admin(request)
    require_enabled()
    db = get_db()
    if not ObjectId.is_valid(body.property_id) or not await db.properties.find_one({'_id': ObjectId(body.property_id)}):
        raise HTTPException(422, 'climate_property_invalid')
    if body.unit_id:
        if not ObjectId.is_valid(body.unit_id):
            raise HTTPException(422, 'climate_unit_invalid')
        unit = await db.property_units.find_one({'_id': ObjectId(body.unit_id)})
        if not unit or str(unit.get('property_id')) != body.property_id:
            raise HTTPException(422, 'climate_unit_invalid')
    devices = (await discover(body.connection_id, request))['devices']
    if not any(d['provider_device_id'] == body.provider_device_id and d['location_id'] == body.location_id for d in devices):
        raise HTTPException(422, 'climate_device_invalid')
    # Global device identity prevents assignment of one thermostat to two homes, even after reauthorization.
    kind = await provider.connection_kind(db, body.connection_id)
    identity = body.provider_device_id if kind == 'first_alert' else kind + ':' + body.provider_device_id
    identifier = hashlib.sha256(identity.encode()).hexdigest()
    record = body.model_dump()
    record.update(_id=identifier, provider=kind, created_by=actor_id(user), created_at=provider.now())
    try:
        await db.climate_bindings.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(409, 'climate_already_assigned')
    return {'id': identifier}


async def issue_command(binding, user, body):
    require_enabled()
    if not control():
        raise HTTPException(503, 'climate_control_disabled')
    db = get_db()
    values = body.model_dump(exclude_none=True, exclude={'request_id'})
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    key = hashlib.sha256((actor_id(user) + str(body.request_id)).encode()).hexdigest()
    previous = await db.climate_commands.find_one({'_id': key})
    if previous:
        if previous['device_id'] != binding['_id'] or previous['digest'] != digest:
            raise HTTPException(409, 'climate_request_conflict')
        return {'status': previous['status']}
    # Per-device lease serializes commands across API workers.
    locked = await db.climate_bindings.find_one_and_update({'_id': binding['_id'], '$or': [
        {'busy_until': {'$exists': False}}, {'busy_until': {'$lt': provider.now()}}]},
        {'$set': {'busy_until': provider.now() + timedelta(seconds=180), 'command_lock': key}})
    if not locked:
        raise HTTPException(409, 'climate_busy')
    inserted = False
    try:
        raw = await provider.device(db, binding)
        payload = validate_change(raw, values)
        try:
            await db.climate_commands.insert_one({'_id': key, 'device_id': binding['_id'], 'actor': actor_id(user),
                'digest': digest, 'requested': values, 'created_at': provider.now(), 'status': 'pending'})
            inserted = True
        except DuplicateKeyError:
            return {'status': 'pending'}
        await provider.change(db, binding, payload)
        observed = await provider.device(db, binding)
        observed_state = snapshot(observed)
        status = 'confirmed' if all(observed.get('changeableValues', {}).get(k) == v for k, v in values.items()) else 'pending'
        await db.climate_commands.update_one({'_id': key}, {'$set': {'status': status}})
        await climate_monitor.record_snapshot(db, binding, observed_state, source='command')
        return {'status': status}
    except HTTPException:
        if inserted:
            await db.climate_commands.update_one({'_id': key}, {'$set': {'status': 'unknown'}})
            return {'status': 'unknown'}
        raise
    finally:
        await db.climate_bindings.update_one({'_id': binding['_id'], 'command_lock': key}, {'$unset': {'busy_until': '', 'command_lock': ''}})


async def issue_feature_command(binding, user, request_id, kind, requested):
    require_enabled()
    if not control():
        raise HTTPException(503, 'climate_control_disabled')
    db = get_db()
    digest = hashlib.sha256(json.dumps(requested, sort_keys=True).encode()).hexdigest()
    key = hashlib.sha256((actor_id(user) + str(request_id) + kind).encode()).hexdigest()
    previous = await db.climate_commands.find_one({'_id': key})
    if previous:
        if previous['device_id'] != binding['_id'] or previous['digest'] != digest:
            raise HTTPException(409, 'climate_request_conflict')
        return {'status': previous['status']}
    locked = await db.climate_bindings.find_one_and_update({'_id': binding['_id'], '$or': [
        {'busy_until': {'$exists': False}}, {'busy_until': {'$lt': provider.now()}}]},
        {'$set': {'busy_until': provider.now() + timedelta(seconds=180), 'command_lock': key}})
    if not locked:
        raise HTTPException(409, 'climate_busy')
    inserted = False
    try:
        current = snapshot(await provider.device(db, binding))
        if kind == 'fan':
            if not current.get('capabilities', {}).get('fan') or requested['mode'] not in current.get('fanModes', []):
                raise HTTPException(422, 'climate_fan_mode_unsupported')
        elif kind == 'hold':
            if not current.get('capabilities', {}).get('hold') or requested['mode'] not in ('schedule', 'permanent'):
                raise HTTPException(422, 'climate_hold_mode_unsupported')
        else:
            raise HTTPException(422, 'climate_command_unsupported')
        try:
            await db.climate_commands.insert_one({
                '_id': key, 'device_id': binding['_id'], 'actor': actor_id(user),
                'kind': kind, 'digest': digest, 'requested': requested,
                'created_at': provider.now(), 'status': 'pending',
            })
            inserted = True
        except DuplicateKeyError:
            return {'status': 'pending'}
        if kind == 'fan':
            await provider.change_fan(db, binding, requested['mode'])
        else:
            await provider.change_hold(db, binding, requested['mode'])
        observed = snapshot(await provider.device(db, binding))
        confirmed = (
            observed.get('fanMode') == requested['mode']
            if kind == 'fan'
            else observed.get('holdStatus') == requested['mode']
        )
        status = 'confirmed' if confirmed else 'pending'
        await db.climate_commands.update_one({'_id': key}, {'$set': {'status': status}})
        await climate_monitor.record_snapshot(db, binding, observed, source=kind)
        return {'status': status}
    except HTTPException:
        if inserted:
            await db.climate_commands.update_one({'_id': key}, {'$set': {'status': 'unknown'}})
            return {'status': 'unknown'}
        raise
    finally:
        await db.climate_bindings.update_one(
            {'_id': binding['_id'], 'command_lock': key},
            {'$unset': {'busy_until': '', 'command_lock': ''}},
        )



async def admin_binding(device_id: str, request: Request):
    user = await auth_admin(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return user, binding


async def tenant_binding(device_id: str, request: Request):
    user, scope = await tenant_scope(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id, **scope})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return user, binding


@router.get('/admin/climate/devices/{device_id}/analytics')
async def admin_analytics(device_id: str, request: Request, range: str = '7d'):
    _, binding = await admin_binding(device_id, request)
    return await climate_monitor.analytics(get_db(), binding['_id'], range)


@router.get('/tenant/climate/devices/{device_id}/analytics')
async def tenant_analytics(device_id: str, request: Request, range: str = '7d'):
    _, binding = await tenant_binding(device_id, request)
    return await climate_monitor.analytics(get_db(), binding['_id'], range)


@router.get('/admin/climate/devices/{device_id}/reading')
async def admin_reading_at(device_id: str, request: Request, at: str):
    _, binding = await admin_binding(device_id, request)
    return await climate_monitor.nearest_reading(get_db(), binding['_id'], at)


@router.get('/tenant/climate/devices/{device_id}/reading')
async def tenant_reading_at(device_id: str, request: Request, at: str):
    _, binding = await tenant_binding(device_id, request)
    return await climate_monitor.nearest_reading(get_db(), binding['_id'], at)


@router.get('/admin/climate/devices/{device_id}/alerts')
async def admin_alerts(device_id: str, request: Request):
    _, binding = await admin_binding(device_id, request)
    return {'alerts': await climate_monitor.active_alerts(get_db(), binding)}


@router.get('/tenant/climate/devices/{device_id}/alerts')
async def tenant_alerts(device_id: str, request: Request):
    _, binding = await tenant_binding(device_id, request)
    return {'alerts': await climate_monitor.active_alerts(get_db(), binding)}


@router.get('/admin/climate/devices/{device_id}/schedules')
async def admin_schedules(device_id: str, request: Request):
    _, binding = await admin_binding(device_id, request)
    return {'schedules': await climate_monitor.list_schedules(get_db(), binding)}


@router.get('/tenant/climate/devices/{device_id}/schedules')
async def tenant_schedules(device_id: str, request: Request):
    _, binding = await tenant_binding(device_id, request)
    return {'schedules': await climate_monitor.list_schedules(get_db(), binding)}


@router.post('/admin/climate/devices/{device_id}/schedules')
async def admin_create_schedule(device_id: str, body: ScheduleBody, request: Request):
    user, binding = await admin_binding(device_id, request)
    doc = await climate_monitor.create_schedule(get_db(), binding, actor_id(user), body.model_dump())
    return climate_monitor.public_schedule(doc)


@router.post('/tenant/climate/devices/{device_id}/schedules')
async def tenant_create_schedule(device_id: str, body: ScheduleBody, request: Request):
    user, binding = await tenant_binding(device_id, request)
    doc = await climate_monitor.create_schedule(get_db(), binding, actor_id(user), body.model_dump())
    return climate_monitor.public_schedule(doc)


@router.put('/admin/climate/devices/{device_id}/schedules/{schedule_id}')
async def admin_update_schedule(device_id: str, schedule_id: str, body: ScheduleBody, request: Request):
    user, binding = await admin_binding(device_id, request)
    doc = await climate_monitor.update_schedule(get_db(), binding, schedule_id, actor_id(user), body.model_dump())
    return climate_monitor.public_schedule(doc)


@router.put('/tenant/climate/devices/{device_id}/schedules/{schedule_id}')
async def tenant_update_schedule(device_id: str, schedule_id: str, body: ScheduleBody, request: Request):
    user, binding = await tenant_binding(device_id, request)
    doc = await climate_monitor.update_schedule(get_db(), binding, schedule_id, actor_id(user), body.model_dump())
    return climate_monitor.public_schedule(doc)


@router.delete('/admin/climate/devices/{device_id}/schedules/{schedule_id}')
async def admin_delete_schedule(device_id: str, schedule_id: str, request: Request):
    _, binding = await admin_binding(device_id, request)
    await climate_monitor.delete_schedule(get_db(), binding, schedule_id)
    return {'success': True}


@router.delete('/tenant/climate/devices/{device_id}/schedules/{schedule_id}')
async def tenant_delete_schedule(device_id: str, schedule_id: str, request: Request):
    _, binding = await tenant_binding(device_id, request)
    await climate_monitor.delete_schedule(get_db(), binding, schedule_id)
    return {'success': True}


@router.post('/admin/climate/devices/{device_id}/schedules/{schedule_id}/run-now')
async def admin_run_schedule(device_id: str, schedule_id: str, body: ScheduleRun, request: Request):
    user, binding = await admin_binding(device_id, request)
    if not control():
        raise HTTPException(503, 'climate_control_disabled')
    return {'status': await climate_monitor.run_schedule_now(
        get_db(), binding, schedule_id, body.period_index, actor_id(user))}


@router.post('/tenant/climate/devices/{device_id}/schedules/{schedule_id}/run-now')
async def tenant_run_schedule(device_id: str, schedule_id: str, body: ScheduleRun, request: Request):
    user, binding = await tenant_binding(device_id, request)
    if not control():
        raise HTTPException(503, 'climate_control_disabled')
    return {'status': await climate_monitor.run_schedule_now(
        get_db(), binding, schedule_id, body.period_index, actor_id(user))}


@router.patch('/admin/climate/devices/{device_id}')
async def admin_command(device_id: str, body: Command, request: Request):
    user = await auth_admin(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return await issue_command(binding, user, body)


@router.patch('/tenant/climate/devices/{device_id}')
async def tenant_command(device_id: str, body: Command, request: Request):
    user, scope = await tenant_scope(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id, **scope})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return await issue_command(binding, user, body)


@router.patch('/admin/climate/devices/{device_id}/fan')
async def admin_fan_command(device_id: str, body: FanCommand, request: Request):
    user = await auth_admin(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return await issue_feature_command(binding, user, body.request_id, 'fan', {'mode': body.mode})


@router.patch('/tenant/climate/devices/{device_id}/fan')
async def tenant_fan_command(device_id: str, body: FanCommand, request: Request):
    user, scope = await tenant_scope(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id, **scope})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return await issue_feature_command(binding, user, body.request_id, 'fan', {'mode': body.mode})


@router.patch('/admin/climate/devices/{device_id}/hold')
async def admin_hold_command(device_id: str, body: HoldCommand, request: Request):
    user = await auth_admin(request)
    binding = await get_db().climate_bindings.find_one({'_id': device_id})
    if not binding:
        raise HTTPException(404, 'climate_device_not_found')
    return await issue_feature_command(binding, user, body.request_id, 'hold', {'mode': body.mode})
