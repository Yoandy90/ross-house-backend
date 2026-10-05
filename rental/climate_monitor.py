"""Climate telemetry, schedules and analytics for Ross House.

The monitor is provider-neutral. It stores normalized device snapshots, records
state transitions, evaluates basic protection alerts and can execute Ross House
weekly schedules. Autonomous execution is opt-in and is never started in
staging because server.py's background-job policy exits before workers start.
"""
from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import climate_provider as provider
from .climate_policy import snapshot, validate_change


def now() -> datetime:
    return datetime.now(timezone.utc)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sample_minutes() -> int:
    try:
        value = int(os.getenv("CLIMATE_HISTORY_BUCKET_MINUTES", "5"))
    except ValueError:
        value = 5
    return max(1, min(value, 60))


def _floor_time(value: datetime, minutes: int) -> datetime:
    value = value.astimezone(timezone.utc)
    minute = value.minute - (value.minute % minutes)
    return value.replace(minute=minute, second=0, microsecond=0)


async def ensure_indexes(db):
    await db.climate_readings.create_index([("device_id", 1), ("observed_at", -1)])
    await db.climate_readings.create_index([("property_id", 1), ("observed_at", -1)])
    await db.climate_events.create_index([("device_id", 1), ("created_at", -1)])
    await db.climate_alert_state.create_index([("device_id", 1), ("active", 1), ("updated_at", -1)])
    await db.climate_schedules.create_index([("device_id", 1), ("enabled", 1)])
    await db.climate_schedule_runs.create_index([("device_id", 1), ("started_at", -1)])


def _reading_doc(binding: dict, state: dict, observed_at: datetime, source: str) -> dict:
    return {
        "device_id": binding["_id"],
        "property_id": binding.get("property_id", ""),
        "unit_id": binding.get("unit_id", ""),
        "name": binding.get("name", ""),
        "observed_at": observed_at,
        "source": source,
        "online": state.get("online") is True,
        "units": state.get("units"),
        "temperature": state.get("temperature"),
        "humidity": state.get("humidity"),
        "outdoor_temperature": state.get("outdoorTemperature"),
        "outdoor_humidity": state.get("outdoorHumidity"),
        "mode": state.get("mode"),
        "heat_setpoint": state.get("heatSetpoint"),
        "cool_setpoint": state.get("coolSetpoint"),
        "activity": state.get("activity"),
        "fan_mode": state.get("fanMode"),
        "fan_running": state.get("fanRunning"),
        "hold_status": state.get("holdStatus"),
    }


_EVENT_FIELDS = {
    "mode": "mode",
    "heat_setpoint": "heatSetpoint",
    "cool_setpoint": "coolSetpoint",
    "activity": "activity",
    "fan_mode": "fanMode",
    "fan_running": "fanRunning",
    "hold_status": "holdStatus",
}


async def _record_transitions(db, binding: dict, reading: dict):
    state_id = binding["_id"]
    previous = await db.climate_device_state.find_one({"_id": state_id})
    current_values = {field: reading.get(field) for field in _EVENT_FIELDS}
    await db.climate_device_state.update_one(
        {"_id": state_id},
        {"$set": {**current_values, "updated_at": reading["observed_at"]}},
        upsert=True,
    )
    if not previous:
        return
    events = []
    for stored, public_name in _EVENT_FIELDS.items():
        before, after = previous.get(stored), current_values.get(stored)
        if before != after and (before is not None or after is not None):
            events.append({
                "_id": str(uuid4()),
                "device_id": binding["_id"],
                "property_id": binding.get("property_id", ""),
                "unit_id": binding.get("unit_id", ""),
                "type": "state_change",
                "field": public_name,
                "before": before,
                "after": after,
                "created_at": reading["observed_at"],
            })
    if events:
        await db.climate_events.insert_many(events)


