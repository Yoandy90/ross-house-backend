"""TCC US bridge using the pinned AIOSomecomfort community protocol client.

No automatic retries or separate writes for mode/setpoints. Account sessions are
isolated, encrypted at rest and serialized across workers. No credentials reach
tenants. First Alert OAuth remains independent.
"""
import asyncio
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta

import aiohttp
from aiosomecomfort import AIOSomeComfort, SomeComfortError
from aiosomecomfort.exceptions import APIError
from fastapi import HTTPException
from yarl import URL

from . import climate_provider as crypto
from .climate_policy import validate_change

# Upstream debug logging includes authentication cookies and response bodies.
logging.getLogger('somecomfort').disabled = True
ORIGIN = URL('https://mytotalconnectcomfort.com')
MODES = {1: 'Heat', 2: 'Off', 3: 'Cool', 4: 'Auto', 5: 'Auto'}


async def safe_redirect(session, context, params):
    target = params.url.join(URL(params.response.headers.get('Location', '')))
    if target.origin() != ORIGIN:
        raise HTTPException(502, 'climate_provider_invalid_redirect')


@asynccontextmanager
async def session_client(credentials, cookies=None):
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(safe_redirect)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                     trace_configs=[trace]) as session:
        if cookies:
            session.cookie_jar.update_cookies(cookies, response_url=ORIGIN)
        client = AIOSomeComfort(credentials['username'], credentials['password'],
                                session=session, timeout=15)
        # Restored sessions must use JSON just like a completed upstream login.
        if cookies:
            client._headers['Content-Type'] = 'application/json'
        yield client, session


def cookie_values(session):
    return {k: v.value for k, v in session.cookie_jar.filter_cookies(ORIGIN / 'portal').items()}


async def locations(client):
    # Pinned upstream endpoint; discovery is read-only despite its POST transport.
    rows = await client._get_locations()
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise HTTPException(502, 'climate_provider_invalid')
    return [{'location_id': str(row['LocationID']), 'provider_device_id': str(d['DeviceID']),
             'name': str(d.get('Name') or d['DeviceID'])}
            for row in rows for d in row.get('Devices', []) if d.get('DeviceID')]


def normalize(response):
    if not isinstance(response, dict) or response.get('success') not in (True, 1):
        raise HTTPException(502, 'climate_provider_invalid')
    latest = response.get('latestData') or {}
    ui = latest.get('uiData')
    if not isinstance(ui, dict):
        raise HTTPException(502, 'climate_provider_invalid')
    fan = latest.get('fanData') or {}
    has_fan = latest.get('hasFan') is True and isinstance(fan, dict)
    fan_modes = []
    if has_fan:
        for label, key in (
            ('Auto', 'fanModeAutoAllowed'),
            ('On', 'fanModeOnAllowed'),
            ('Circulate', 'fanModeCirculateAllowed'),
            ('FollowSchedule', 'fanModeFollowScheduleAllowed'),
        ):
            if fan.get(key) in (True, 1):
                fan_modes.append(label)
    hold_values = {0: 'schedule', 1: 'temporary', 2: 'permanent'}
    heat_hold, cool_hold = hold_values.get(ui.get('StatusHeat')), hold_values.get(ui.get('StatusCool'))
    hold_status = heat_hold if heat_hold == cool_hold else ('mixed' if heat_hold or cool_hold else None)
    return {
        'isAlive': response.get('deviceLive') in (True, 1) and response.get('communicationLost') in (False, 0),
        'units': {'F': 'Fahrenheit', 'C': 'Celsius'}.get(ui.get('DisplayUnits')),
        'indoorTemperature': ui.get('DispTemperature'),
        'indoorHumidity': ui.get('IndoorHumidity') if ui.get('IndoorHumiditySensorAvailable') and ui.get('IndoorHumiditySensorNotFault') else None,
        'outdoorTemperature': ui.get('OutdoorTemperature') if ui.get('OutdoorTemperatureAvailable') else None,
        'displayedOutdoorHumidity': ui.get('OutdoorHumidity') if ui.get('OutdoorHumidityAvailable') else None,
        'allowedModes': [m for m in ('Off', 'Heat', 'Cool', 'Auto') if ui.get('Switch' + m + 'Allowed') in (True, 1)],
        'changeableValues': {'mode': MODES.get(ui.get('SystemSwitchPosition')),
                             'heatSetpoint': ui.get('HeatSetpoint'), 'coolSetpoint': ui.get('CoolSetpoint')},
        'minHeatSetpoint': ui.get('HeatLowerSetptLimit'), 'maxHeatSetpoint': ui.get('HeatUpperSetptLimit'),
        'minCoolSetpoint': ui.get('CoolLowerSetptLimit'), 'maxCoolSetpoint': ui.get('CoolUpperSetptLimit'),
        'deadband': ui.get('Deadband'),
        'activity': {0: 'idle', 1: 'heating', 2: 'cooling'}.get(ui.get('EquipmentOutputStatus')),
        'fan': {
            'supported': has_fan and bool(fan_modes),
            'allowedModes': fan_modes,
            'mode': {0: 'Auto', 1: 'On', 2: 'Circulate', 3: 'FollowSchedule'}.get(fan.get('fanMode')),
            'running': fan.get('fanIsRunning') if has_fan else None,
        },
        'holdStatus': hold_status,
        'scheduleStatus': 'resume' if hold_status == 'schedule' else 'hold',
        'scheduleCapabilities': {'availableScheduleTypes': [], 'schedulableFan': False},
    }


