import asyncio
import os

import pytest
from fastapi import HTTPException

from rental import climate_monitor, climate_intelligence


RAW = {
    'isAlive': True,
    'units': 'Fahrenheit',
    'allowedModes': ['Off', 'Heat', 'Cool', 'Auto'],
    'changeableValues': {
        'mode': 'Heat',
        'heatSetpoint': 70,
        'coolSetpoint': 75,
    },
    'minHeatSetpoint': 40,
    'maxHeatSetpoint': 90,
    'minCoolSetpoint': 50,
    'maxCoolSetpoint': 99,
    'deadband': 3,
}


def test_schedule_period_validation_by_mode():
    heat = climate_monitor.validate_period({
        'days': [0, 1, 2, 3, 4],
        'time': '06:30',
        'mode': 'Heat',
        'heatSetpoint': 72,
    }, RAW)
    assert heat == {'mode': 'Heat', 'heatSetpoint': 72}

    auto = climate_monitor.validate_period({
        'days': [5, 6],
        'time': '08:00',
        'mode': 'Auto',
        'heatSetpoint': 68,
        'coolSetpoint': 74,
    }, RAW)
    assert auto == {'mode': 'Auto', 'heatSetpoint': 68, 'coolSetpoint': 74}

    with pytest.raises(HTTPException):
        climate_monitor.validate_period({
            'days': [0],
            'time': '07:00',
            'mode': 'Cool',
        }, RAW)


def test_schedule_rejects_bad_days_time_and_deadband():
    for payload in (
        {'days': [7], 'time': '07:00', 'mode': 'Heat', 'heatSetpoint': 70},
        {'days': [0], 'time': '25:00', 'mode': 'Heat', 'heatSetpoint': 70},
        {'days': [], 'time': '07:00', 'mode': 'Heat', 'heatSetpoint': 70},
        {'days': [0], 'time': '07:00', 'mode': 'Auto', 'heatSetpoint': 72, 'coolSetpoint': 73},
    ):
        with pytest.raises(HTTPException):
            climate_monitor.validate_period(payload, RAW)


def test_alert_conditions_cover_offline_temperature_and_humidity():
    alerts = climate_monitor._alert_conditions({
        'online': False,
        'offline_alert': True,
        'units': 'Fahrenheit',
        'temperature': 45,
        'humidity': 70,
    })
    assert alerts['offline'][0] is True
    assert alerts['temperature_low'][0] is True
    assert alerts['temperature_high'][0] is False
    assert alerts['humidity_high'][0] is True
    assert alerts['humidity_low'][0] is False


def test_range_buckets_scale_for_long_term_history():
    assert climate_monitor._bucket_for_range('24h') == ('minute', 15)
    assert climate_monitor._bucket_for_range('7d') == ('hour', 1)
    assert climate_monitor._bucket_for_range('1y') == ('day', 1)
    assert climate_monitor._bucket_for_range('5y') == ('week', 1)
    assert climate_monitor._bucket_for_range('all') == ('month', 1)


def test_telemetry_and_schedule_workers_have_independent_gates(monkeypatch):
    monkeypatch.setenv('CLIMATE_ENABLED', 'true')
    monkeypatch.delenv('CLIMATE_MONITOR_ENABLED', raising=False)
    monkeypatch.delenv('CLIMATE_TELEMETRY_ENABLED', raising=False)
    monkeypatch.delenv('CLIMATE_SCHEDULE_WORKER_ENABLED', raising=False)
    monkeypatch.delenv('CLIMATE_CONTROL_ENABLED', raising=False)

    assert climate_monitor.telemetry_enabled() is False
    assert climate_monitor.monitor_enabled() is False
    assert climate_monitor.schedule_worker_enabled() is False

    monkeypatch.setenv('CLIMATE_TELEMETRY_ENABLED', 'true')
    assert climate_monitor.telemetry_enabled() is True
    assert climate_monitor.monitor_enabled() is True
    assert climate_monitor.schedule_worker_enabled() is False

    monkeypatch.setenv('CLIMATE_CONTROL_ENABLED', 'true')
    assert climate_monitor.schedule_worker_enabled() is False
    monkeypatch.setenv('CLIMATE_SCHEDULE_WORKER_ENABLED', 'true')
    assert climate_monitor.schedule_worker_enabled() is True


def test_legacy_monitor_flag_is_telemetry_only(monkeypatch):
    monkeypatch.setenv('CLIMATE_ENABLED', 'true')
    monkeypatch.setenv('CLIMATE_MONITOR_ENABLED', 'true')
    monkeypatch.delenv('CLIMATE_TELEMETRY_ENABLED', raising=False)
    monkeypatch.delenv('CLIMATE_SCHEDULE_WORKER_ENABLED', raising=False)
    monkeypatch.setenv('CLIMATE_CONTROL_ENABLED', 'true')

    assert climate_monitor.telemetry_enabled() is True
    assert climate_monitor.schedule_worker_enabled() is False


