"""
Tests for the NEW provider-agnostic Tenant Screening flow.

Endpoints under test (mounted on a minimal local app — same router deployed
to Railway):
  POST  /api/admin/rental-applications/{id}/screening/request
  PATCH /api/admin/rental-applications/{id}/screening
  POST  /api/admin/rental-applications/{id}/screening/report
  GET   /api/admin/rental-applications/{id}/screening/report
  GET   /api/admin/rental-applications/{id}   (screening included in serializer)

Seeds a clearly-marked TEST application in rental_applications and removes it
afterwards. Emails are NOT sent (send_email=False).
"""
import asyncio
import base64
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from bson import ObjectId  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental.shared import set_db, create_marketplace_token  # noqa: E402
from rental.screening_router import router as screening_router  # noqa: E402
from rental.finances_router import router as finances_router  # noqa: E402

TEST_MARK = "__screening_pytest__"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def ctx(loop):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=loop)
    db = client[os.environ.get("DB_NAME", "taxportal")]
    set_db(db)

    app = FastAPI()
    app.include_router(screening_router, prefix="/api")
    app.include_router(finances_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        assert admin, "No admin user in app_users"
        token = create_marketplace_token(str(admin["_id"]), admin["email"], "admin")
        res = await db.rental_applications.insert_one({
            "name": "Pytest Screening Test",
            "email": "pytest-screening@example.com",
            "phone": "0000000000",
            "property_interest": TEST_MARK,
            "status": "new",
            "source": "pytest",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })
        return token, str(res.inserted_id)

    token, app_id = loop.run_until_complete(_setup())
    yield {"app": app, "db": db, "token": token, "app_id": app_id, "loop": loop}

    async def _teardown():
        await db.rental_applications.delete_many({"property_interest": TEST_MARK})
        await db.screening_reports.delete_many({"application_id": app_id})

    loop.run_until_complete(_teardown())
    client.close()


def _call(ctx, method, path, json=None, expect_json=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.request(
                method, path, json=json,
                headers={"Authorization": f"Bearer {ctx['token']}"},
            )
            return r
    return ctx["loop"].run_until_complete(_do())


def test_01_request_screening(ctx):
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{ctx['app_id']}/screening/request",
              json={"provider": "smartmove", "screening_link": "https://mysmartmove.com/test", "send_email": False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["success"] is True
    assert d["screening"]["status"] == "requested"
    assert d["screening"]["provider"] == "smartmove"
    assert d["email_sent"] is False


def test_02_application_auto_moves_to_reviewing(ctx):
    r = _call(ctx, "GET", f"/api/admin/rental-applications/{ctx['app_id']}")
    assert r.status_code == 200, r.text
    a = r.json()["application"]
    assert a["status"] == "reviewing"
    assert a["screening"]["status"] == "requested"


def test_03_invalid_provider_rejected(ctx):
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{ctx['app_id']}/screening/request",
              json={"provider": "equifax", "send_email": False})
    assert r.status_code == 400


def test_04_invalid_link_rejected(ctx):
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{ctx['app_id']}/screening/request",
              json={"provider": "smartmove", "screening_link": "javascript:alert(1)", "send_email": False})
    assert r.status_code == 400


def test_05_update_status_and_results(ctx):
    r = _call(ctx, "PATCH", f"/api/admin/rental-applications/{ctx['app_id']}/screening",
              json={"status": "completed", "results": {
                  "credit_score": 712, "income_verified": True,
                  "criminal_records": "clean", "eviction_records": "clean",
                  "recommendation": "approve", "notes": "3.2x ingreso/renta",
              }})
    assert r.status_code == 200, r.text
    s = r.json()["screening"]
    assert s["status"] == "completed"
    assert s["completed_at"]
    assert s["results"]["credit_score"] == 712
    assert s["results"]["income_verified"] is True
    assert s["results"]["recommendation"] == "approve"


def test_06_invalid_results_rejected(ctx):
    for bad in [
        {"results": {"credit_score": 9999}},
        {"results": {"credit_score": "abc"}},
        {"results": {"recommendation": "maybe"}},
        {"results": {"criminal_records": "unknown"}},
        {"status": "done"},
    ]:
        r = _call(ctx, "PATCH", f"/api/admin/rental-applications/{ctx['app_id']}/screening", json=bad)
        assert r.status_code == 400, f"{bad} -> {r.status_code}"


def test_07_upload_and_download_report(ctx):
    pdf = b"%PDF-1.4 pytest screening report"
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{ctx['app_id']}/screening/report",
              json={"filename": "reporte.pdf", "content_type": "application/pdf",
                    "data_base64": "data:application/pdf;base64," + base64.b64encode(pdf).decode()})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["size"] == len(pdf)

    r = _call(ctx, "GET", f"/api/admin/rental-applications/{ctx['app_id']}/screening/report")
    assert r.status_code == 200
    assert r.content == pdf
    assert "reporte.pdf" in r.headers.get("content-disposition", "")


def test_08_bad_base64_rejected(ctx):
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{ctx['app_id']}/screening/report",
              json={"filename": "x.pdf", "data_base64": "!!!not-base64!!!"})
    assert r.status_code == 400


def test_09_patch_without_screening_rejected(ctx):
    async def _mk():
        res = await ctx["db"].rental_applications.insert_one({
            "name": "Pytest NoScreening", "email": "x@example.com",
            "property_interest": TEST_MARK, "status": "new",
            "created_at": datetime.utcnow(),
        })
        return str(res.inserted_id)
    other_id = ctx["loop"].run_until_complete(_mk())
    r = _call(ctx, "PATCH", f"/api/admin/rental-applications/{other_id}/screening",
              json={"status": "completed"})
    assert r.status_code == 400


def test_10_unauthorized_without_token(ctx):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(f"/api/admin/rental-applications/{ctx['app_id']}/screening/request", json={})
    r = ctx["loop"].run_until_complete(_do())
    assert r.status_code == 401


def test_11_legacy_waived_schema_serialized(ctx):
    async def _mk():
        res = await ctx["db"].rental_applications.insert_one({
            "name": "Pytest Waived", "email": "w@example.com",
            "property_interest": TEST_MARK, "status": "approved",
            "screening": {"type": "waived", "reason": "Familiar directo del propietario"},
            "created_at": datetime.utcnow(),
        })
        return str(res.inserted_id)
    waived_id = ctx["loop"].run_until_complete(_mk())

    r = _call(ctx, "GET", f"/api/admin/rental-applications/{waived_id}")
    assert r.status_code == 200, r.text
    s = r.json()["application"]["screening"]
    assert s["status"] == "waived"
    assert s["reason"] == "Familiar directo del propietario"

    # PATCH / report upload must be rejected on waived screening
    r = _call(ctx, "PATCH", f"/api/admin/rental-applications/{waived_id}/screening",
              json={"status": "completed"})
    assert r.status_code == 400
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{waived_id}/screening/report",
              json={"filename": "x.pdf", "data_base64": base64.b64encode(b"x").decode()})
    assert r.status_code == 400

    # But re-requesting a real screening over a waived one is allowed
    r = _call(ctx, "POST", f"/api/admin/rental-applications/{waived_id}/screening/request",
              json={"provider": "boomscreen", "send_email": False})
    assert r.status_code == 200, r.text
    assert r.json()["screening"]["status"] == "requested"
