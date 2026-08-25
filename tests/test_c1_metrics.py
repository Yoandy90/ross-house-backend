"""Tests — C1 Observability (auth_metrics). mongomock, CERO acceso a prod.
Run: cd /app/ross-house-backend && python -m pytest tests/test_c1_metrics.py -q
"""
import asyncio
import os
import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient

# Igual que test_phase0_auth: fijar el secret ANTES de importar rental.shared
# (constante de módulo) para no romper suites que corren juntas.
os.environ.setdefault("TENANT_JWT_SECRET", "phase0-test-secret-do-not-use-in-prod")

import rental.auth_metrics as am  # noqa: E402
import rental.shared as shared  # noqa: E402
from rental.auth_metrics import router, bump, VALID_METRICS  # noqa: E402

DB = AsyncMongoMockClient()["testdb"]

app = FastAPI()
app.include_router(router, prefix="/api")


async def fake_admin(request):
    if request.headers.get("x-test-admin") != "1":
        raise HTTPException(status_code=401, detail="No autorizado")
    return {"_id": "admin1", "email": "admin@test.com"}


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(am, "get_db", lambda: DB)
    monkeypatch.setattr(am, "auth_admin", fake_admin)
    yield


@pytest.fixture(autouse=True)
def clean():
    asyncio.run(DB.auth_metrics_daily.delete_many({}))
    yield


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_1_bump_increments_daily_counter():
    await bump("refresh_denied")
    await bump("refresh_denied")
    await bump("legacy_fallback_used")
    d = await DB.auth_metrics_daily.find_one({})
    assert d["refresh_denied"] == 2 and d["legacy_fallback_used"] == 1


@pytest.mark.asyncio
async def test_2_bump_invalid_metric_is_noop():
    await bump("not_a_metric")
    assert await DB.auth_metrics_daily.count_documents({}) == 0


@pytest.mark.asyncio
async def test_3_bump_never_raises_on_db_error(monkeypatch):
    monkeypatch.setattr(am, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    await bump("refresh_denied")  # no exception = pass


@pytest.mark.asyncio
async def test_4_endpoint_rbac_and_totals():
    await bump("refresh_bootstrap_ok")
    await bump("refresh_rotate_ok")
    async with client() as c:
        assert (await c.get("/api/admin/auth-metrics")).status_code == 401
        r = await c.get("/api/admin/auth-metrics", headers={"x-test-admin": "1"})
        assert r.status_code == 200
        d = r.json()
        assert d["totals"]["refresh_bootstrap_ok"] == 1
        assert d["totals"]["refresh_rotate_ok"] == 1
        assert set(d["totals"].keys()) == VALID_METRICS
        # sin PII: solo day + contadores
        for row in d["daily"]:
            assert set(row.keys()) <= ({"day"} | VALID_METRICS)


@pytest.mark.asyncio
async def test_5_sidless_accepted_and_rejected_metrics(monkeypatch):
    # aceptado (ventana de gracia)
    monkeypatch.delenv("REQUIRE_SESSION_SID", raising=False)
    await shared._validate_session_claims({"user_id": "u1"})  # sin sid → return
    d = await DB.auth_metrics_daily.find_one({})
    assert d["sidless_token_accepted"] == 1
    # rechazado (Phase C flag)
    monkeypatch.setenv("REQUIRE_SESSION_SID", "true")
    with pytest.raises(HTTPException) as e:
        await shared._validate_session_claims({"user_id": "u1"})
    assert e.value.status_code == 401
    d = await DB.auth_metrics_daily.find_one({})
    assert d["sidless_token_rejected"] == 1
