import io
import json
from urllib.error import HTTPError

import pytest

from scripts.staging_renewal_smoke import (
    RENEWAL_PATH,
    SmokeFailure,
    run_smoke,
    validate_base_url,
)


class Response:
    def __init__(self, status, payload):
        self.status = status
        self.body = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return self.body


def opener_for(*, database_name="ross_house_staging", anonymous_status=401, items=None):
    items = [] if items is None else items
    def open_request(request, timeout):
        assert timeout == 10
        assert request.get_method() == "GET"
        assert request.get_header("X-ross-environment") == "staging-smoke"
        if request.full_url.endswith("/api/health"):
            return Response(200, {
                "status": "ok",
                "service": "Ross House Rentals API",
                "database": "connected",
                "database_name": database_name,
            })
        assert request.full_url.endswith(RENEWAL_PATH)
        if request.get_header("Authorization"):
            return Response(200, {
                "ok": True,
                "read_only": True,
                "items": items,
                "total": len(items),
            })
        raise HTTPError(
            request.full_url,
            anonymous_status,
            "denied",
            {},
            io.BytesIO(b'{"detail":"Not authenticated"}'),
        )
    return open_request


@pytest.mark.parametrize("url", [
    "https://ross-house-backend-production.up.railway.app",
    "https://api.rosshouserentals.com",
    "https://preview.example.com",
    "http://backend-staging.example.com",
    "https://backend-staging.example.com/api",
])
def test_refuses_non_staging_or_unsafe_targets(url):
    with pytest.raises(SmokeFailure):
        validate_base_url(url)


def test_accepts_https_staging_and_localhost():
    assert validate_base_url("https://backend-staging.example.com/") == (
        "https://backend-staging.example.com"
    )
    assert validate_base_url("http://localhost:8000") == "http://localhost:8000"


@pytest.mark.parametrize("database_name", ["taxportal", "ross_house", ""])
def test_health_must_prove_dedicated_staging_database(database_name):
    with pytest.raises(SmokeFailure, match="dedicated staging"):
        run_smoke(
            "https://backend-staging.example.com",
            opener=opener_for(database_name=database_name),
        )


def test_anonymous_renewal_boundary_must_fail_closed():
    with pytest.raises(SmokeFailure, match="reject anonymous"):
        run_smoke(
            "https://backend-staging.example.com",
            opener=opener_for(anonymous_status=200),
        )


def test_read_only_preflight_passes_without_secret():
    assert run_smoke(
        "https://backend-staging.example.com",
        opener=opener_for(),
    ) == ["health-and-database", "renewal-auth-boundary"]


def test_admin_token_validates_bounded_read_model_without_printing_secret():
    items = [
        {"read_only": True, "integrity": "verified"},
        {"read_only": True, "integrity": "unavailable"},
    ]
    assert run_smoke(
        "https://backend-staging.example.com",
        admin_token="never-log-this",
        opener=opener_for(items=items),
    ) == [
        "health-and-database",
        "renewal-auth-boundary",
        "renewal-admin-read-model",
    ]


def test_invalid_admin_read_model_fails_closed():
    def bad_opener(request, timeout):
        if request.full_url.endswith("/api/health"):
            return opener_for()(request, timeout)
        if request.get_header("Authorization"):
            return Response(200, {"ok": True, "read_only": False, "items": []})
        return opener_for()(request, timeout)
    with pytest.raises(SmokeFailure, match="canonical read-only"):
        run_smoke(
            "https://backend-staging.example.com",
            admin_token="secret",
            opener=bad_opener,
        )