async def connect(db, actor, username, password):
    crypto.cipher()  # Fail before contacting the provider if encryption is unavailable.
    # Stable account identity also rate-limits failed setup attempts across workers.
    identity = 'tcc_us:' + hashlib.sha256(username.casefold().encode()).hexdigest()
    from pymongo.errors import DuplicateKeyError
    try:
        await db.climate_oauth_states.insert_one({'_id': identity, 'expires_at': crypto.now() + timedelta(minutes=2), 'actor': actor})
    except DuplicateKeyError:
        raise HTTPException(429, 'climate_busy')
    credentials = {'username': username, 'password': password}
    try:
        async with asyncio.timeout(45), session_client(credentials) as (client, session):
            await client.login()
            await locations(client)  # Authenticated JSON proves login, including empty accounts.
            cookies = cookie_values(session)
            if not cookies.get('.ASPXAUTH_TRUEHOME'):
                raise HTTPException(503, 'climate_reconnect_required')
        # A new ID avoids replacing credentials underneath an in-flight command.
        connection_id = secrets.token_hex(16)
        await db.climate_connections.insert_one({'_id': connection_id, 'provider': 'tcc_us',
            'credentials': crypto.seal(credentials), 'session': crypto.seal(cookies),
            'session_until': crypto.now() + timedelta(hours=6), 'created_at': crypto.now(), 'created_by': actor})
        return connection_id
    except (SomeComfortError, aiohttp.ClientError, TimeoutError, KeyError, TypeError, ValueError):
        raise HTTPException(503, 'climate_reconnect_required') from None


@asynccontextmanager
async def account(db, connection_id):
    lock = secrets.token_hex(16)
    now = crypto.now()
    connection = await db.climate_connections.find_one_and_update({
        '_id': connection_id, 'provider': 'tcc_us', '$and': [
            {'$or': [{'session_busy_until': {'$exists': False}}, {'session_busy_until': {'$lt': now}}]},
            {'$or': [{'retry_after': {'$exists': False}}, {'retry_after': {'$lt': now}}]}]},
        {'$set': {'session_busy_until': now + timedelta(seconds=60), 'session_lock': lock}})
    if not connection:
        raise HTTPException(503, 'climate_busy')
    query = {'_id': connection_id, 'session_lock': lock}
    try:
        credentials = crypto.unseal(connection['credentials'])
        expiry = connection.get('session_until')
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=now.tzinfo)
        cookies = crypto.unseal(connection['session']) if expiry and expiry > now and connection.get('session') else None
        async with asyncio.timeout(45), session_client(credentials, cookies) as (client, session):
            if not cookies:
                await client.login()
            yield client
            await db.climate_connections.update_one(query, {'$set': {
                'session': crypto.seal(cookie_values(session)),
                'session_until': (expiry if cookies else crypto.now() + timedelta(hours=6))}})
    except (SomeComfortError, aiohttp.ClientError, TimeoutError, KeyError, TypeError, ValueError):
        await db.climate_connections.update_one(query, {'$unset': {'session': '', 'session_until': ''},
            '$set': {'retry_after': crypto.now() + timedelta(minutes=2)}})
        raise HTTPException(503, 'climate_provider_unavailable') from None
    finally:
        await db.climate_connections.update_one(query, {'$unset': {'session_busy_until': '', 'session_lock': ''}})