def test_worker_intervals_are_bounded(monkeypatch):
    monkeypatch.setenv('CLIMATE_TELEMETRY_INTERVAL_SECONDS', '5')
    monkeypatch.setenv('CLIMATE_SCHEDULE_INTERVAL_SECONDS', '5000')
    assert climate_monitor._telemetry_interval_seconds() == 60
    assert climate_monitor._schedule_interval_seconds() == 900


def test_timezone_validation():
    assert climate_monitor.validate_timezone('America/Chicago') == 'America/Chicago'
    with pytest.raises(HTTPException):
        climate_monitor.validate_timezone('Not/A_Timezone')


def test_offline_condition_respects_grace_flag():
    early = climate_monitor._alert_conditions({
        'online': False,
        'offline_alert': False,
    })
    mature = climate_monitor._alert_conditions({
        'online': False,
        'offline_alert': True,
    })
    assert early['offline'][0] is False
    assert mature['offline'][0] is True


def test_alert_thresholds_can_be_overridden():
    rules = dict(climate_intelligence.DEFAULT_RULES)
    rules.update(temperature_low_f=55, temperature_high_f=80, humidity_low=30, humidity_high=60)
    alerts = climate_monitor._alert_conditions({
        'online': True,
        'offline_alert': False,
        'units': 'Fahrenheit',
        'temperature': 82,
        'humidity': 62,
    }, rules)
    assert alerts['temperature_high'][0] is True
    assert alerts['humidity_high'][0] is True


def test_health_score_penalizes_predictive_alerts():
    result = climate_intelligence.score_from_alerts(
        ['no_progress', 'short_cycling', 'filter_runtime'],
        {'mean_target_error': 4.0, 'comfort_pct': 60},
    )
    assert result['score'] < 50
    assert result['label'] in ('attention', 'critical')
    assert any(reason['type'] == 'no_progress' for reason in result['reasons'])


def test_temperature_rate_estimation():
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {'observed_at': start, 'temperature': 68, 'activity': 'heating'},
        {'observed_at': start + timedelta(minutes=30), 'temperature': 69, 'activity': 'heating'},
        {'observed_at': start + timedelta(minutes=60), 'temperature': 70, 'activity': 'heating'},
    ]
    assert climate_intelligence.estimate_rate(rows, 'heating') == pytest.approx(2.0)


def test_schedule_rejects_duplicate_day_time():
    with pytest.raises(HTTPException):
        climate_monitor._validate_schedule_periods([
            {'days': [0, 1], 'time': '06:00', 'mode': 'Heat', 'heatSetpoint': 70},
            {'days': [1, 2], 'time': '06:00', 'mode': 'Heat', 'heatSetpoint': 68},
        ], RAW)


def test_emergency_heat_schedule_requires_provider_capability_and_heat_target():
    raw = dict(RAW)
    raw['allowedModes'] = [*RAW['allowedModes'], 'EmergencyHeat']
    period = climate_monitor.validate_period({
        'days': [0],
        'time': '05:30',
        'mode': 'EmergencyHeat',
        'heatSetpoint': 68,
    }, raw)
    assert period == {'mode': 'EmergencyHeat', 'heatSetpoint': 68}

    with pytest.raises(HTTPException):
        climate_monitor.validate_period({
            'days': [0],
            'time': '05:30',
            'mode': 'EmergencyHeat',
        }, raw)

    with pytest.raises(HTTPException):
        climate_monitor.validate_period({
            'days': [0],
            'time': '05:30',
            'mode': 'EmergencyHeat',
            'heatSetpoint': 68,
        }, RAW)


def test_health_score_penalizes_new_predictive_signals():
    result = climate_intelligence.score_from_alerts(
        ['stale_telemetry', 'schedule_missed', 'thermal_envelope_degradation', 'emergency_heat_extended'],
        {},
    )
    assert result['score'] <= 55
    names = {reason['type'] for reason in result['reasons']}
    assert 'stale_telemetry' in names
    assert 'thermal_envelope_degradation' in names


def test_advanced_rules_have_safe_defaults():
    rules = climate_intelligence.DEFAULT_RULES
    assert rules['stale_reading_minutes'] > 0
    assert rules['schedule_grace_minutes'] > 0
    assert rules['thermal_degradation_ratio'] >= 1
    assert rules['emergency_heat_minutes'] > 0


def test_read_only_telemetry_loop_never_executes_schedules(monkeypatch):
    calls = {"sample": 0, "schedule": 0}

    class StopLoop(Exception):
        pass

    async def fake_sample_all(db):
        calls["sample"] += 1
        return {"sampled": 1, "failed": 0}

    async def forbidden_schedule(db):
        calls["schedule"] += 1
        raise AssertionError("telemetry loop must never execute schedules")

    async def stop_sleep(seconds):
        raise StopLoop()

    monkeypatch.setattr(climate_monitor, "sample_all", fake_sample_all)
    monkeypatch.setattr(climate_monitor, "run_due_schedules", forbidden_schedule)
    monkeypatch.setattr(climate_monitor.asyncio, "sleep", stop_sleep)

    with pytest.raises(StopLoop):
        asyncio.run(climate_monitor.telemetry_loop(object()))

    assert calls == {"sample": 1, "schedule": 0}


