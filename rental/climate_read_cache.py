"""Shared short-lived cache for public TCC display reads, never physical commands."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

PUBLIC_FIELDS = {
    'id', 'name', 'property_id', 'unit_id', 'online', 'units', 'temperature',
    'humidity', 'outdoorTemperature', 'outdoorHumidity', 'outdoorSource',
    'mode', 'heatSetpoint', 'coolSetpoint', 'activity', 'modes', 'fanModes',
    'fanMode', 'fanRunning', 'holdStatus', 'scheduleStatus',
    'currentSchedulePeriod', 'capabilities', 'minHeatSetpoint', 'maxHeatSetpoint',
    'minCoolSetpoint', 'maxCoolSetpoint', 'deadband', 'weatherSummary',
    'observed_at', 'error',
}


def now():
    return datetime.now(timezone.utc)


def key_for(binding):
    identity = [str(binding.get(k) or '') for k in
                ('_id', 'connection_id', 'property_id', 'unit_id', 'name')]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _future(value, current):
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > current


async def ensure_indexes(db):
    await db.climate_read_cache.create_index('garbage_at', expireAfterSeconds=0)
    await db.climate_read_metrics.create_index('expires_at', expireAfterSeconds=0)


async def _metric(db, binding, kind, duration_ms=0):
    current = now()
    bucket = current.replace(minute=0, second=0, microsecond=0)
    try:
        await db.climate_read_metrics.update_one(
            {'_id': key_for(binding) + ':' + bucket.isoformat()},
            {'$inc': {kind: 1, 'duration_ms_total': duration_ms},
             '$max': {'duration_ms_max': duration_ms},
             '$set': {'last_at': current},
             '$setOnInsert': {'device_id': str(binding['_id']), 'provider': 'tcc_us',
                              'bucket_at': bucket, 'expires_at': current + timedelta(days=30)}},
            upsert=True,
        )
    except Exception:
        # Diagnostics must not discard an otherwise valid reading.
        pass


async def invalidate(db, binding):
    if binding.get('provider') == 'tcc_us':
        try:
            await db.climate_read_cache.delete_one({'_id': key_for(binding)})
        except Exception:
            # Cache maintenance must not mask a physical command's outcome or
            # prevent releasing its independent command lock.
            await _metric(db, binding, 'invalidation_failures')


async def shared_read(db, binding, load):
    key = key_for(binding)
    current = now()
    doc = await db.climate_read_cache.find_one({'_id': key})
    if doc and _future(doc.get('expires_at'), current) and isinstance(doc.get('payload'), dict):
        await _metric(db, binding, 'cache_hits')
        return deepcopy(doc['payload'])
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        await db.climate_read_cache.update_one(
            {'_id': key}, {'$setOnInsert': {'expires_at': epoch, 'lock_until': epoch,
                                          'garbage_at': current + timedelta(days=1)}}, upsert=True,
        )
    except DuplicateKeyError:
        pass  # Another worker initialized the same device cache.
    token = str(uuid4())
    locked = await db.climate_read_cache.find_one_and_update(
        {'_id': key, 'expires_at': {'$lte': current}, 'lock_until': {'$lte': current}},
        {'$set': {'lock_until': current + timedelta(seconds=90), 'lock_token': token}},
        return_document=ReturnDocument.AFTER,
    )
    if not locked:
        # Join another worker's read; never start a second provider request.
        for _ in range(5):
            await asyncio.sleep(1)
            doc = await db.climate_read_cache.find_one({'_id': key})
            if doc and _future(doc.get('expires_at'), now()) and isinstance(doc.get('payload'), dict):
                await _metric(db, binding, 'cache_hits')
                return deepcopy(doc['payload'])
        await _metric(db, binding, 'coalesced_busy')
        raise HTTPException(503, 'climate_busy')
    query = {'_id': key, 'lock_token': token}
    started = monotonic()
    outcome = 'load_failures'
    try:
        result = await load()
        payload = {k: deepcopy(v) for k, v in result.items() if k in PUBLIC_FIELDS}
        healthy = payload.get('online') is True and not payload.get('error')
        outcome = 'provider_successes' if healthy else 'provider_failures'
        ttl = 60 if healthy else 15
        await db.climate_read_cache.update_one(query, {'$set': {
            'payload': payload, 'expires_at': now() + timedelta(seconds=ttl),
            'garbage_at': now() + timedelta(days=1),
        }})
        return payload
    finally:
        await _metric(db, binding, outcome, round((monotonic() - started) * 1000))
        await db.climate_read_cache.update_one(query, {'$set': {
            'lock_until': epoch,
        }, '$unset': {'lock_token': ''}})

