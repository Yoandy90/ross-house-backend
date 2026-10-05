"""Climate telemetry, schedules, and analytics for Ross House.

The raw stream is intentionally provider-neutral. Every sample stores the normalized
snapshot that the UI sees, plus device/property scope. Schedules are Ross House-owned
so legacy providers do not need to expose their own weekly schedule API.

Autonomous collection/execution is opt-in. Staging can safely exercise the same code
through authenticated manual reads and endpoints without enabling the worker.
"""
import asyncio
import hashlib
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from . import climate_provider as provider
from .climate_policy import snapshot, validate_change

DEFAULT_TZ = "America/Chicago"
RANGES = {
    "24h": (timedelta(hours=24), 15),
    "7d": (timedelta(days=7), 60),
    "30d": (timedelta(days=30), 360),
    "90d": (timedelta(days=90), 720),
    "1y": (timedelta(days=365), 1440),
    "all": (timedelta(days=365 * 5), 1440),
}


def now():
    return datetime.now(timezone.utc)


async def ensure_indexes(db):
    await db.climate_readings.create_index([("device_id", 1), ("observed_at", 1)])
    await db.climate_readings.create_index([("property_id", 1), ("observed_at", 1)])
    await db.climate_schedules.create_index([("device_id", 1), ("enabled", 1)])
    await db.climate_schedules.create_index([("property_id", 1), ("enabled", 1)])
    await db.climate_schedule_runs.create_index([("schedule_id", 1), ("scheduled_for", -1)])


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _minute_id(device_id, observed):
    minute = observed.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return hashlib.sha256(f"{device_id}:{minute.isoformat()}".encode()).hexdigest()


def reading_document(binding, snap, observed=None, source="read"):
    observed = observed or now()
    doc = {
        "_id": _minute_id(binding["_id"], observed),
        "device_id": binding["_id"],
        "property_id": binding["property_id"],
        "unit_id": binding.get("unit_id", ""),
        "provider": binding.get("provider", "first_alert"),
        "observed_at": observed,
        "source": source,
    }
    for key in (
        "temperature", "humidity", "outdoorTemperature", "outdoorHumidity",
        "mode", "heatSetpoint", "coolSetpoint", "activity",
        "fanMode", "fanRunning", "holdStatus", "scheduleStatus",
    ):
        value = snap.get(key)
        if value is not None:
            doc[key] = value
    return doc


async def record_snapshot(db, binding, snap, source="read", observed=None):
    doc = reading_document(binding, snap, observed=observed, source=source)
    await db.climate_readings.update_one(
        {"_id": doc["_id"]},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def collect_binding(db, binding, source="poll"):
    raw = await provider.device(db, binding)
    snap = snapshot(raw)
    if snap.get("online"):
        await record_snapshot(db, binding, snap, source=source)
    return snap


async def collect_all(db):
    if os.getenv("CLIMATE_ENABLED") != "true":
        return {"sampled": 0, "failed": 0}
    sampled = failed = 0
    async for binding in db.climate_bindings.find({}).limit(500):
        try:
            await collect_binding(db, binding, source="poll")
            sampled += 1
        except Exception:
            failed += 1
    return {"sampled": sampled, "failed": failed}


def validate_schedule_payload(data):
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise HTTPException(422, "climate_schedule_name_invalid")
    tz_name = str(data.get("timezone") or DEFAULT_TZ)
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(422, "climate_schedule_timezone_invalid")
    days = data.get("days")
    if not isinstance(days, list) or not days or len(set(days)) != len(days):
        raise HTTPException(422, "climate_schedule_days_invalid")
    if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 or x > 6 for x in days):
        raise HTTPException(422, "climate_schedule_days_invalid")
    clock = str(data.get("time") or "")
    try:
        hh, mm = map(int, clock.split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59) or len(clock) != 5:
            raise ValueError
    except Exception:
        raise HTTPException(422, "climate_schedule_time_invalid")
    mode = data.get("mode")
    if mode not in ("Off", "Heat", "Cool", "Auto"):
        raise HTTPException(422, "climate_schedule_mode_invalid")
    command = {"mode": mode}
    if mode in ("Heat", "Auto"):
        if not _finite(data.get("heatSetpoint")):
            raise HTTPException(422, "climate_schedule_heat_required")
        command["heatSetpoint"] = data["heatSetpoint"]
    if mode in ("Cool", "Auto"):
        if not _finite(data.get("coolSetpoint")):
            raise HTTPException(422, "climate_schedule_cool_required")
        command["coolSetpoint"] = data["coolSetpoint"]
    return {
        "name": name,
        "enabled": bool(data.get("enabled", True)),
        "timezone": tz_name,
        "days": sorted(days),
        "time": clock,
        **command,
    }


