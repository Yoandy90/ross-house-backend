from datetime import datetime

from fastapi import FastAPI

from rental.auth_metrics import router as pre_tenant_router
from rental.tenant_dashboard_security_router import _next_due
from rental.tenant_router import router as historical_tenant_router


def test_secure_dashboard_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    matches = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/tenant/dashboard"
        and "GET" in getattr(route, "methods", set())
    ]
    assert len(matches) == 2
    assert matches[0].name == "secure_tenant_dashboard"
    assert matches[1].name == "tenant_dashboard"


def test_server_mounts_auth_metrics_before_tenant_router():
    source = open("server.py", encoding="utf-8").read()
    pre = 'app.include_router(auth_metrics_router, prefix="/api")'
    legacy = 'app.include_router(tenant_router, prefix="/api")'
    assert source.count(pre) == 1
    assert source.count(legacy) == 1
    assert source.index(pre) < source.index(legacy)


def test_due_date_clamps_short_months():
    assert _next_due(datetime(2026, 2, 10), 31).strftime("%Y-%m-%d") == "2026-02-28"
    assert _next_due(datetime(2026, 2, 28), 31).strftime("%Y-%m-%d") == "2026-02-28"
    assert _next_due(datetime(2026, 3, 31), 31).strftime("%Y-%m-%d") == "2026-03-31"


def test_due_date_invalid_value_fails_to_safe_day_one():
    assert _next_due(datetime(2026, 8, 2), "bad").strftime("%Y-%m-%d") == "2026-09-01"