def _alert_conditions(state: dict) -> dict[str, tuple[bool, str, str]]:
    conditions: dict[str, tuple[bool, str, str]] = {}
    online = state.get("online") is True
    conditions["offline"] = (
        state.get("offline_alert") is True,
        "critical",
        "Thermostat is not reporting live data.",
    )
    units = state.get("units")
    temp = state.get("temperature")
    if _finite(temp) and units in ("Fahrenheit", "Celsius"):
        low = 50 if units == "Fahrenheit" else 10
        high = 85 if units == "Fahrenheit" else 29.5
        conditions["temperature_low"] = (
            temp <= low,
            "critical",
            "Indoor temperature is below the configured protection threshold.",
        )
        conditions["temperature_high"] = (
            temp >= high,
            "critical",
            "Indoor temperature is above the configured protection threshold.",
        )
    humidity = state.get("humidity")
    if _finite(humidity):
        conditions["humidity_low"] = (
            humidity <= 25,
            "warning",
            "Indoor humidity is unusually low.",
        )
        conditions["humidity_high"] = (
            humidity >= 65,
            "warning",
            "Indoor humidity is unusually high.",
        )
    return conditions


async def _evaluate_alerts(db, binding: dict, state: dict, observed_at: datetime):
    active_types = set()
    for alert_type, (active, severity, message) in _alert_conditions(state).items():
        alert_id = f"{binding['_id']}:{alert_type}"
        if active:
            active_types.add(alert_type)
            await db.climate_alert_state.update_one(
                {"_id": alert_id},
                {
                    "$set": {
                        "device_id": binding["_id"],
                        "property_id": binding.get("property_id", ""),
                        "unit_id": binding.get("unit_id", ""),
                        "type": alert_type,
                        "severity": severity,
                        "message": message,
                        "active": True,
                        "updated_at": observed_at,
                    },
                    "$setOnInsert": {"triggered_at": observed_at},
                    "$unset": {"resolved_at": ""},
                },
                upsert=True,
            )
        else:
            current = await db.climate_alert_state.find_one({"_id": alert_id, "active": True})
            if current:
                await db.climate_alert_state.update_one(
                    {"_id": alert_id},
                    {"$set": {"active": False, "resolved_at": observed_at, "updated_at": observed_at}},
                )
                await db.climate_events.insert_one({
                    "_id": str(uuid4()),
                    "device_id": binding["_id"],
                    "property_id": binding.get("property_id", ""),
                    "unit_id": binding.get("unit_id", ""),
                    "type": "alert_resolved",
                    "field": alert_type,
                    "created_at": observed_at,
                })


async def record_snapshot(db, binding: dict, state: dict, source: str = "read"):
    observed_at = now()
    if state.get("online") is True:
        state = {**state, "offline_alert": False}
        await db.climate_device_health.delete_one({"_id": binding["_id"]})
    elif "offline_alert" not in state:
        health = await db.climate_device_health.find_one({"_id": binding["_id"]})
        first = health.get("first_failure_at") if health else observed_at
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        failures = int((health or {}).get("failure_count") or 0) + 1
        state = {
            **state,
            "offline_alert": failures >= 3 or observed_at - first >= timedelta(minutes=15),
        }
        await db.climate_device_health.update_one(
            {"_id": binding["_id"]},
            {"$set": {
                "first_failure_at": first,
                "last_failure_at": observed_at,
                "failure_count": failures,
            }},
            upsert=True,
        )
    reading = _reading_doc(binding, state, observed_at, source)
    bucket = _floor_time(observed_at, _sample_minutes())
    reading["_id"] = f"{binding['_id']}:{bucket.isoformat()}"
    reading["bucket_at"] = bucket
    await db.climate_readings.replace_one({"_id": reading["_id"]}, reading, upsert=True)
    await _record_transitions(db, binding, reading)
    await _evaluate_alerts(db, binding, state, observed_at)
    return reading


async def record_unavailable(db, binding: dict, source: str = "read"):
    observed_at = now()
    health = await db.climate_device_health.find_one({"_id": binding["_id"]})
    first = health.get("first_failure_at") if health else observed_at
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    failures = int((health or {}).get("failure_count") or 0) + 1
    offline_alert = failures >= 3 or observed_at - first >= timedelta(minutes=15)
    await db.climate_device_health.update_one(
        {"_id": binding["_id"]},
        {"$set": {
            "first_failure_at": first,
            "last_failure_at": observed_at,
            "failure_count": failures,
        }},
        upsert=True,
    )
    state = {
        "online": False,
        "offline_alert": offline_alert,
        "units": None,
        "temperature": None,
        "humidity": None,
        "outdoorTemperature": None,
        "outdoorHumidity": None,
        "mode": None,
        "heatSetpoint": None,
        "coolSetpoint": None,
        "activity": None,
        "fanMode": None,
        "fanRunning": None,
        "holdStatus": None,
    }
    return await record_snapshot(db, binding, state, source)