async def validate_schedule_for_device(db, binding, data):
    normalized = validate_schedule_payload(data)
    raw = await provider.device(db, binding)
    validate_change(
        raw,
        {k: normalized[k] for k in ("mode", "heatSetpoint", "coolSetpoint") if k in normalized},
    )
    return normalized


def public_schedule(doc):
    return {
        "id": str(doc["_id"]),
        "device_id": doc["device_id"],
        "name": doc["name"],
        "enabled": doc.get("enabled", True),
        "timezone": doc.get("timezone", DEFAULT_TZ),
        "days": doc.get("days", []),
        "time": doc["time"],
        "mode": doc["mode"],
        "heatSetpoint": doc.get("heatSetpoint"),
        "coolSetpoint": doc.get("coolSetpoint"),
        "last_status": doc.get("last_status"),
        "last_run_at": doc.get("last_run_at").isoformat() if doc.get("last_run_at") else None,
    }


async def create_schedule(db, binding, actor, data):
    values = await validate_schedule_for_device(db, binding, data)
    sid = hashlib.sha256(
        f"{binding['_id']}:{actor}:{now().isoformat()}:{values['name']}".encode()
    ).hexdigest()
    doc = {
        "_id": sid,
        "device_id": binding["_id"],
        "property_id": binding["property_id"],
        "unit_id": binding.get("unit_id", ""),
        "created_by": actor,
        "created_at": now(),
        **values,
    }
    await db.climate_schedules.insert_one(doc)
    return public_schedule(doc)


async def update_schedule(db, schedule, actor, data, binding):
    values = await validate_schedule_for_device(db, binding, data)
    values.update(updated_by=actor, updated_at=now())
    await db.climate_schedules.update_one({"_id": schedule["_id"]}, {"$set": values})
    merged = {**schedule, **values}
    return public_schedule(merged)


async def _execute_schedule(db, schedule, binding, scheduled_for):
    occurrence = f"{schedule['_id']}:{scheduled_for.astimezone(timezone.utc).isoformat()}"
    occurrence_id = hashlib.sha256(occurrence.encode()).hexdigest()
    run_doc = {
        "_id": occurrence_id,
        "schedule_id": schedule["_id"],
        "device_id": binding["_id"],
        "scheduled_for": scheduled_for,
        "claimed_at": now(),
        "status": "pending",
    }
    try:
        await db.climate_schedule_runs.insert_one(run_doc)
    except DuplicateKeyError:
        return "duplicate"

    command = {
        key: schedule[key]
        for key in ("mode", "heatSetpoint", "coolSetpoint")
        if key in schedule and schedule[key] is not None
    }
    lock = occurrence_id
    locked = await db.climate_bindings.find_one_and_update(
        {
            "_id": binding["_id"],
            "$or": [
                {"busy_until": {"$exists": False}},
                {"busy_until": {"$lt": now()}},
            ],
        },
        {"$set": {"busy_until": now() + timedelta(seconds=180), "command_lock": lock}},
    )
    if not locked:
        await db.climate_schedule_runs.update_one(
            {"_id": occurrence_id},
            {"$set": {"status": "skipped_busy", "finished_at": now()}},
        )
        return "skipped_busy"

    try:
        raw = await provider.device(db, binding)
        payload = validate_change(raw, command)
        before = snapshot(raw)
        await provider.change(db, binding, payload)
        after_raw = await provider.device(db, binding)
        after = snapshot(after_raw)
        confirmed = all(after.get(k) == v for k, v in command.items())
        status = "confirmed" if confirmed else "pending"
        await record_snapshot(db, binding, after, source="schedule")
        await db.climate_schedule_runs.update_one(
            {"_id": occurrence_id},
            {"$set": {
                "status": status,
                "finished_at": now(),
                "requested": command,
                "before": {k: before.get(k) for k in ("mode", "heatSetpoint", "coolSetpoint")},
                "after": {k: after.get(k) for k in ("mode", "heatSetpoint", "coolSetpoint")},
            }},
        )
        await db.climate_schedules.update_one(
            {"_id": schedule["_id"]},
            {"$set": {"last_run_at": now(), "last_status": status}},
        )
        return status
    except Exception as exc:
        await db.climate_schedule_runs.update_one(
            {"_id": occurrence_id},
            {"$set": {
                "status": "unknown",
                "finished_at": now(),
                "error": type(exc).__name__,
            }},
        )
        await db.climate_schedules.update_one(
            {"_id": schedule["_id"]},
            {"$set": {"last_run_at": now(), "last_status": "unknown"}},
        )
        return "unknown"
    finally:
        await db.climate_bindings.update_one(
            {"_id": binding["_id"], "command_lock": lock},
            {"$unset": {"busy_until": "", "command_lock": ""}},
        )