def test_schedule_loop_does_not_collect_telemetry(monkeypatch):
    calls = {"sample": 0, "schedule": 0}

    class StopLoop(Exception):
        pass

    async def forbidden_sample(db):
        calls["sample"] += 1
        raise AssertionError("schedule loop must not collect telemetry")

    async def fake_schedule(db):
        calls["schedule"] += 1

    async def stop_sleep(seconds):
        raise StopLoop()

    monkeypatch.setattr(climate_monitor, "sample_all", forbidden_sample)
    monkeypatch.setattr(climate_monitor, "run_due_schedules", fake_schedule)
    monkeypatch.setattr(climate_monitor.asyncio, "sleep", stop_sleep)

    with pytest.raises(StopLoop):
        asyncio.run(climate_monitor.schedule_loop(object()))

    assert calls == {"sample": 0, "schedule": 1}


def test_weather_fallback_requires_known_thermostat_units():
    binding = {"_id": "device-1", "property_id": "property-1", "name": "Test"}
    weather = {
        "status": "ok",
        "current": {
            "temperature_f": 75.2,
            "humidity": 50.0,
            "station": "KDUX",
            "description": "Mostly Clear",
        },
        "hourly": [],
        "alerts": [],
    }
    from datetime import datetime, timezone
    observed = datetime(2026, 10, 5, tzinfo=timezone.utc)

    unknown = climate_monitor._reading_doc(
        binding,
        {"online": False, "units": None},
        observed,
        "monitor",
        weather,
    )
    assert unknown["nws_temperature_f"] == 75.2
    assert unknown["outdoor_temperature"] is None
    assert unknown["outdoor_source"] is None

    fahrenheit = climate_monitor._reading_doc(
        binding,
        {"online": True, "units": "Fahrenheit"},
        observed,
        "monitor",
        weather,
    )
    assert fahrenheit["outdoor_temperature"] == 75.2
    assert fahrenheit["outdoor_source"] == "nws"

    celsius = climate_monitor._reading_doc(
        binding,
        {"online": True, "units": "Celsius"},
        observed,
        "monitor",
        weather,
    )
    assert celsius["outdoor_temperature"] == pytest.approx(24.0, abs=0.01)
    assert celsius["outdoor_source"] == "nws"


def test_online_bucket_is_preserved_from_transient_offline_sample():
    assert climate_monitor._preserve_existing_bucket(
        {"online": True},
        {"online": False},
    ) is True
    assert climate_monitor._preserve_existing_bucket(
        {"online": False},
        {"online": True},
    ) is False
    assert climate_monitor._preserve_existing_bucket(
        None,
        {"online": False},
    ) is False


def test_read_retry_helpers_are_bounded(monkeypatch):
    monkeypatch.setenv("CLIMATE_READ_RETRY_COUNT", "9")
    monkeypatch.setenv("CLIMATE_READ_RETRY_DELAY_SECONDS", "99")
    assert climate_monitor._read_retry_count() == 2
    assert climate_monitor._read_retry_delay_seconds() == 5.0

    monkeypatch.setenv("CLIMATE_READ_RETRY_COUNT", "-1")
    monkeypatch.setenv("CLIMATE_READ_RETRY_DELAY_SECONDS", "0")
    assert climate_monitor._read_retry_count() == 0
    assert climate_monitor._read_retry_delay_seconds() == 0.1


def test_read_retry_recovers_before_marking_device_unavailable(monkeypatch):
    calls = {"read": 0, "sleep": 0}

    async def flaky_device(db, binding):
        calls["read"] += 1
        if calls["read"] == 1:
            raise HTTPException(503, "temporary")
        return RAW

    async def fake_sleep(seconds):
        calls["sleep"] += 1

    monkeypatch.setenv("CLIMATE_READ_RETRY_COUNT", "1")
    monkeypatch.setattr(climate_monitor.provider, "device", flaky_device)
    monkeypatch.setattr(climate_monitor.asyncio, "sleep", fake_sleep)

    result = asyncio.run(climate_monitor._read_provider_with_retry(object(), {"_id": "device-1"}))
    assert result == RAW
    assert calls == {"read": 2, "sleep": 1}


def test_telemetry_availability_percentage():
    assert climate_monitor._availability_pct(9, 10) == 90.0
    assert climate_monitor._availability_pct(0, 0) == 0.0
    assert climate_monitor._availability_pct(10, 10) == 100.0


def test_health_score_distinguishes_low_data_availability():
    degraded = climate_intelligence.score_from_alerts(
        [],
        {"samples": 20, "data_availability_pct": 45.0},
    )
    assert degraded["score"] == 85
    reason = next(item for item in degraded["reasons"] if item["type"] == "low_data_availability")
    assert reason["availability_pct"] == 45.0
    assert reason["penalty"] == 15

    early = climate_intelligence.score_from_alerts(
        [],
        {"samples": 3, "data_availability_pct": 33.3},
    )
    assert early["score"] == 100
    assert all(item["type"] != "low_data_availability" for item in early["reasons"])
