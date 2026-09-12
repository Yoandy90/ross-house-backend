from pathlib import Path

import pytest

from rental.runtime_business_boundary import (
    RENTALS_CORS_ORIGINS,
    RENTALS_STAGING_CORS_ORIGINS,
    deployed_cors_origins,
    resolve_database_name,
)


@pytest.mark.parametrize("environment,name", [
    ("production", "ross_house_production"), ("staging", "ross_house_staging"),
    ("development", "ross_house_local"),
])
def test_accepts_only_expected_ross_house_database(environment, name):
    assert resolve_database_name({"ENVIRONMENT": environment, "DB_NAME": name}) == name


def test_local_default_is_never_shared_historical_database():
    assert resolve_database_name({}) == "ross_house_local"


@pytest.mark.parametrize("environment,name", [
    ("production", ""), ("staging", ""),
    ("production", "ross_house_staging"), ("staging", "ross_house_production"),
    ("production", "taxportal"), ("development", "ross_tax"),
    ("development", "ross_lending"), ("development", "loan_records"),
    ("development", "unrelated_database"),
])
def test_rejects_missing_wrong_or_foreign_database(environment, name):
    with pytest.raises(RuntimeError):
        resolve_database_name({"ENVIRONMENT": environment, "DB_NAME": name})


def test_deployed_cors_is_exclusively_ross_house():
    assert deployed_cors_origins({"ENVIRONMENT": "production"}) == RENTALS_CORS_ORIGINS
    assert deployed_cors_origins({"ENVIRONMENT": "staging"}) == (
        RENTALS_CORS_ORIGINS + RENTALS_STAGING_CORS_ORIGINS
    )
    assert set(RENTALS_CORS_ORIGINS) == {
        "https://rosshouserentals.com", "https://www.rosshouserentals.com",
    }
    assert deployed_cors_origins({"ENVIRONMENT": "local"}) == ("*",)


def test_staging_cors_is_exact_and_never_leaks_to_production():
    staging_origin = (
        "https://ross-house-rentals-git-staging-yoandyross-2350s-projects.vercel.app"
    )
    assert RENTALS_STAGING_CORS_ORIGINS == (staging_origin,)
    assert staging_origin in deployed_cors_origins({"ENVIRONMENT": "staging"})
    assert staging_origin not in deployed_cors_origins({"ENVIRONMENT": "production"})
    assert "*" not in deployed_cors_origins({"ENVIRONMENT": "staging"})


def test_server_wires_boundary_and_preserves_opportunity_workers():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text()
    assert "DB_NAME = resolve_database_name(os.environ)" in source
    assert "rosslending.com" not in source.casefold()
    assert "emergentagent.com" not in source.casefold()
    assert "emergent.host" not in source.casefold()
    for worker in ("client_radar_scan_loop", "obituary_scan_loop", "struckoff_request_loop",
                   "deal_finder_scan_loop"):
        assert worker in source