_RANGE_MAP = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "1y": timedelta(days=365),
    "5y": timedelta(days=365 * 5),
}


def _bucket_for_range(range_name: str):
    if range_name == "24h":
        return ("minute", 15)
    if range_name == "7d":
        return ("hour", 1)
    if range_name == "30d":
        return ("hour", 4)
    if range_name == "90d":
        return ("day", 1)
    if range_name == "1y":
        return ("day", 1)
    if range_name == "5y":
        return ("week", 1)
    return ("month", 1)


async def analytics(db, device_id: str, range_name: str = "7d") -> dict:
    if range_name not in {*_RANGE_MAP.keys(), "all"}:
        raise HTTPException(422, "climate_analytics_range_invalid")
    start = now() - _RANGE_MAP[range_name] if range_name != "all" else datetime(2020, 1, 1, tzinfo=timezone.utc)
    unit, bin_size = _bucket_for_range(range_name)
    match = {"device_id": device_id, "observed_at": {"$gte": start}}
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "$dateTrunc": {
                        "date": "$observed_at",
                        "unit": unit,
                        "binSize": bin_size,
                        "timezone": "UTC",
                    }
                },
                "temperature": {"$avg": "$temperature"},
                "temperature_min": {"$min": "$temperature"},
                "temperature_max": {"$max": "$temperature"},
                "humidity": {"$avg": "$humidity"},
                "outdoor_temperature": {"$avg": "$outdoor_temperature"},
                "heat_setpoint": {"$avg": "$heat_setpoint"},
                "cool_setpoint": {"$avg": "$cool_setpoint"},
                "heating_samples": {"$sum": {"$cond": [{"$eq": ["$activity", "heating"]}, 1, 0]}},
                "cooling_samples": {"$sum": {"$cond": [{"$eq": ["$activity", "cooling"]}, 1, 0]}},
                "samples": {"$sum": 1},
                "mode": {"$last": "$mode"},
                "activity": {"$last": "$activity"},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    rows = await db.climate_readings.aggregate(pipeline).to_list(5000)
    points = []
    total_samples = heating = cooling = 0
    for row in rows:
        count = int(row.get("samples") or 0)
        total_samples += count
        heating += int(row.get("heating_samples") or 0)
        cooling += int(row.get("cooling_samples") or 0)
        points.append({
            "at": row["_id"].isoformat(),
            "temperature": row.get("temperature"),
            "temperature_min": row.get("temperature_min"),
            "temperature_max": row.get("temperature_max"),
            "humidity": row.get("humidity"),
            "outdoor_temperature": row.get("outdoor_temperature"),
            "heat_setpoint": row.get("heat_setpoint"),
            "cool_setpoint": row.get("cool_setpoint"),
            "mode": row.get("mode"),
            "activity": row.get("activity"),
            "samples": count,
        })
    summary_rows = await db.climate_readings.aggregate([
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "temperature_avg": {"$avg": "$temperature"},
                "temperature_min": {"$min": "$temperature"},
                "temperature_max": {"$max": "$temperature"},
                "humidity_avg": {"$avg": "$humidity"},
                "outdoor_avg": {"$avg": "$outdoor_temperature"},
                "samples": {"$sum": 1},
            }
        },
    ]).to_list(1)
    summary = summary_rows[0] if summary_rows else {}
    events = await db.climate_events.find(
        {"device_id": device_id, "created_at": {"$gte": start}},
        {"_id": 0},
    ).sort("created_at", -1).limit(200).to_list(200)
    for event in events:
        if isinstance(event.get("created_at"), datetime):
            event["created_at"] = event["created_at"].isoformat()
    return {
        "range": range_name,
        "bucket": {"unit": unit, "size": bin_size},
        "summary": {
            "temperature_avg": summary.get("temperature_avg"),
            "temperature_min": summary.get("temperature_min"),
            "temperature_max": summary.get("temperature_max"),
            "humidity_avg": summary.get("humidity_avg"),
            "outdoor_avg": summary.get("outdoor_avg"),
            "samples": int(summary.get("samples") or 0),
            "heating_pct": round(heating / total_samples * 100, 1) if total_samples else 0,
            "cooling_pct": round(cooling / total_samples * 100, 1) if total_samples else 0,
        },
        "points": points,
        "events": events,
    }


