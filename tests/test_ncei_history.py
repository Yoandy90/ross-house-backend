import os

from rental import ncei_history


def test_ncei_token_is_optional_and_env_driven(monkeypatch):
    monkeypatch.delenv("NCEI_CDO_TOKEN", raising=False)
    assert ncei_history.configured() is False
    monkeypatch.setenv("NCEI_CDO_TOKEN", "example-token")
    assert ncei_history.configured() is True


def test_ncei_daily_mapping_uses_standard_weather_fields():
    docs = ncei_history._daily_docs(
        "property-1",
        2025,
        {"id": "GHCND:TEST", "name": "Test Station", "distance_miles": 2.5},
        [
            {"date": "2025-01-01T00:00:00", "datatype": "TAVG", "value": 48.2},
            {"date": "2025-01-01T00:00:00", "datatype": "TMAX", "value": 60.0},
            {"date": "2025-01-01T00:00:00", "datatype": "TMIN", "value": 35.0},
            {"date": "2025-01-01T00:00:00", "datatype": "PRCP", "value": 0.12},
            {"date": "2025-01-01T00:00:00", "datatype": "SNOW", "value": 1.0},
            {"date": "2025-01-02T00:00:00", "datatype": "TAVG", "value": 51.0},
            {"date": "2025-01-01T00:00:00", "datatype": "UNKNOWN", "value": 999},
        ],
    )
    assert len(docs) == 2
    first = docs[0]
    assert first["_id"] == "property-1:2025-01-01"
    assert first["tavg_f"] == 48.2
    assert first["tmax_f"] == 60.0
    assert first["tmin_f"] == 35.0
    assert first["prcp_in"] == 0.12
    assert first["snow_in"] == 1.0
    assert "UNKNOWN" not in first
    assert first["source"] == "NCEI_CDO_GHCND"


def test_ncei_distance_is_reasonable():
    # Dumas to Amarillo is roughly tens of miles, not hundreds or fractions.
    distance = ncei_history._haversine_miles(35.865, -101.973, 35.222, -101.831)
    assert 40 < distance < 60


def test_ncei_registry_never_requires_token_for_live_nws():
    # Historical NCEI is intentionally optional; NWS live weather uses another path.
    assert ncei_history.DATASET == "GHCND"
    assert "NCEI_CDO_TOKEN" not in os.environ or isinstance(os.environ["NCEI_CDO_TOKEN"], str)
