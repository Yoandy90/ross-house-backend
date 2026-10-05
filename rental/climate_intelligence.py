"""Predictive climate analytics and health scoring.

All heuristics are explicitly rule-based. They are designed to surface patterns
worth reviewing, not diagnose HVAC failures. Device/property-specific thresholds
are stored in climate_alert_rules and fall back to conservative defaults.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median

from fastapi import HTTPException

DEFAULT_RULES = {
    "temperature_low_f": 50.0,
    "temperature_high_f": 85.0,
    "humidity_low": 25.0,
    "humidity_high": 65.0,
    "humidity_sustain_minutes": 45,
    "rapid_humidity_window_minutes": 30,
    "rapid_humidity_delta": 15.0,
    "no_progress_window_minutes": 45,
    "no_progress_min_delta_f": 1.0,
    "short_cycle_window_minutes": 60,
    "short_cycle_max_starts": 5,
    "excessive_runtime_window_minutes": 180,
    "excessive_runtime_pct": 90.0,
    "fan_continuous_minutes": 240,
    "stale_reading_minutes": 20,
    "schedule_grace_minutes": 20,
    "efficiency_recent_days": 7,
    "efficiency_baseline_days": 28,
    "efficiency_degradation_ratio": 1.5,
    "filter_runtime_hours": 300.0,
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def native_delta(value_f: float, units: str | None) -> float:
    return value_f if units == "Fahrenheit" else value_f * 5 / 9


async def get_rules(db, binding: dict) -> dict:
    doc = await db.climate_alert_rules.find_one({"_id": binding["_id"]})
    rules = dict(DEFAULT_RULES)
    if doc:
        for key in DEFAULT_RULES:
            value = doc.get(key)
            if finite(value):
                rules[key] = value
    return rules


async def set_rules(db, binding: dict, values: dict, actor: str) -> dict:
    clean = {}
    for key, default in DEFAULT_RULES.items():
        if key not in values:
            continue
        value = values[key]
        if not finite(value) or value < 0:
            raise HTTPException(422, "climate_alert_rule_invalid")
        clean[key] = value
    merged = await get_rules(db, binding)
    merged.update(clean)
    if merged["temperature_low_f"] >= merged["temperature_high_f"]:
        raise HTTPException(422, "climate_alert_temperature_thresholds_invalid")
    if merged["humidity_low"] >= merged["humidity_high"] or merged["humidity_high"] > 100:
        raise HTTPException(422, "climate_alert_humidity_thresholds_invalid")
    if merged["excessive_runtime_pct"] > 100 or merged["efficiency_degradation_ratio"] < 1:
        raise HTTPException(422, "climate_alert_rule_invalid")
    for key in (
        "humidity_sustain_minutes", "rapid_humidity_window_minutes",
        "no_progress_window_minutes", "short_cycle_window_minutes",
        "excessive_runtime_window_minutes", "fan_continuous_minutes",
        "stale_reading_minutes", "schedule_grace_minutes",
        "efficiency_recent_days", "efficiency_baseline_days",
        "filter_runtime_hours",
    ):
        if merged[key] <= 0:
            raise HTTPException(422, "climate_alert_rule_invalid")
    clean.update(
        device_id=binding["_id"],
        property_id=binding.get("property_id", ""),
        updated_by=actor,
        updated_at=now(),
    )
    await db.climate_alert_rules.update_one(
        {"_id": binding["_id"]},
        {"$set": clean, "$setOnInsert": {"created_at": now()}},
        upsert=True,
    )
    return await get_rules(db, binding)


def basic_conditions(state: dict, rules: dict):
    units = state.get("units")
    temp = state.get("temperature")
    humidity = state.get("humidity")
    low_f = rules["temperature_low_f"]
    high_f = rules["temperature_high_f"]
    low = low_f if units == "Fahrenheit" else (low_f - 32) * 5 / 9
    high = high_f if units == "Fahrenheit" else (high_f - 32) * 5 / 9
    result = {
        "offline": (
            state.get("offline_alert") is True,
            "critical",
            "Thermostat is not reporting live data.",
        )
    }
    if finite(temp) and units in ("Fahrenheit", "Celsius"):
        result["temperature_low"] = (
            temp <= low,
            "critical",
            "Indoor temperature is below the configured protection threshold.",
        )
        result["temperature_high"] = (
            temp >= high,
            "critical",
            "Indoor temperature is above the configured protection threshold.",
        )
    if finite(humidity):
        result["humidity_low"] = (
            humidity <= rules["humidity_low"],
            "warning",
            "Indoor humidity is unusually low.",
        )
        result["humidity_high"] = (
            humidity >= rules["humidity_high"],
            "warning",
            "Indoor humidity is unusually high.",
        )
    return result


async def _recent_readings(db, device_id: str, minutes: int, limit=5000):
    start = now() - timedelta(minutes=minutes)
    return await db.climate_readings.find(
        {"device_id": device_id, "observed_at": {"$gte": start}}
    ).sort("observed_at", 1).limit(limit).to_list(limit)


def _target_error(row: dict):
    temp = row.get("temperature")
    if not finite(temp):
        return None
    mode = row.get("mode")
    if mode == "Heat" and finite(row.get("heat_setpoint")):
        return row["heat_setpoint"] - temp
    if mode == "Cool" and finite(row.get("cool_setpoint")):
        return temp - row["cool_setpoint"]
    if mode == "Auto" and finite(row.get("heat_setpoint")) and finite(row.get("cool_setpoint")):
        if temp < row["heat_setpoint"]:
            return row["heat_setpoint"] - temp
        if temp > row["cool_setpoint"]:
            return temp - row["cool_setpoint"]
        return 0.0
    return None


def estimate_rate(rows: list[dict], activity: str | None):
    usable = [
        row for row in rows
        if finite(row.get("temperature")) and isinstance(row.get("observed_at"), datetime)
    ]
    if len(usable) < 3:
        return None
    if activity in ("heating", "cooling"):
        usable = [row for row in usable if row.get("activity") == activity]
    if len(usable) < 3:
        return None
    first, last = usable[0], usable[-1]
    seconds = (last["observed_at"] - first["observed_at"]).total_seconds()
    if seconds <= 0:
        return None
    delta = last["temperature"] - first["temperature"]
    if activity == "cooling":
        delta = -delta
    return delta / seconds * 3600


async def filter_runtime_hours(db, binding: dict, sample_minutes: int = 5):
    maintenance = await db.climate_maintenance.find_one({"_id": binding["_id"]})
    start = (maintenance or {}).get("filter_changed_at")
    if not isinstance(start, datetime):
        start = now() - timedelta(days=365 * 5)
    rows = await db.climate_readings.find(
        {"device_id": binding["_id"], "observed_at": {"$gte": start}},
        {"activity": 1, "fan_running": 1},
    ).limit(250000).to_list(250000)
    active = sum(
        1
        for row in rows
        if row.get("activity") in ("heating", "cooling") or row.get("fan_running") is True
    )
    return active * sample_minutes / 60


async def reset_filter(db, binding: dict, actor: str):
    await db.climate_maintenance.update_one(
        {"_id": binding["_id"]},
        {"$set": {
            "device_id": binding["_id"],
            "property_id": binding.get("property_id", ""),
            "filter_changed_at": now(),
            "filter_changed_by": actor,
            "updated_at": now(),
        }},
        upsert=True,
    )
    return {"filter_changed_at": now().isoformat()}


async def predictive_conditions(db, binding: dict, state: dict, rules: dict, sample_minutes=5):
    result = {}
    device_id = binding["_id"]
    units = state.get("units")
    current_temp = state.get("temperature")
    activity = state.get("activity")
    tolerance = native_delta(2.0, units)
    min_progress = native_delta(rules["no_progress_min_delta_f"], units)

    # No-progress / excessive runtime.
    runtime_window = int(max(rules["excessive_runtime_window_minutes"], rules["no_progress_window_minutes"]))
    rows = await _recent_readings(db, device_id, runtime_window)
    if rows and activity in ("heating", "cooling") and finite(current_temp):
        progress_rows = [
            row for row in rows
            if row.get("activity") == activity and finite(row.get("temperature"))
        ]
        if len(progress_rows) >= 3:
            first_temp = progress_rows[0]["temperature"]
            progress = current_temp - first_temp if activity == "heating" else first_temp - current_temp
            error = _target_error(rows[-1])
            enough_span = (
                rows[-1]["observed_at"] - progress_rows[0]["observed_at"]
            ).total_seconds() >= rules["no_progress_window_minutes"] * 60 * 0.75
            result["no_progress"] = (
                bool(enough_span and finite(error) and error > tolerance and progress < min_progress),
                "critical",
                "HVAC is running but indoor temperature is not moving toward the target as expected.",
            )

        active_rows = [r for r in rows if r.get("activity") in ("heating", "cooling")]
        runtime_pct = len(active_rows) / len(rows) * 100 if rows else 0
        error = _target_error(rows[-1])
        result["excessive_runtime"] = (
            bool(
                len(rows) >= 6
                and runtime_pct >= rules["excessive_runtime_pct"]
                and finite(error)
                and error > tolerance
            ),
            "warning",
            "HVAC runtime is unusually high while the property remains away from target.",
        )

    # Short cycling.
    cycle_start = now() - timedelta(minutes=rules["short_cycle_window_minutes"])
    events = await db.climate_events.find(
        {
            "device_id": device_id,
            "type": "state_change",
            "field": "activity",
            "created_at": {"$gte": cycle_start},
            "after": {"$in": ["heating", "cooling"]},
        },
        {"after": 1},
    ).limit(100).to_list(100)
    result["short_cycling"] = (
        len(events) >= int(rules["short_cycle_max_starts"]),
        "warning",
        "HVAC is starting unusually often within a short period.",
    )

    # Sustained and fast-changing humidity.
    humidity_window = int(max(rules["humidity_sustain_minutes"], rules["rapid_humidity_window_minutes"]))
    humidity_rows = await _recent_readings(db, device_id, humidity_window)
    humid = [r for r in humidity_rows if finite(r.get("humidity"))]
    if len(humid) >= 3:
        high_fraction = sum(r["humidity"] >= rules["humidity_high"] for r in humid) / len(humid)
        low_fraction = sum(r["humidity"] <= rules["humidity_low"] for r in humid) / len(humid)
        result["humidity_high_sustained"] = (
            high_fraction >= 0.8,
            "warning",
            "Indoor humidity has remained high for an extended period.",
        )
        result["humidity_low_sustained"] = (
            low_fraction >= 0.8,
            "warning",
            "Indoor humidity has remained low for an extended period.",
        )
        rise = humid[-1]["humidity"] - humid[0]["humidity"]
        result["humidity_rising_fast"] = (
            rise >= rules["rapid_humidity_delta"],
            "warning",
            "Indoor humidity rose rapidly and should be reviewed.",
        )

    # Fan left running for a long interval.
    fan_rows = await _recent_readings(db, device_id, int(rules["fan_continuous_minutes"]))
    fan_active = [r for r in fan_rows if r.get("fan_running") is True or r.get("fan_mode") == "On"]
    result["fan_extended"] = (
        bool(len(fan_rows) >= 6 and len(fan_active) / len(fan_rows) >= 0.9),
        "info",
        "Fan has been running continuously for an extended period.",
    )

    # Recent ambiguous command or schedule execution.
    command_cutoff = now() - timedelta(minutes=60)
    unknown_command = await db.climate_commands.find_one(
        {"device_id": device_id, "status": "unknown", "created_at": {"$gte": command_cutoff}},
        {"_id": 1},
    )
    result["command_unconfirmed"] = (
        bool(unknown_command),
        "warning",
        "A thermostat command could not be confirmed and should be reviewed before retrying.",
    )
    schedule_cutoff = now() - timedelta(minutes=max(60, int(rules["schedule_grace_minutes"]) * 2))
    bad_schedule = await db.climate_schedule_runs.find_one(
        {
            "device_id": device_id,
            "status": {"$in": ["unknown", "pending"]},
            "started_at": {"$gte": schedule_cutoff},
        },
        {"_id": 1},
    )
    result["schedule_unconfirmed"] = (
        bool(bad_schedule),
        "warning",
        "A scheduled climate change did not reach a confirmed final state.",
    )

    # Filter runtime reminder.
    runtime_hours = await filter_runtime_hours(db, binding, sample_minutes=sample_minutes)
    result["filter_runtime"] = (
        runtime_hours >= rules["filter_runtime_hours"],
        "info",
        "Estimated HVAC/fan runtime has reached the configured filter-service interval.",
    )

    # Efficiency degradation compared with the property's own baseline.
    recent_days = int(rules["efficiency_recent_days"])
    baseline_days = int(rules["efficiency_baseline_days"])
    cutoff_recent = now() - timedelta(days=recent_days)
    cutoff_baseline = now() - timedelta(days=baseline_days)
    hist = await db.climate_readings.find(
        {"device_id": device_id, "observed_at": {"$gte": cutoff_baseline}},
        {"temperature": 1, "mode": 1, "heat_setpoint": 1, "cool_setpoint": 1, "observed_at": 1},
    ).limit(100000).to_list(100000)
    recent_errors, baseline_errors = [], []
    for row in hist:
        error = _target_error(row)
        if error is None:
            continue
        ts = row.get("observed_at")
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if isinstance(ts, datetime) and ts >= cutoff_recent:
            recent_errors.append(abs(error))
        else:
            baseline_errors.append(abs(error))
    recent_mean = sum(recent_errors) / len(recent_errors) if recent_errors else None
    baseline_median = median(baseline_errors) if baseline_errors else None
    degraded = bool(
        recent_mean is not None
        and baseline_median is not None
        and len(recent_errors) >= 20
        and len(baseline_errors) >= 50
        and recent_mean > max(native_delta(2, units), baseline_median * rules["efficiency_degradation_ratio"])
    )
    result["efficiency_degradation"] = (
        degraded,
        "warning",
        "Recent temperature control is less accurate than this property's historical baseline.",
    )
    return result


ALERT_WEIGHTS = {
    "offline": 35,
    "temperature_low": 30,
    "temperature_high": 30,
    "no_progress": 25,
    "short_cycling": 18,
    "excessive_runtime": 16,
    "efficiency_degradation": 15,
    "humidity_high_sustained": 10,
    "humidity_low_sustained": 8,
    "humidity_rising_fast": 10,
    "schedule_unconfirmed": 10,
    "command_unconfirmed": 10,
    "fan_extended": 5,
    "filter_runtime": 5,
    "humidity_low": 5,
    "humidity_high": 5,
}


def score_from_alerts(alert_types: list[str], analytics_summary: dict | None = None):
    score = 100
    reasons = []
    for alert in alert_types:
        penalty = ALERT_WEIGHTS.get(alert, 4)
        score -= penalty
        reasons.append({"type": alert, "penalty": penalty})
    summary = analytics_summary or {}
    error = summary.get("mean_target_error")
    comfort = summary.get("comfort_pct")
    if finite(error) and error > 3:
        score -= 8
        reasons.append({"type": "target_error", "penalty": 8})
    if finite(comfort) and comfort < 75:
        score -= 8
        reasons.append({"type": "low_comfort", "penalty": 8})
    score = max(0, min(100, int(round(score))))
    label = (
        "excellent" if score >= 90 else
        "good" if score >= 80 else
        "watch" if score >= 65 else
        "attention" if score >= 45 else
        "critical"
    )
    return {"score": score, "label": label, "reasons": reasons}


async def health_score(db, binding: dict, analytics_fn, sample_minutes=5):
    active = await db.climate_alert_state.find(
        {"device_id": binding["_id"], "active": True},
        {"type": 1, "severity": 1},
    ).limit(100).to_list(100)
    analytics = await analytics_fn(db, binding["_id"], "7d")
    scored = score_from_alerts(
        [a.get("type") for a in active if a.get("type")],
        analytics.get("summary") or {},
    )

    recent = await _recent_readings(db, binding["_id"], 90)
    latest = recent[-1] if recent else None
    activity = (latest or {}).get("activity")
    rate = estimate_rate(recent, activity)
    eta = None
    if latest and finite(rate) and rate > 0:
        error = _target_error(latest)
        if finite(error) and error > 0:
            eta = max(0, min(24 * 60, error / rate * 60))

    filter_hours = await filter_runtime_hours(db, binding, sample_minutes=sample_minutes)
    return {
        **scored,
        "active_alerts": active,
        "estimated_rate_per_hour": round(rate, 2) if finite(rate) else None,
        "estimated_minutes_to_target": round(eta) if finite(eta) else None,
        "filter_runtime_hours_est": round(filter_hours, 1),
        "analytics": analytics.get("summary") or {},
    }


async def fleet_overview(db, analytics_fn, sample_minutes=5):
    bindings = await db.climate_bindings.find({}).limit(200).to_list(200)
    property_ids = [b.get("property_id") for b in bindings if b.get("property_id")]
    properties = {}
    if property_ids:
        from bson import ObjectId
        ids = []
        for value in property_ids:
            if ObjectId.is_valid(str(value)):
                ids.append(ObjectId(str(value)))
        async for prop in db.properties.find(
            {"_id": {"$in": ids}},
            {"address": 1, "city": 1, "state": 1},
        ):
            properties[str(prop["_id"])] = prop

    devices = []
    totals = {"excellent": 0, "good": 0, "watch": 0, "attention": 0, "critical": 0}
    alert_total = 0
    for binding in bindings:
        latest = await db.climate_readings.find_one(
            {"device_id": binding["_id"]},
            sort=[("observed_at", -1)],
        )
        health = await health_score(
            db,
            binding,
            analytics_fn,
            sample_minutes=sample_minutes,
        )
        label = health["label"]
        totals[label] = totals.get(label, 0) + 1
        alert_total += len(health.get("active_alerts") or [])
        prop = properties.get(str(binding.get("property_id", ""))) or {}
        observed_at = (latest or {}).get("observed_at")
        if isinstance(observed_at, datetime):
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            observed_text = observed_at.isoformat()
            age_minutes = max(0, (now() - observed_at).total_seconds() / 60)
        else:
            observed_text = None
            age_minutes = None
        devices.append({
            "device_id": binding["_id"],
            "name": binding.get("name", ""),
            "property_id": str(binding.get("property_id", "")),
            "property": prop.get("address") or binding.get("name", ""),
            "city": prop.get("city", ""),
            "score": health["score"],
            "health": health["label"],
            "active_alerts": health.get("active_alerts") or [],
            "estimated_rate_per_hour": health.get("estimated_rate_per_hour"),
            "estimated_minutes_to_target": health.get("estimated_minutes_to_target"),
            "filter_runtime_hours_est": health.get("filter_runtime_hours_est"),
            "temperature": (latest or {}).get("temperature"),
            "humidity": (latest or {}).get("humidity"),
            "outdoor_temperature": (latest or {}).get("outdoor_temperature"),
            "mode": (latest or {}).get("mode"),
            "activity": (latest or {}).get("activity"),
            "heat_setpoint": (latest or {}).get("heat_setpoint"),
            "cool_setpoint": (latest or {}).get("cool_setpoint"),
            "units": (latest or {}).get("units"),
            "online": (latest or {}).get("online"),
            "observed_at": observed_text,
            "age_minutes": round(age_minutes, 1) if finite(age_minutes) else None,
        })
    devices.sort(key=lambda row: (row["score"], row["property"]))
    return {
        "summary": {
            "devices": len(devices),
            "active_alerts": alert_total,
            **totals,
        },
        "devices": devices,
    }


async def get_energy_config(db, binding: dict):
    doc = await db.climate_energy_config.find_one({"_id": binding["_id"]}) or {}
    return {
        "heat_kw": doc.get("heat_kw"),
        "cool_kw": doc.get("cool_kw"),
        "fan_kw": doc.get("fan_kw"),
        "electric_rate": doc.get("electric_rate"),
        "updated_at": doc.get("updated_at").isoformat() if isinstance(doc.get("updated_at"), datetime) else None,
    }


async def set_energy_config(db, binding: dict, values: dict, actor: str):
    clean = {}
    limits = {
        "heat_kw": (0, 50),
        "cool_kw": (0, 50),
        "fan_kw": (0, 10),
        "electric_rate": (0, 5),
    }
    for key, (low, high) in limits.items():
        value = values.get(key)
        if not finite(value) or value < low or value > high:
            raise HTTPException(422, "climate_energy_config_invalid")
        clean[key] = float(value)
    clean.update({
        "device_id": binding["_id"],
        "property_id": binding.get("property_id", ""),
        "updated_by": actor,
        "updated_at": now(),
    })
    await db.climate_energy_config.update_one(
        {"_id": binding["_id"]},
        {"$set": clean, "$setOnInsert": {"created_at": now()}},
        upsert=True,
    )
    return await get_energy_config(db, binding)