async def run_due_schedules(db, at=None):
    if os.getenv("CLIMATE_ENABLED") != "true":
        return {"due": 0, "executed": 0}
    at = at or now()
    due = executed = 0
    async for schedule in db.climate_schedules.find({"enabled": True}).limit(1000):
        try:
            tz = ZoneInfo(schedule.get("timezone") or DEFAULT_TZ)
            local = at.astimezone(tz)
            if local.weekday() not in schedule.get("days", []):
                continue
            if local.strftime("%H:%M") != schedule.get("time"):
                continue
            due += 1
            binding = await db.climate_bindings.find_one({"_id": schedule["device_id"]})
            if not binding:
                continue
            scheduled_for = local.replace(second=0, microsecond=0).astimezone(timezone.utc)
            status = await _execute_schedule(db, schedule, binding, scheduled_for)
            if status not in ("duplicate", "skipped_busy"):
                executed += 1
        except Exception:
            continue
    return {"due": due, "executed": executed}


def _avg(values):
    clean = [x for x in values if _finite(x)]
    return sum(clean) / len(clean) if clean else None


def _round(value, digits=1):
    return round(value, digits) if _finite(value) else None


async def analytics(db, device_id, range_name="24h"):
    if range_name not in RANGES:
        raise HTTPException(422, "climate_analytics_range_invalid")
    span, bucket_minutes = RANGES[range_name]
    start = now() - span
    docs = await db.climate_readings.find(
        {"device_id": device_id, "observed_at": {"$gte": start}}
    ).sort("observed_at", 1).limit(150000).to_list(150000)

    buckets = defaultdict(list)
    for doc in docs:
        ts = doc.get("observed_at")
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        seconds = int(ts.timestamp())
        width = bucket_minutes * 60
        key = datetime.fromtimestamp(seconds - seconds % width, tz=timezone.utc)
        buckets[key].append(doc)

    series = []
    for ts in sorted(buckets):
        rows = buckets[ts]
        mode_counts = Counter(r.get("mode") for r in rows if r.get("mode"))
        activity_counts = Counter(r.get("activity") for r in rows if r.get("activity"))
        series.append({
            "at": ts.isoformat(),
            "temperature": _round(_avg([r.get("temperature") for r in rows])),
            "temperature_min": _round(min([r["temperature"] for r in rows if _finite(r.get("temperature"))], default=None)),
            "temperature_max": _round(max([r["temperature"] for r in rows if _finite(r.get("temperature"))], default=None)),
            "humidity": _round(_avg([r.get("humidity") for r in rows])),
            "outdoorTemperature": _round(_avg([r.get("outdoorTemperature") for r in rows])),
            "heatSetpoint": _round(_avg([r.get("heatSetpoint") for r in rows])),
            "coolSetpoint": _round(_avg([r.get("coolSetpoint") for r in rows])),
            "mode": mode_counts.most_common(1)[0][0] if mode_counts else None,
            "activity": activity_counts.most_common(1)[0][0] if activity_counts else None,
        })

    temperatures = [d.get("temperature") for d in docs if _finite(d.get("temperature"))]
    humidity = [d.get("humidity") for d in docs if _finite(d.get("humidity"))]
    activities = Counter(d.get("activity") for d in docs if d.get("activity"))
    modes = [d.get("mode") for d in docs if d.get("mode")]
    transitions = sum(1 for a, b in zip(modes, modes[1:]) if a != b)
    target_errors = []
    for d in docs:
        temp = d.get("temperature")
        if not _finite(temp):
            continue
        target = d.get("heatSetpoint") if d.get("mode") == "Heat" else d.get("coolSetpoint") if d.get("mode") == "Cool" else None
        if _finite(target):
            target_errors.append(abs(temp - target))

    return {
        "range": range_name,
        "bucket_minutes": bucket_minutes,
        "sample_count": len(docs),
        "series": series,
        "summary": {
            "temperature_avg": _round(_avg(temperatures)),
            "temperature_min": _round(min(temperatures), 1) if temperatures else None,
            "temperature_max": _round(max(temperatures), 1) if temperatures else None,
            "humidity_avg": _round(_avg(humidity)),
            "heating_sample_pct": _round(activities.get("heating", 0) / len(docs) * 100, 1) if docs else 0,
            "cooling_sample_pct": _round(activities.get("cooling", 0) / len(docs) * 100, 1) if docs else 0,
            "idle_sample_pct": _round(activities.get("idle", 0) / len(docs) * 100, 1) if docs else 0,
            "mode_transitions": transitions,
            "mean_target_error": _round(_avg(target_errors)),
        },
    }


async def worker(db):
    interval = max(60, int(os.getenv("CLIMATE_SAMPLE_SECONDS", "300")))
    while True:
        try:
            if os.getenv("CLIMATE_TELEMETRY_ENABLED") == "true":
                await collect_all(db)
            if os.getenv("CLIMATE_AUTOMATION_ENABLED") == "true":
                await run_due_schedules(db)
        except Exception:
            pass
        await asyncio.sleep(interval)