async def nearest_reading(db, device_id: str, at_value: str) -> dict:
    try:
        target = datetime.fromisoformat(at_value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "climate_reading_time_invalid")
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    else:
        target = target.astimezone(timezone.utc)
    before = await db.climate_readings.find_one(
        {"device_id": device_id, "observed_at": {"$lte": target}},
        sort=[("observed_at", -1)],
    )
    after = await db.climate_readings.find_one(
        {"device_id": device_id, "observed_at": {"$gte": target}},
        sort=[("observed_at", 1)],
    )
    candidates = [row for row in (before, after) if row]
    if not candidates:
        raise HTTPException(404, "climate_reading_not_found")
    row = min(
        candidates,
        key=lambda item: abs((item["observed_at"].replace(tzinfo=timezone.utc)
                              if item["observed_at"].tzinfo is None
                              else item["observed_at"].astimezone(timezone.utc)) - target),
    )
    return {
        "at": row["observed_at"].isoformat(),
        "distance_seconds": abs((row["observed_at"].replace(tzinfo=timezone.utc)
                                 if row["observed_at"].tzinfo is None
                                 else row["observed_at"].astimezone(timezone.utc)) - target).total_seconds(),
        "online": row.get("online"),
        "units": row.get("units"),
        "temperature": row.get("temperature"),
        "humidity": row.get("humidity"),
        "outdoor_temperature": row.get("outdoor_temperature"),
        "outdoor_humidity": row.get("outdoor_humidity"),
        "mode": row.get("mode"),
        "heat_setpoint": row.get("heat_setpoint"),
        "cool_setpoint": row.get("cool_setpoint"),
        "activity": row.get("activity"),
        "fan_mode": row.get("fan_mode"),
        "fan_running": row.get("fan_running"),
        "hold_status": row.get("hold_status"),
        "source": row.get("source"),
    }


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise HTTPException(422, "climate_timezone_invalid")
    return name


def validate_period(period: dict, raw: dict | None = None):
    days = period.get("days") or []
    if not days or any(not isinstance(day, int) or day < 0 or day > 6 for day in days):
        raise HTTPException(422, "climate_schedule_days_invalid")
    time_value = str(period.get("time") or "")
    try:
        hour, minute = [int(x) for x in time_value.split(":", 1)]
    except (ValueError, TypeError):
        raise HTTPException(422, "climate_schedule_time_invalid")
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise HTTPException(422, "climate_schedule_time_invalid")
    mode = period.get("mode")
    if mode not in ("Off", "Heat", "Cool", "Auto"):
        raise HTTPException(422, "climate_mode_unsupported")
    command = {"mode": mode}
    if mode in ("Heat", "Auto") and period.get("heatSetpoint") is not None:
        command["heatSetpoint"] = period["heatSetpoint"]
    if mode in ("Cool", "Auto") and period.get("coolSetpoint") is not None:
        command["coolSetpoint"] = period["coolSetpoint"]
    if mode == "Heat" and "heatSetpoint" not in command:
        raise HTTPException(422, "climate_schedule_temperature_required")
    if mode == "Cool" and "coolSetpoint" not in command:
        raise HTTPException(422, "climate_schedule_temperature_required")
    if mode == "Auto" and ("heatSetpoint" not in command or "coolSetpoint" not in command):
        raise HTTPException(422, "climate_schedule_temperature_required")
    if raw is not None:
        validate_change(raw, command)
    return command


