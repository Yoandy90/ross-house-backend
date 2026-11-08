"""Bounded TCC operation diagnostics. Never store payloads or exception messages."""
from datetime import datetime, timedelta, timezone
import hashlib


async def ensure_indexes(db):
    await db.climate_tcc_metrics.create_index('expires_at', expireAfterSeconds=0)


async def record(db, connection_id, operation, outcome, phase, elapsed_ms, restored, login):
    current = datetime.now(timezone.utc)
    bucket = current.replace(minute=0, second=0, microsecond=0)
    operation = operation if operation in {'read', 'discovery', 'change', 'fan', 'hold'} else 'other'
    outcome = outcome if outcome in {'success', 'transport', 'session', 'rate_limited', 'provider', 'invalid_response', 'controlled', 'busy'} else 'other'
    phase = phase if phase in {'session_restore', 'login', 'operation', 'session_persist', 'lease'} else 'other'
    elapsed_ms = max(0, min(int(elapsed_ms), 120000))
    increments = {'attempts': 1, 'operations.' + operation: 1, 'duration_ms_total': elapsed_ms,
                  'session_restores': int(restored), 'login_attempts': int(login)}
    if outcome == 'success':
        increments['successes'] = 1
    else:
        increments['failures.' + outcome] = 1
        increments['failure_phases.' + phase] = 1
    try:
        await db.climate_tcc_metrics.update_one(
            {'_id': hashlib.sha256(str(connection_id).encode()).hexdigest() + ':' + bucket.isoformat()},
            {'$inc': increments, '$max': {'duration_ms_max': elapsed_ms},
             '$set': {'last_at': current},
             '$setOnInsert': {'bucket_at': bucket, 'expires_at': current + timedelta(days=30)}},
            upsert=True,
        )
    except Exception:
        pass  # Diagnostics cannot alter provider outcomes or command execution.
