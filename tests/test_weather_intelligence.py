import os

import pytest
from fastapi import HTTPException

from rental import weather_intelligence


def test_weather_flag_is_explicit(monkeypatch):
    monkeypatch.delenv("CLIMATE_WEATHER_ENABLED", raising=False)
    assert weather_intelligence.enabled() is False
    monkeypatch.setenv("CLIMATE_WEATHER_ENABLED", "true")
    assert weather_intelligence.enabled() is True


def test_weather_unit_normalization():
    assert weather_intelligence._fahrenheit(0, "wmoUnit:degC") == 32
    assert weather_intelligence._fahrenheit(68, "wmoUnit:degF") == 68
    assert weather_intelligence._mph(16.09344, "wmoUnit:km_h-1") == pytest.approx(10, abs=0.02)


def test_forecast_period_normalizes_temperature_and_probabilities():
    row = weather_intelligence._forecast_period({
        "number": 1,
        "name": "This Afternoon",
        "startTime": "2026-10-05T13:00:00-05:00",
        "endTime": "2026-10-05T14:00:00-05:00",
        "isDaytime": True,
        "temperature": 20,
        "temperatureUnit": "C",
        "relativeHumidity": {"value": 44},
        "probabilityOfPrecipitation": {"value": 15},
        "windSpeed": "10 mph",
        "windDirection": "N",
        "shortForecast": "Sunny",
    })
    assert row["temperature_f"] == 68
    assert row["humidity"] == 44
    assert row["precipitation_probability"] == 15
    assert row["short_forecast"] == "Sunny"


def test_weather_alerts_detect_hvac_conflicts_and_freeze_risk():
    weather = {
        "status": "ok",
        "current": {"temperature_f": 30},
        "hourly": [{"temperature_f": 27}, {"temperature_f": 25}],
        "alerts": [],
    }
    conditions = weather_intelligence.alert_conditions({
        "units": "Fahrenheit",
        "temperature": 58,
        "activity": "cooling",
    }, weather)
    assert conditions["cooling_in_cold_weather"][0] is True
    assert conditions["forecast_freeze_risk"][0] is True
    assert conditions["heating_in_hot_weather"][0] is False


def test_official_severe_alert_is_exposed_as_weather_signal():
    weather = {
        "status": "ok",
        "current": {"temperature_f": 72},
        "hourly": [{"temperature_f": 72}],
        "alerts": [{"event": "Tornado Warning", "severity": "Extreme"}],
    }
    conditions = weather_intelligence.alert_conditions({
        "units": "Fahrenheit",
        "temperature": 72,
        "activity": "idle",
    }, weather)
    assert conditions["nws_severe_weather"][0] is True
    advice = weather_intelligence.weather_advice({}, weather)
    assert advice[0]["type"] == "official_weather_alert"


def test_weather_advice_is_advisory_not_autonomous_control():
    weather = {
        "status": "ok",
        "current": {"temperature_f": 38},
        "hourly": [{"temperature_f": 38}, {"temperature_f": 31}, {"temperature_f": 26}],
        "alerts": [],
    }
    advice = weather_intelligence.weather_advice({
        "units": "Fahrenheit",
        "temperature": 70,
        "activity": "cooling",
    }, weather)
    conflict = next(item for item in advice if item["type"] == "cooling_cold_outdoor")
    assert "no apagará" in conflict["body_es"]
    assert "will not shut" in conflict["body_en"]


def test_nws_url_guard_rejects_non_government_host():
    with pytest.raises(HTTPException):
        weather_intelligence._safe_nws_url("https://example.com/forecast")
    assert weather_intelligence._safe_nws_url(
        "https://api.weather.gov/gridpoints/AMA/1,1/forecast"
    ).startswith("https://api.weather.gov/")


def test_weather_rule_matrix_covers_extreme_heat_and_safe_freeze_context():
    hot = {
        "status": "ok",
        "current": {"temperature_f": 96},
        "hourly": [{"temperature_f": 97}, {"temperature_f": 99}],
        "alerts": [],
    }
    hot_conditions = weather_intelligence.alert_conditions({
        "units": "Fahrenheit",
        "temperature": 72,
        "activity": "heating",
    }, hot)
    assert hot_conditions["heating_in_hot_weather"][0] is True
    assert hot_conditions["cooling_in_cold_weather"][0] is False
    hot_advice = weather_intelligence.weather_advice({
        "units": "Fahrenheit",
        "temperature": 72,
        "activity": "idle",
    }, hot)
    assert any(item["type"] == "extreme_heat_forecast" for item in hot_advice)

    freeze_but_safe_inside = {
        "status": "ok",
        "current": {"temperature_f": 24},
        "hourly": [{"temperature_f": 22}, {"temperature_f": 18}],
        "alerts": [],
    }
    safe_conditions = weather_intelligence.alert_conditions({
        "units": "Fahrenheit",
        "temperature": 70,
        "activity": "heating",
    }, freeze_but_safe_inside)
    assert safe_conditions["forecast_freeze_risk"][0] is False
    safe_advice = weather_intelligence.weather_advice({
        "units": "Fahrenheit",
        "temperature": 70,
        "activity": "heating",
    }, freeze_but_safe_inside)
    assert any(item["type"] == "freeze_forecast" for item in safe_advice)


def test_non_severe_nws_alert_does_not_raise_severe_weather_signal():
    weather = {
        "status": "ok",
        "current": {"temperature_f": 70},
        "hourly": [{"temperature_f": 70}],
        "alerts": [{"event": "Wind Advisory", "severity": "Moderate"}],
    }
    conditions = weather_intelligence.alert_conditions({
        "units": "Fahrenheit",
        "temperature": 70,
        "activity": "idle",
    }, weather)
    assert conditions["nws_severe_weather"][0] is False


def test_weather_context_never_creates_hvac_command_payload():
    weather = {
        "status": "ok",
        "current": {"temperature_f": 30},
        "hourly": [{"temperature_f": 25}],
        "alerts": [{"event": "Winter Storm Warning", "severity": "Severe"}],
    }
    advice = weather_intelligence.weather_advice({
        "units": "Fahrenheit",
        "temperature": 58,
        "activity": "cooling",
    }, weather)
    serialized = repr(advice).lower()
    for forbidden in ("heatsetpoint", "coolsetpoint", "request_id", "provider.change"):
        assert forbidden not in serialized