def _validate_schedule_periods(periods: list[dict]):
    if not periods or len(periods) > 56:
        raise HTTPException(422, "climate_schedule_periods_invalid")
    seen: set[tuple[int, str]] = set()
    for period in periods:
        validate_period(period)
        for day in period.get("days") or []:
            key = (day, period.get("time"))
            if key in seen:
                raise HTTPException(422, "climate_schedule_overlap")
            seen.add(key)


async def create_schedule(db, binding: dict, actor: str, payload: dict):
    timezone_name = validate_timezone(payload.get("timezone") or "America/Chicago")
    periods = payload.get("periods") or []
    _validate_schedule_periods(periods)
    schedule_id = str(uuid4())
    doc = {
        "_id": schedule_id,
        "device_id": binding["_id"],
        "property_id": binding.get("property_id", ""),
        "unit_id": binding.get("unit_id", ""),
        "name": str(payload.get("name") or "Weekly schedule")[:80],
        "enabled": bool(payload.get("enabled", True)),
        "timezone": timezone_name,
        "periods": periods,
        "created_by": actor,
        "created_at": now(),
        "updated_at": now(),
    }
    await db.climate_schedules.insert_one(doc)
    return doc


async def update_schedule(db, binding: dict, schedule_id: str, actor: str, payload: dict):
    timezone_name = validate_timezone(payload.get("timezone") or "America/Chicago")
    periods = payload.get("periods") or []
    if not periods or len(periods) > 56:
        raise HTTPException(422, "climate_schedule_periods_invalid")
    for period in periods:
        validate_period(period)
    result = await db.climate_schedules.find_one_and_update(
        {"_id": schedule_id, "device_id": binding["_id"]},
        {"$set": {
            "name": str(payload.get("name") or "Weekly schedule")[:80],
            "enabled": bool(payload.get("enabled", True)),
            "timezone": timezone_name,
            "periods": periods,
            "updated_by": actor,
            "updated_at": now(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(404, "climate_schedule_not_found")
    return result


def public_schedule(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "device_id": doc["device_id"],
        "name": doc.get("name", ""),
        "enabled": doc.get("enabled", False),
        "timezone": doc.get("timezone", "America/Chicago"),
        "periods": doc.get("periods", []),
        "created_at": doc.get("created_at").isoformat() if isinstance(doc.get("created_at"), datetime) else None,
        "updated_at": doc.get("updated_at").isoformat() if isinstance(doc.get("updated_at"), datetime) else None,
    }


async def list_schedules(db, binding: dict):
    docs = await db.climate_schedules.find({"device_id": binding["_id"]}).sort("created_at", 1).to_list(20)
    return [public_schedule(doc) for doc in docs]


async def delete_schedule(db, binding: dict, schedule_id: str):
    result = await db.climate_schedules.delete_one({"_id": schedule_id, "device_id": binding["_id"]})
    if not result.deleted_count:
        raise HTTPException(404, "climate_schedule_not_found")


async def active_alerts(db, binding: dict):
    rows = await db.climate_alert_state.find(
        {"device_id": binding["_id"], "active": True},
        {"_id": 0},
    ).sort("updated_at", -1).to_list(50)
    for row in rows:
        for field in ("triggered_at", "updated_at"):
            if isinstance(row.get(field), datetime):
                row[field] = row[field].isoformat()
    return rows


async def _execute_schedule_period(db, binding: dict, schedule: dict, period: dict, run_key: str, actor: str):
    previous = await db.climate_schedule_runs.find_one({"_id": run_key})
    if previous:
        return previous.get("status", "pending")
    lock_key = "schedule:" + run_key
    locked = await db.climate_bindings.find_one_and_update(
        {"_id": binding["_id"], "$or": [
            {"busy_until": {"$exists": False}},
            {"busy_until": {"$lt": now()}},
        ]},
        {"$set": {"busy_until": now() + timedelta(seconds=180), "command_lock": lock_key}},
    )
    if not locked:
        return "busy"
    status = "unknown"
    try:
        try:
            await db.climate_schedule_runs.insert_one({
                "_id": run_key,
                "schedule_id": schedule["_id"],
                "device_id": binding["_id"],
                "period": period,
                "started_at": now(),
                "status": "pending",
                "actor": actor,
            })
        except DuplicateKeyError:
            previous = await db.climate_schedule_runs.find_one({"_id": run_key})
            status = (previous or {}).get("status", "pending")
            return status
        raw = await provider.device(db, binding)
        command = validate_period(period, raw)
        payload = validate_change(raw, command)
        await provider.change(db, binding, payload)
        observed_raw = await provider.device(db, binding)
        observed = snapshot(observed_raw)
        expected = command
        confirmed = (
            observed.get("mode") == expected.get("mode")
            and (
                "heatSetpoint" not in expected
                or observed.get("heatSetpoint") == expected.get("heatSetpoint")
            )
            and (
                "coolSetpoint" not in expected
                or observed.get("coolSetpoint") == expected.get("coolSetpoint")
            )
        )
        status = "confirmed" if confirmed else "pending"
        await record_snapshot(db, binding, observed, source="schedule")
    except HTTPException:
        status = "unknown"
    finally:
        await db.climate_schedule_runs.update_one(
            {"_id": run_key},
            {"$set": {"status": status, "finished_at": now()}},
        )
        await db.climate_bindings.update_one(
            {"_id": binding["_id"], "command_lock": lock_key},
            {"$unset": {"busy_until": "", "command_lock": ""}},
        )
    return status


async def run_schedule_now(db, binding: dict, schedule_id: str, period_index: int, actor: str):
    schedule = await db.climate_schedules.find_one({"_id": schedule_id, "device_id": binding["_id"]})
    if not schedule:
        raise HTTPException(404, "climate_schedule_not_found")
    periods = schedule.get("periods") or []
    if period_index < 0 or period_index >= len(periods):
        raise HTTPException(422, "climate_schedule_period_invalid")
    run_key = f"manual:{schedule_id}:{period_index}:{uuid4()}"
    return await _execute_schedule_period(db, binding, schedule, periods[period_index], run_key, actor)


async def run_due_schedules(db):
    current_utc = now()
    schedules = await db.climate_schedules.find({"enabled": True}).limit(500).to_list(500)
    for schedule in schedules:
        try:
            tz = ZoneInfo(schedule.get("timezone") or "America/Chicago")
        except ZoneInfoNotFoundError:
            continue
        local = current_utc.astimezone(tz)
        for index, period in enumerate(schedule.get("periods") or []):
            if local.weekday() not in period.get("days", []):
                continue
            try:
                scheduled_hour, scheduled_minute = [int(x) for x in period.get("time", "").split(":", 1)]
                scheduled_local = local.replace(
                    hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0
                )
            except (TypeError, ValueError):
                continue
            delay = local - scheduled_local
            due_window = timedelta(minutes=max(10, _sample_minutes() * 2))
            if delay < timedelta(0) or delay >= due_window:
                continue
            run_key = f"{schedule['_id']}:{index}:{local.date().isoformat()}:{period.get('time')}"
            binding = await db.climate_bindings.find_one({"_id": schedule["device_id"]})
            if binding:
                await _execute_schedule_period(db, binding, schedule, period, run_key, "climate_scheduler")


async def sample_all(db):
    bindings = await db.climate_bindings.find({}).limit(500).to_list(500)
    for binding in bindings:
        try:
            raw = await provider.device(db, binding)
            await record_snapshot(db, binding, snapshot(raw), source="monitor")
        except HTTPException:
            await record_unavailable(db, binding, source="monitor")


def monitor_enabled() -> bool:
    return (
        os.getenv("CLIMATE_ENABLED") == "true"
        and os.getenv("CLIMATE_MONITOR_ENABLED") == "true"
    )


async def monitor_loop(db):
    """Production worker: sample devices and execute due Ross House schedules."""
    interval = max(60, min(int(os.getenv("CLIMATE_MONITOR_INTERVAL_SECONDS", "300")), 3600))
    while True:
        try:
            await sample_all(db)
            await run_due_schedules(db)
        except Exception:
            # Worker errors must not terminate the API; the next interval retries the
            # read loop but physical commands themselves remain idempotent by run key.
            pass
        await asyncio.sleep(interval)
