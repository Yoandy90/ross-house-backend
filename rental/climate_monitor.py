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
from . import climate_intelligence
from . import weather_intelligence


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
    await db.climate_alert_rules.create_index([("property_id", 1)])
    await db.climate_alert_deliveries.create_index([("device_id", 1), ("created_at", -1)])
    await db.climate_maintenance.create_index([("property_id", 1)])
    await weather_intelligence.ensure_indexes(db)


def _reading_doc(binding: dict, state: dict, observed_at: datetime, source: str, weather: dict | None = None) -> dict:
    weather_context = weather_intelligence.reading_context(state, weather)
    provider_outdoor = state.get("outdoorTemperature")
    nws_f = weather_context.get("nws_temperature")
    nws_native = None
    if _finite(nws_f):
        nws_native = nws_f if state.get("units") == "Fahrenheit" else (nws_f - 32) * 5 / 9
    outdoor_temperature = provider_outdoor if _finite(provider_outdoor) else nws_native
    outdoor_humidity = state.get("outdoorHumidity")
    if not _finite(outdoor_humidity):
        outdoor_humidity = weather_context.get("nws_humidity")
    outdoor_source = (
        "thermostat"
        if _finite(provider_outdoor)
        else "nws"
        if _finite(nws_f)
        else None
    )
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
        "outdoor_temperature": outdoor_temperature,
        "outdoor_humidity": outdoor_humidity,
        "outdoor_source": outdoor_source,
        "nws_temperature_f": nws_f,
        "nws_humidity": weather_context.get("nws_humidity"),
        "nws_wind_speed_mph": weather_context.get("nws_wind_speed_mph"),
        "nws_wind_gust_mph": weather_context.get("nws_wind_gust_mph"),
        "nws_condition": weather_context.get("nws_condition"),
        "nws_observed_at": weather_context.get("nws_observed_at"),
        "nws_station": weather_context.get("nws_station"),
        "nws_alert_count": weather_context.get("nws_alert_count"),
        "nws_forecast_min_12h": weather_context.get("nws_forecast_min_12h"),
        "nws_forecast_max_12h": weather_context.get("nws_forecast_max_12h"),
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
                "source": reading.get("source"),
                "created_at": reading["observed_at"],
            })
    if events:
        await db.climate_events.insert_many(events)


def _alert_conditions(state: dict, rules: dict | None = None) -> dict[str, tuple[bool, str, str]]:
    return climate_intelligence.basic_conditions(
        state,
        rules or climate_intelligence.DEFAULT_RULES,
    )


async def _evaluate_alerts(db, binding: dict, state: dict, observed_at: datetime, weather: dict | None = None):
    rules = await climate_intelligence.get_rules(db, binding)
    conditions = _alert_conditions(state, rules)
    try:
        conditions.update(
            await climate_intelligence.predictive_conditions(
                db,
                binding,
                state,
                rules,
                sample_minutes=_sample_minutes(),
            )
        )
    except Exception:
        # Predictive heuristics must never block raw telemetry collection.
        pass
    try:
        conditions.update(weather_intelligence.alert_conditions(state, weather))
    except Exception:
        # Official-weather context is advisory and must never block HVAC telemetry.
        pass

    for alert_type, (active, severity, message) in conditions.items():
        alert_id = f"{binding['_id']}:{alert_type}"
        current = await db.climate_alert_state.find_one({"_id": alert_id})
        was_active = bool(current and current.get("active") is True)
        if active:
            triggered_at = (
                current.get("triggered_at")
                if was_active and isinstance(current.get("triggered_at"), datetime)
                else observed_at
            )
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
                        "triggered_at": triggered_at,
                        "updated_at": observed_at,
                    },
                    "$unset": {"resolved_at": ""},
                },
                upsert=True,
            )
            if not was_active:
                await db.climate_events.insert_one({
                    "_id": str(uuid4()),
                    "device_id": binding["_id"],
                    "property_id": binding.get("property_id", ""),
                    "unit_id": binding.get("unit_id", ""),
                    "type": "alert_triggered",
                    "field": alert_type,
                    "severity": severity,
                    "created_at": observed_at,
                })
                try:
                    from .climate_notifications import notify_transition
                    await notify_transition(
                        db, binding, alert_type, severity, True, observed_at
                    )
                except Exception:
                    pass
        elif was_active:
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
            try:
                from .climate_notifications import notify_transition
                await notify_transition(
                    db,
                    binding,
                    alert_type,
                    current.get("severity") or severity,
                    False,
                    observed_at,
                )
            except Exception:
                pass