async def discover(db, connection_id):
    async with account(db, connection_id) as client:
        return await locations(client)


async def device(db, binding):
    async with account(db, binding['connection_id']) as client:
        return normalize(await client.get_thermostat_data(binding['provider_device_id']))


async def change(db, binding, values):
    async with account(db, binding['connection_id']) as client:
        raw = normalize(await client.get_thermostat_data(binding['provider_device_id']))
        # Revalidate inside the account lock; send only values actually changed.
        command = {k: v for k, v in values.items() if k in ('mode', 'heatSetpoint', 'coolSetpoint')
                   and v != raw['changeableValues'].get(k)}
        if not command:
            return
        if raw['changeableValues']['mode'] is None and 'mode' not in command:
            raise HTTPException(409, 'climate_mode_unsupported')
        validate_change(raw, command)
        settings = {}
        if 'mode' in command:
            settings['SystemSwitch'] = {'Heat': 1, 'Off': 2, 'Cool': 3, 'Auto': 4}[command['mode']]

        # TCC expects the heat/cool pair to remain internally consistent when either
        # setpoint changes. Mirror the provider client's own Device behavior: preserve
        # the untouched setpoint and move it only when required by the native deadband.
        if 'heatSetpoint' in command or 'coolSetpoint' in command:
            current = raw['changeableValues']
            heat = command.get('heatSetpoint', current.get('heatSetpoint'))
            cool = command.get('coolSetpoint', current.get('coolSetpoint'))
            band = raw.get('deadband')
            limits = (
                raw.get('minHeatSetpoint'), raw.get('maxHeatSetpoint'),
                raw.get('minCoolSetpoint'), raw.get('maxCoolSetpoint'),
            )
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (heat, cool, band, *limits)):
                raise HTTPException(409, 'climate_capabilities_unknown')
            min_heat, max_heat, min_cool, max_cool = limits
            if band > 0 and cool - heat < band:
                if 'heatSetpoint' in command and 'coolSetpoint' not in command:
                    cool = heat + band
                elif 'coolSetpoint' in command and 'heatSetpoint' not in command:
                    heat = cool - band
                else:
                    raise HTTPException(422, 'climate_deadband_invalid')
            if not (min_heat <= heat <= max_heat and min_cool <= cool <= max_cool):
                raise HTTPException(422, 'climate_temperature_out_of_range')
            settings.update(
                HeatSetpoint=heat,
                CoolSetpoint=cool,
                StatusHeat=2,
                StatusCool=2,
            )

        # Exactly one physical command; no retry, including after a timeout.
        try:
            await client.set_thermostat_settings(binding['provider_device_id'], settings)
        except APIError:
            # The provider explicitly rejected the request; this is not an ambiguous
            # timeout and should not invalidate the authenticated account session.
            raise HTTPException(409, 'climate_provider_rejected') from None


async def change_fan(db, binding, mode):
    async with account(db, binding['connection_id']) as client:
        raw = normalize(await client.get_thermostat_data(binding['provider_device_id']))
        fan = raw.get('fan') or {}
        if not fan.get('supported') or mode not in fan.get('allowedModes', []):
            raise HTTPException(422, 'climate_fan_mode_unsupported')
        if mode == fan.get('mode'):
            return
        value = {'Auto': 0, 'On': 1, 'Circulate': 2, 'FollowSchedule': 3}[mode]
        try:
            await client.set_thermostat_settings(binding['provider_device_id'], {'FanMode': value})
        except APIError:
            raise HTTPException(409, 'climate_provider_rejected') from None


async def change_hold(db, binding, mode):
    if mode not in ('schedule', 'permanent'):
        raise HTTPException(422, 'climate_hold_mode_unsupported')
    async with account(db, binding['connection_id']) as client:
        raw = normalize(await client.get_thermostat_data(binding['provider_device_id']))
        if raw.get('holdStatus') == mode:
            return
        value = 0 if mode == 'schedule' else 2
        try:
            await client.set_thermostat_settings(binding['provider_device_id'], {
                'StatusHeat': value,
                'StatusCool': value,
            })
        except APIError:
            raise HTTPException(409, 'climate_provider_rejected') from None
