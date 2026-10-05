"""Resideo cloud adapter. Credentials never leave the server or enter logs."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pymongo import ReturnDocument

BASE = 'https://api.honeywellhome.com'


def now():
    return datetime.now(timezone.utc)


def config():
    names = ('CLIMATE_RESIDEO_CLIENT_ID', 'CLIMATE_RESIDEO_CLIENT_SECRET',
             'CLIMATE_TOKEN_KEY', 'CLIMATE_REDIRECT_URI')
    values = [os.getenv(n, '') for n in names]
    if not all(values) or not values[3].startswith('https://'):
        raise HTTPException(503, 'climate_not_configured')
    try:
        Fernet(values[2].encode())
    except (ValueError, TypeError):
        raise HTTPException(503, 'climate_not_configured')
    return values


def configured():
    try:
        config()
        return True
    except HTTPException:
        return False


def cipher():
    try:
        return Fernet(os.environ['CLIMATE_TOKEN_KEY'].encode())
    except (KeyError, ValueError, TypeError):
        raise HTTPException(503, 'climate_not_configured')


def tcc_configured():
    try:
        cipher()
        return True
    except HTTPException:
        return False


def seal(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise HTTPException(503, 'climate_reconnect_required')


async def token_request(data):
    client_id, secret, _, _ = config()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(BASE + '/oauth2/token', data=data,
                                         auth=(client_id, secret))
        if response.status_code >= 400:
            raise HTTPException(503, 'climate_reconnect_required')
        result = response.json()
        if not result.get('access_token') or not result.get('refresh_token'):
            raise HTTPException(502, 'climate_provider_invalid')
        return result
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'climate_provider_unavailable')


async def start_oauth(db, actor):
    client_id, _, _, redirect = config()
    state = secrets.token_urlsafe(32)
    await db.climate_oauth_states.insert_one({
        '_id': hashlib.sha256(state.encode()).hexdigest(), 'actor': actor,
        'expires_at': now() + timedelta(minutes=10),
    })
    return BASE + '/oauth2/authorize?' + urlencode({
        'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect,
        'state': state, 'subSystemId': '5',
    })


async def finish_oauth(db, actor, state, code):
    entry = await db.climate_oauth_states.find_one_and_delete({
        '_id': hashlib.sha256(state.encode()).hexdigest(), 'actor': actor,
        'expires_at': {'$gt': now()},
    })
    if not entry:
        raise HTTPException(400, 'climate_oauth_invalid')
    result = await token_request({'grant_type': 'authorization_code', 'code': code,
                                  'redirect_uri': config()[3]})
    connection_id = secrets.token_hex(16)
    await db.climate_connections.insert_one({
        '_id': connection_id, 'provider': 'first_alert', 'tokens': seal(result), 'created_by': actor,
        'created_at': now(), 'expires_at': now() + timedelta(seconds=int(result.get('expires_in', 600))),
    })
    return connection_id


async def access_token(db, connection_id):
    connection = await db.climate_connections.find_one({'_id': connection_id})
    if not connection:
        raise HTTPException(503, 'climate_reconnect_required')
    expiry = connection['expires_at']
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry > now() + timedelta(seconds=60):
        return unseal(connection['tokens'])['access_token']
    # Cross-worker lock prevents rotating the same refresh token concurrently.
    lock = secrets.token_hex(16)
    locked = await db.climate_connections.find_one_and_update({
        '_id': connection_id, '$or': [{'refresh_until': {'$exists': False}},
                                     {'refresh_until': {'$lt': now()}}],
    }, {'$set': {'refresh_until': now() + timedelta(seconds=45), 'refresh_lock': lock}},
        return_document=ReturnDocument.AFTER)
    if not locked:
        raise HTTPException(503, 'climate_busy')
    try:
        expiry = locked['expires_at']
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry > now() + timedelta(seconds=60):
            return unseal(locked['tokens'])['access_token']
        result = await token_request({'grant_type': 'refresh_token',
                                     'refresh_token': unseal(locked['tokens'])['refresh_token']})
        await db.climate_connections.update_one({'_id': connection_id, 'refresh_lock': lock}, {'$set': {
            'tokens': seal(result), 'expires_at': now() + timedelta(seconds=int(result.get('expires_in', 600))),
        }})
        return result['access_token']
    finally:
        await db.climate_connections.update_one({'_id': connection_id, 'refresh_lock': lock},
                                                {'$unset': {'refresh_until': '', 'refresh_lock': ''}})


async def api(db, connection_id, method, path, location_id=None, body=None):
    token = await access_token(db, connection_id)
    params = {'apikey': config()[0]}
    if location_id is not None:
        params['locationId'] = str(location_id)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, BASE + '/v2' + path, params=params,
                                            headers={'Authorization': 'Bearer ' + token}, json=body)
        if response.status_code in (401, 403):
            raise HTTPException(503, 'climate_reconnect_required')
        if response.status_code >= 400:
            raise HTTPException(503, 'climate_provider_unavailable')
        return response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        # A POST timeout has unknown outcome: never automatically resend it.
        raise HTTPException(503, 'climate_provider_unavailable')


async def connection_kind(db, connection_id):
    record = await db.climate_connections.find_one({'_id': connection_id})
    if not record:
        raise HTTPException(503, 'climate_reconnect_required')
    kind = record.get('provider', 'first_alert')
    if kind not in ('first_alert', 'tcc_us'):
        raise HTTPException(503, 'climate_provider_unsupported')
    return kind


async def discover(db, connection_id):
    if await connection_kind(db, connection_id) == 'tcc_us':
        from . import climate_tcc
        return await climate_tcc.discover(db, connection_id)
    locations = await api(db, connection_id, 'GET', '/locations')
    return [{'location_id': str(l['locationID']), 'provider_device_id': str(d['deviceID']),
             'name': d.get('userDefinedDeviceName') or d.get('name') or str(d['deviceID'])}
            for l in locations for d in l.get('devices', [])
            if d.get('deviceID') and d.get('deviceClass') == 'Thermostat']


async def device(db, binding):
    if await connection_kind(db, binding['connection_id']) == 'tcc_us':
        from . import climate_tcc
        return await climate_tcc.device(db, binding)
    return await api(db, binding['connection_id'], 'GET',
                     '/devices/thermostats/' + quote(binding['provider_device_id'], safe=''),
                     binding['location_id'])


async def change(db, binding, values):
    if await connection_kind(db, binding['connection_id']) == 'tcc_us':
        from . import climate_tcc
        return await climate_tcc.change(db, binding, values)
    await api(db, binding['connection_id'], 'POST',
              '/devices/thermostats/' + quote(binding['provider_device_id'], safe=''),
              binding['location_id'], values)


async def change_fan(db, binding, mode):
    if await connection_kind(db, binding['connection_id']) == 'tcc_us':
        from . import climate_tcc
        return await climate_tcc.change_fan(db, binding, mode)
    raw = await api(
        db,
        binding['connection_id'],
        'GET',
        '/devices/thermostats/' + quote(binding['provider_device_id'], safe='') + '/fan',
        binding['location_id'],
    )
    allowed = [x for x in raw.get('allowedModes', []) if x in ('Auto', 'On', 'Circulate')]
    if mode not in allowed:
        raise HTTPException(422, 'climate_fan_mode_unsupported')
    await api(
        db,
        binding['connection_id'],
        'POST',
        '/devices/thermostats/' + quote(binding['provider_device_id'], safe='') + '/fan',
        binding['location_id'],
        {'mode': mode},
    )


async def change_hold(db, binding, mode):
    if await connection_kind(db, binding['connection_id']) == 'tcc_us':
        from . import climate_tcc
        return await climate_tcc.change_hold(db, binding, mode)
    if mode not in ('schedule', 'permanent'):
        raise HTTPException(422, 'climate_hold_mode_unsupported')
    raw = await device(db, binding)
    values = raw.get('changeableValues') or {}
    body = {
        'mode': values.get('mode'),
        'heatSetpoint': values.get('heatSetpoint'),
        'coolSetpoint': values.get('coolSetpoint'),
        'thermostatSetpointStatus': 'NoHold' if mode == 'schedule' else 'PermanentHold',
    }
    if not body['mode']:
        raise HTTPException(409, 'climate_capabilities_unknown')
    await api(
        db,
        binding['connection_id'],
        'POST',
        '/devices/thermostats/' + quote(binding['provider_device_id'], safe=''),
        binding['location_id'],
        body,
    )
