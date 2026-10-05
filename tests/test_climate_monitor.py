import os

import pytest
from fastapi import HTTPException

from rental import climate_monitor


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


def test_monitor_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv('CLIMATE_ENABLED', 'true')
    monkeypatch.delenv('CLIMATE_MONITOR_ENABLED', raising=False)
    assert climate_monitor.monitor_enabled() is False
    monkeypatch.setenv('CLIMATE_MONITOR_ENABLED', 'true')
    assert climate_monitor.monitor_enabled() is True


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