async def record_snapshot(db, binding: dict, state: dict, source: str = "read"):
    observed_at = now()
    weather = None
    if weather_intelligence.enabled():
        try:
            weather = await weather_intelligence.weather_for_binding(db, binding)
        except Exception:
            weather = None
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
    reading = _reading_doc(binding, state, observed_at, source, weather)
    bucket = _floor_time(observed_at, _sample_minutes())
    reading["_id"] = f"{binding['_id']}:{bucket.isoformat()}"
    reading["bucket_at"] = bucket
    await db.climate_readings.replace_one({"_id": reading["_id"]}, reading, upsert=True)
    await _record_transitions(db, binding, reading)
    await _evaluate_alerts(db, binding, state, observed_at, weather)
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
                "nws_temperature_f": {"$avg": "$nws_temperature_f"},
                "nws_wind_speed_mph": {"$avg": "$nws_wind_speed_mph"},
                "nws_alert_count": {"$max": "$nws_alert_count"},
                "outdoor_source": {"$last": "$outdoor_source"},
                "heat_setpoint": {"$avg": "$heat_setpoint"},
                "cool_setpoint": {"$avg": "$cool_setpoint"},
                "heating_samples": {"$sum": {"$cond": [{"$eq": ["$activity", "heating"]}, 1, 0]}},
                "cooling_samples": {"$sum": {"$cond": [{"$eq": ["$activity", "cooling"]}, 1, 0]}},
                "fan_only_samples": {"$sum": {"$cond": [
                    {"$and": [
                        {"$eq": ["$fan_running", True]},
                        {"$eq": ["$activity", "idle"]},
                    ]},
                    1,
                    0,
                ]}},
                "samples": {"$sum": 1},
                "mode": {"$last": "$mode"},
                "activity": {"$last": "$activity"},
            }
        },
        {"$sort": {"_id": 1}},
    ]
    rows = await db.climate_readings.aggregate(pipeline).to_list(5000)
    points = []
    total_samples = heating = cooling = fan_only = 0
    for row in rows:
        count = int(row.get("samples") or 0)
        total_samples += count
        heating += int(row.get("heating_samples") or 0)
        cooling += int(row.get("cooling_samples") or 0)
        fan_only += int(row.get("fan_only_samples") or 0)
        points.append({
            "at": row["_id"].isoformat(),
            "temperature": row.get("temperature"),
            "temperature_min": row.get("temperature_min"),
            "temperature_max": row.get("temperature_max"),
            "humidity": row.get("humidity"),
            "outdoor_temperature": row.get("outdoor_temperature"),
            "nws_temperature_f": row.get("nws_temperature_f"),
            "nws_wind_speed_mph": row.get("nws_wind_speed_mph"),
            "nws_alert_count": row.get("nws_alert_count"),
            "outdoor_source": row.get("outdoor_source"),
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
                "humidity_min": {"$min": "$humidity"},
                "humidity_max": {"$max": "$humidity"},
                "outdoor_avg": {"$avg": "$outdoor_temperature"},
                "outdoor_min": {"$min": "$outdoor_temperature"},
                "outdoor_max": {"$max": "$outdoor_temperature"},
                "nws_wind_speed_avg_mph": {"$avg": "$nws_wind_speed_mph"},
                "weather_samples": {"$sum": {"$cond": [{"$eq": ["$outdoor_source", "nws"]}, 1, 0]}},
                "units": {"$last": "$units"},
                "samples": {"$sum": 1},
            }
        },
    ]).to_list(1)
    summary = summary_rows[0] if summary_rows else {}

    activity_events = await db.climate_events.find(
        {
            "device_id": device_id,
            "created_at": {"$gte": start},
            "type": "state_change",
            "field": {"$in": ["activity", "mode"]},
        },
        {"_id": 0},
    ).sort("created_at", 1).limit(10000).to_list(10000)
    heating_cycles = sum(
        1 for event in activity_events
        if event.get("field") == "activity" and event.get("after") == "heating"
    )
    cooling_cycles = sum(
        1 for event in activity_events
        if event.get("field") == "activity" and event.get("after") == "cooling"
    )
    mode_changes = sum(1 for event in activity_events if event.get("field") == "mode")

    sample_minutes = _sample_minutes()
    heating_minutes = heating * sample_minutes
    cooling_minutes = cooling * sample_minutes

    target_errors = []
    comfort_samples = 0
    heating_degree_hours = 0.0
    cooling_degree_hours = 0.0
    for point in points:
        temp = point.get("temperature")
        mode = point.get("mode")
        if not _finite(temp):
            continue
        error = None
        if mode == "Heat" and _finite(point.get("heat_setpoint")):
            error = abs(temp - point["heat_setpoint"])
        elif mode == "Cool" and _finite(point.get("cool_setpoint")):
            error = abs(temp - point["cool_setpoint"])
        elif mode == "Auto" and _finite(point.get("heat_setpoint")) and _finite(point.get("cool_setpoint")):
            if temp < point["heat_setpoint"]:
                error = point["heat_setpoint"] - temp
            elif temp > point["cool_setpoint"]:
                error = temp - point["cool_setpoint"]
            else:
                error = 0
        if error is not None:
            target_errors.append(error)
            tolerance = 2 if summary.get("units") == "Fahrenheit" else 1.1
            if error <= tolerance:
                comfort_samples += 1
        outdoor = point.get("outdoor_temperature")
        sample_hours = _sample_minutes() / 60
        if _finite(outdoor):
            if _finite(point.get("heat_setpoint")):
                heating_degree_hours += max(0, point["heat_setpoint"] - outdoor) * sample_hours
            if _finite(point.get("cool_setpoint")):
                cooling_degree_hours += max(0, outdoor - point["cool_setpoint"]) * sample_hours

    heat_rates = []
    cool_rates = []
    idle_drift_rates = []
    indoor_outdoor_deltas = []
    for first, second in zip(points, points[1:]):
        try:
            t1 = datetime.fromisoformat(first["at"])
            t2 = datetime.fromisoformat(second["at"])
        except (ValueError, TypeError):
            continue
        hours = (t2 - t1).total_seconds() / 3600
        if hours <= 0 or not _finite(first.get("temperature")) or not _finite(second.get("temperature")):
            continue
        delta = second["temperature"] - first["temperature"]
        if first.get("activity") == second.get("activity") == "heating" and delta > 0:
            heat_rates.append(delta / hours)
        elif first.get("activity") == second.get("activity") == "cooling" and delta < 0:
            cool_rates.append(-delta / hours)
        elif first.get("activity") == second.get("activity") == "idle":
            idle_drift_rates.append(abs(delta) / hours)
        if _finite(first.get("outdoor_temperature")):
            indoor_outdoor_deltas.append(abs(first["temperature"] - first["outdoor_temperature"]))

    def mean_or_none(values):
        return sum(values) / len(values) if values else None

    energy = await db.climate_energy_config.find_one({"_id": device_id}) or {}
    heating_minutes_est = heating * _sample_minutes()
    cooling_minutes_est = cooling * _sample_minutes()
    fan_only_minutes_est = fan_only * _sample_minutes()
    energy_kwh = None
    energy_cost = None
    if all(_finite(energy.get(key)) for key in ("heat_kw", "cool_kw", "fan_kw", "electric_rate")):
        energy_kwh = (
            heating_minutes_est / 60 * energy["heat_kw"]
            + cooling_minutes_est / 60 * energy["cool_kw"]
            + fan_only_minutes_est / 60 * energy["fan_kw"]
        )
        energy_cost = energy_kwh * energy["electric_rate"]

    events = await db.climate_events.find(
        {"device_id": device_id, "created_at": {"$gte": start}},
        {"_id": 0},
    ).sort("created_at", -1).limit(200).to_list(200)
    for event in events:
        if isinstance(event.get("created_at"), datetime):
            event["created_at"] = event["created_at"].isoformat()

    temperature_span = None
    if _finite(summary.get("temperature_min")) and _finite(summary.get("temperature_max")):
        temperature_span = summary["temperature_max"] - summary["temperature_min"]

    return {
        "range": range_name,
        "bucket": {"unit": unit, "size": bin_size},
        "summary": {
            "temperature_avg": summary.get("temperature_avg"),
            "temperature_min": summary.get("temperature_min"),
            "temperature_max": summary.get("temperature_max"),
            "temperature_span": temperature_span,
            "humidity_avg": summary.get("humidity_avg"),
            "humidity_min": summary.get("humidity_min"),
            "humidity_max": summary.get("humidity_max"),
            "outdoor_avg": summary.get("outdoor_avg"),
            "outdoor_min": summary.get("outdoor_min"),
            "outdoor_max": summary.get("outdoor_max"),
            "nws_wind_speed_avg_mph": summary.get("nws_wind_speed_avg_mph"),
            "weather_samples": int(summary.get("weather_samples") or 0),
            "heating_degree_hours": round(heating_degree_hours, 2),
            "cooling_degree_hours": round(cooling_degree_hours, 2),
            "heating_runtime_minutes_per_degree_hour": round(heating_minutes_est / heating_degree_hours, 3) if heating_degree_hours > 0 else None,
            "cooling_runtime_minutes_per_degree_hour": round(cooling_minutes_est / cooling_degree_hours, 3) if cooling_degree_hours > 0 else None,
            "units": summary.get("units"),
            "samples": int(summary.get("samples") or 0),
            "heating_pct": round(heating / total_samples * 100, 1) if total_samples else 0,
            "cooling_pct": round(cooling / total_samples * 100, 1) if total_samples else 0,
            "heating_minutes_est": heating_minutes_est,
            "cooling_minutes_est": cooling_minutes_est,
            "fan_only_minutes_est": fan_only_minutes_est,
            "heating_cycles": heating_cycles,
            "cooling_cycles": cooling_cycles,
            "mode_changes": mode_changes,
            "mean_target_error": round(sum(target_errors) / len(target_errors), 2) if target_errors else None,
            "comfort_pct": round(comfort_samples / len(target_errors) * 100, 1) if target_errors else None,
            "heating_recovery_rate_per_hour": round(mean_or_none(heat_rates), 3) if heat_rates else None,
            "cooling_recovery_rate_per_hour": round(mean_or_none(cool_rates), 3) if cool_rates else None,
            "idle_drift_rate_per_hour": round(mean_or_none(idle_drift_rates), 3) if idle_drift_rates else None,
            "indoor_outdoor_delta_avg": round(mean_or_none(indoor_outdoor_deltas), 2) if indoor_outdoor_deltas else None,
            "energy_estimate_kwh": round(energy_kwh, 2) if _finite(energy_kwh) else None,
            "energy_estimate_cost": round(energy_cost, 2) if _finite(energy_cost) else None,
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
        "outdoor_source": row.get("outdoor_source"),
        "nws_temperature_f": row.get("nws_temperature_f"),
        "nws_wind_speed_mph": row.get("nws_wind_speed_mph"),
        "nws_wind_gust_mph": row.get("nws_wind_gust_mph"),
        "nws_condition": row.get("nws_condition"),
        "nws_alert_count": row.get("nws_alert_count"),
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
    if mode not in ("Off", "Heat", "Cool", "Auto", "EmergencyHeat"):
        raise HTTPException(422, "climate_mode_unsupported")
    command = {"mode": mode}
    if mode in ("Heat", "Auto", "EmergencyHeat") and period.get("heatSetpoint") is not None:
        command["heatSetpoint"] = period["heatSetpoint"]
    if mode in ("Cool", "Auto") and period.get("coolSetpoint") is not None:
        command["coolSetpoint"] = period["coolSetpoint"]
    if mode in ("Heat", "EmergencyHeat") and "heatSetpoint" not in command:
        raise HTTPException(422, "climate_schedule_temperature_required")
    if mode == "Cool" and "coolSetpoint" not in command:
        raise HTTPException(422, "climate_schedule_temperature_required")
    if mode == "Auto" and ("heatSetpoint" not in command or "coolSetpoint" not in command):
        raise HTTPException(422, "climate_schedule_temperature_required")
    if raw is not None:
        validate_change(raw, command)
    return command


def _validate_schedule_periods(periods: list[dict], raw: dict | None = None):
    if not periods or len(periods) > 56:
        raise HTTPException(422, "climate_schedule_periods_invalid")
    seen: set[tuple[int, str]] = set()
    for period in periods:
        validate_period(period, raw)
        for day in period.get("days") or []:
            key = (day, period.get("time"))
            if key in seen:
                raise HTTPException(422, "climate_schedule_overlap")
            seen.add(key)


async def _ensure_no_schedule_conflicts(db, binding: dict, periods: list[dict], exclude_id: str | None = None):
    desired = {
        (day, period.get("time"))
        for period in periods
        for day in (period.get("days") or [])
    }
    query = {"device_id": binding["_id"], "enabled": True}
    if exclude_id:
        query["_id"] = {"$ne": exclude_id}
    async for schedule in db.climate_schedules.find(query, {"periods": 1}):
        existing = {
            (day, period.get("time"))
            for period in (schedule.get("periods") or [])
            for day in (period.get("days") or [])
        }
        if desired & existing:
            raise HTTPException(409, "climate_schedule_conflict")


async def create_schedule(db, binding: dict, actor: str, payload: dict):
    timezone_name = validate_timezone(payload.get("timezone") or "America/Chicago")
    periods = payload.get("periods") or []
    raw = await provider.device(db, binding)
    _validate_schedule_periods(periods, raw)
    if bool(payload.get("enabled", True)):
        await _ensure_no_schedule_conflicts(db, binding, periods)
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
    raw = await provider.device(db, binding)
    _validate_schedule_periods(periods, raw)
    if bool(payload.get("enabled", True)):
        await _ensure_no_schedule_conflicts(db, binding, periods, exclude_id=schedule_id)
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
                if binding.get("ross_schedule_paused") is True:
                    try:
                        await db.climate_schedule_runs.insert_one({
                            "_id": run_key,
                            "schedule_id": schedule["_id"],
                            "device_id": binding["_id"],
                            "period": period,
                            "started_at": now(),
                            "finished_at": now(),
                            "status": "skipped_hold",
                            "actor": "climate_scheduler",
                        })
                    except DuplicateKeyError:
                        pass
                    continue
                await _execute_schedule_period(db, binding, schedule, period, run_key, "climate_scheduler")


async def sample_all(db):
    bindings = await db.climate_bindings.find({}).limit(500).to_list(500)
    sampled = failed = 0
    for binding in bindings:
        try:
            raw = await provider.device(db, binding)
            await record_snapshot(db, binding, snapshot(raw), source="monitor")
            sampled += 1
        except HTTPException:
            await record_unavailable(db, binding, source="monitor")
            failed += 1
    return {"sampled": sampled, "failed": failed}


def telemetry_enabled() -> bool:
    """Read-only climate sampling can run independently of all other cron jobs.

    CLIMATE_MONITOR_ENABLED remains a backwards-compatible telemetry opt-in only.
    It no longer implies permission to execute thermostat schedules.
    """
    return (
        os.getenv("CLIMATE_ENABLED") == "true"
        and (
            os.getenv("CLIMATE_TELEMETRY_ENABLED") == "true"
            or os.getenv("CLIMATE_MONITOR_ENABLED") == "true"
        )
    )


def schedule_worker_enabled() -> bool:
    """Automatic thermostat schedule writes require three explicit gates."""
    return (
        os.getenv("CLIMATE_ENABLED") == "true"
        and os.getenv("CLIMATE_CONTROL_ENABLED") == "true"
        and os.getenv("CLIMATE_SCHEDULE_WORKER_ENABLED") == "true"
    )


def monitor_enabled() -> bool:
    """Compatibility alias used by existing clients to mean history collection."""
    return telemetry_enabled()


def _telemetry_interval_seconds() -> int:
    raw = os.getenv(
        "CLIMATE_TELEMETRY_INTERVAL_SECONDS",
        os.getenv("CLIMATE_MONITOR_INTERVAL_SECONDS", "300"),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 300
    return max(60, min(value, 3600))


def _schedule_interval_seconds() -> int:
    try:
        value = int(os.getenv("CLIMATE_SCHEDULE_INTERVAL_SECONDS", "60"))
    except (TypeError, ValueError):
        value = 60
    return max(60, min(value, 900))


async def telemetry_loop(db):
    """Read providers and persist history. Never sends thermostat commands."""
    while True:
        try:
            await sample_all(db)
        except Exception:
            pass
        await asyncio.sleep(_telemetry_interval_seconds())


async def schedule_loop(db):
    """Execute due Ross House schedules only behind the dedicated write gate."""
    while True:
        try:
            await run_due_schedules(db)
        except Exception:
            pass
        await asyncio.sleep(_schedule_interval_seconds())


async def monitor_loop(db):
    """Legacy combined loop retained for compatibility, with writes separately gated."""
    while True:
        try:
            await sample_all(db)
            if schedule_worker_enabled():
                await run_due_schedules(db)
        except Exception:
            pass
        await asyncio.sleep(_telemetry_interval_seconds())
