"""Tests 1099-NEC: summary, exclusión de métodos con tarjeta, W-9, payer, PDF, CSV."""
import asyncio
import os
import sys
import uuid
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

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental.shared import set_db, create_marketplace_token  # noqa: E402
from rental.tax_1099_router import router as tax_router  # noqa: E402

TEST_TAG = "__1099_pytest__"
YEAR = 2025  # año fijo para no chocar con pagos reales del año corriente


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
    app.include_router(tax_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        token = create_marketplace_token(str(admin["_id"]), admin["email"], "admin")
        pid = str(uuid.uuid4())
        await db.service_providers.insert_one({
            "_id": pid, "name": f"Plomero {TEST_TAG}",
            "email": "plomero-1099@example.com", "status": "approved",
        })
        pays = [
            # reportables: 500 zelle + 400 check = 900 → requiere 1099
            {"amount": 500, "method": "zelle", "status": "paid",
             "paid_at": datetime(YEAR, 3, 10)},
            {"amount": 400, "method": "check", "status": "paid",
             "paid_at": datetime(YEAR, 7, 2)},
            # excluidos: tarjeta y venmo (1099-K)
            {"amount": 300, "method": "stripe_card", "status": "paid",
             "paid_at": datetime(YEAR, 5, 5)},
            {"amount": 200, "method": "venmo", "status": "paid",
             "paid_at": datetime(YEAR, 6, 6)},
            # pending no cuenta
            {"amount": 999, "method": "cash", "status": "pending",
             "paid_at": datetime(YEAR, 8, 8)},
            # otro año no cuenta
            {"amount": 888, "method": "cash", "status": "paid",
             "paid_at": datetime(YEAR - 1, 8, 8)},
        ]
        for p in pays:
            await db.provider_payments.insert_one({
                "_id": str(uuid.uuid4()), "provider_id": pid,
                "provider_name": f"Plomero {TEST_TAG}", **p})
        prev_payer = await db.app_settings.find_one({"_id": "tax_1099"})
        return token, pid, prev_payer

    token, pid, prev_payer = loop.run_until_complete(_setup())
    yield {"app": app, "db": db, "token": token, "loop": loop, "pid": pid}

    async def _teardown():
        await db.service_providers.delete_many({"name": {"$regex": TEST_TAG}})
        await db.provider_payments.delete_many({"provider_name": {"$regex": TEST_TAG}})
        # restaurar config del pagador (no dejar EIN falso)
        if prev_payer:
            await db.app_settings.replace_one({"_id": "tax_1099"}, prev_payer, upsert=True)
        else:
            await db.app_settings.delete_one({"_id": "tax_1099"})

    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.request(method, path, json=json, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def _my_row(ctx):
    r = _req(ctx, "GET", f"/api/admin/1099/summary?year={YEAR}")
    assert r.status_code == 200, r.text
    rows = [x for x in r.json()["rows"] if x["provider_id"] == ctx["pid"]]
    assert rows, "proveedor de prueba no aparece en summary"
    return rows[0], r.json()


def test_01_summary_amounts(ctx):
    row, d = _my_row(ctx)
    assert row["reportable"] == 900.0     # zelle + check
    assert row["excluded"] == 500.0       # stripe_card + venmo
    assert row["needs_1099"] is True
    assert row["w9_complete"] is False


def test_02_save_w9_and_validation(ctx):
    r = _req(ctx, "PUT", f"/api/admin/1099/providers/{ctx['pid']}/w9",
             json={"tin": "12345"})
    assert r.status_code == 400  # TIN inválido
    r = _req(ctx, "PUT", f"/api/admin/1099/providers/{ctx['pid']}/w9", json={
        "legal_name": "Juan Plomero", "tin_type": "ssn", "tin": "123-45-6789",
        "address": "1 Pipe St", "city": "Dumas", "state": "TX", "zip": "79029"})
    assert r.status_code == 200, r.text
    row, _ = _my_row(ctx)
    assert row["w9_complete"] is True
    assert row["w9"]["tin_masked"] == "***-**-6789"


def test_03_payer_save(ctx):
    r = _req(ctx, "PUT", "/api/admin/1099/payer", json={"ein": "99-8877665"})
    assert r.status_code == 200
    assert r.json()["payer"]["ein"] == "99-8877665"
    _, d = _my_row(ctx)
    assert d["payer_complete"] is True


def test_04_pdf(ctx):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.get(f"/api/admin/1099/providers/{ctx['pid']}/pdf?year={YEAR}",
                               headers={"Authorization": f"Bearer {ctx['token']}"})
    r = ctx["loop"].run_until_complete(_do())
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 1500
    # año sin pagos → 400
    async def _do2():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.get(f"/api/admin/1099/providers/{ctx['pid']}/pdf?year=2020",
                               headers={"Authorization": f"Bearer {ctx['token']}"})
    assert ctx["loop"].run_until_complete(_do2()).status_code == 400


def test_05_csv(ctx):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.get(f"/api/admin/1099/export/csv?year={YEAR}",
                               headers={"Authorization": f"Bearer {ctx['token']}"})
    r = ctx["loop"].run_until_complete(_do())
    assert r.status_code == 200
    text = r.text
    assert "Juan Plomero" in text
    assert "900.00" in text
    assert "123456789" in text  # TIN completo para e-file


def test_06_unauthorized(ctx):
    assert _req(ctx, "GET", "/api/admin/1099/summary", auth=False).status_code == 401
