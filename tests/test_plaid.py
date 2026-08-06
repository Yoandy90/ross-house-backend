"""Tests Plaid (Fase 3): flujo sandbox real (link→exchange→sync) + motor de auto-match."""
import asyncio
import os
import sys
from datetime import datetime, timedelta

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
from rental.plaid_router import router as plaid_router, _plaid  # noqa: E402

TEST_TAG = "__plaid_pytest__"


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
    app.include_router(plaid_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        return create_marketplace_token(str(admin["_id"]), admin["email"], "admin")

    token = loop.run_until_complete(_setup())
    state = {"app": app, "db": db, "token": token, "loop": loop}
    yield state

    async def _teardown():
        # desvincular items sandbox creados por el test y limpiar transacciones
        item_id = state.get("item_id")
        if item_id:
            it = await db.plaid_items.find_one({"item_id": item_id})
            if it:
                try:
                    from plaid.model.item_remove_request import ItemRemoveRequest
                    _plaid().item_remove(ItemRemoveRequest(access_token=it["access_token"]))
                except Exception:
                    pass
            await db.plaid_items.delete_many({"item_id": item_id})
            await db.bank_transactions.delete_many({"item_id": item_id})
        await db.bank_transactions.delete_many({"name": {"$regex": TEST_TAG}})
        await db.provider_payments.delete_many({"provider_name": {"$regex": TEST_TAG}})

    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
            return await c.request(method, path, json=json, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def test_01_link_token(ctx):
    r = _req(ctx, "POST", "/api/admin/plaid/link-token")
    assert r.status_code == 200, r.text
    assert r.json()["link_token"].startswith("link-sandbox-")


def test_02_sandbox_exchange_and_sync(ctx):
    """Crea un item sandbox sin browser (sandbox_public_token_create) y sincroniza."""
    from plaid.model.products import Products
    from plaid.model.sandbox_public_token_create_request import (
        SandboxPublicTokenCreateRequest,
    )
    resp = _plaid().sandbox_public_token_create(SandboxPublicTokenCreateRequest(
        institution_id="ins_109508",  # First Platypus Bank
        initial_products=[Products("transactions")]))
    public_token = resp["public_token"]

    r = _req(ctx, "POST", "/api/admin/plaid/exchange",
             json={"public_token": public_token,
                   "institution_name": f"First Platypus {TEST_TAG}"})
    assert r.status_code == 200, r.text
    ctx["item_id"] = r.json()["item_id"]
    assert len(r.json()["accounts"]) > 0

    # el primer sync puede tardar en poblarse — reintentar
    import time
    imported = 0
    for _ in range(6):
        r = _req(ctx, "POST", "/api/admin/plaid/sync")
        assert r.status_code == 200, r.text
        imported = r.json()["imported"]
        if imported > 0:
            break
        time.sleep(5)
    assert imported > 0, "sandbox no devolvió transacciones"

    r = _req(ctx, "GET", "/api/admin/plaid/transactions?limit=5")
    assert r.status_code == 200
    assert len(r.json()["transactions"]) > 0
    assert "unmatched" in r.json()["counts"]


def test_03_accounts_list(ctx):
    r = _req(ctx, "GET", "/api/admin/plaid/accounts")
    assert r.status_code == 200
    items = r.json()["items"]
    mine = [i for i in items if i["item_id"] == ctx.get("item_id")]
    assert mine and mine[0].get("access_token") is None  # nunca exponer el token
    assert mine[0]["last_synced_at"]


def test_04_auto_match_engine(ctx):
    """Inserta una transacción bancaria y su pago interno equivalente → deben cruzar."""
    db, loop = ctx["db"], ctx["loop"]

    async def _seed():
        await db.bank_transactions.insert_one({
            "transaction_id": f"tx-{TEST_TAG}", "item_id": ctx.get("item_id", "x"),
            "name": f"ZELLE PLOMERO {TEST_TAG}", "amount": 275.50,  # salida
            "date": datetime.utcnow() - timedelta(days=1), "pending": False,
            "match": {"status": "unmatched"}, "created_at": datetime.utcnow()})
        await db.provider_payments.insert_one({
            "_id": f"pp-{TEST_TAG}", "provider_id": "x",
            "provider_name": f"Plomero {TEST_TAG}", "amount": 275.50,
            "method": "zelle", "status": "paid", "paid_at": datetime.utcnow()})
    loop.run_until_complete(_seed())

    r = _req(ctx, "POST", "/api/admin/plaid/reconcile")
    assert r.status_code == 200
    assert r.json()["auto_matched"] >= 1

    async def _check():
        return await db.bank_transactions.find_one({"transaction_id": f"tx-{TEST_TAG}"})
    tx = loop.run_until_complete(_check())
    assert tx["match"]["status"] == "matched"
    assert tx["match"]["type"] == "Pago proveedor"
    assert TEST_TAG in tx["match"]["ref_desc"]


def test_05_manual_ignore(ctx):
    r = _req(ctx, "POST", f"/api/admin/plaid/transactions/tx-{TEST_TAG}/status",
             json={"status": "ignored"})
    assert r.status_code == 200
    r = _req(ctx, "POST", f"/api/admin/plaid/transactions/tx-{TEST_TAG}/status",
             json={"status": "bad"})
    assert r.status_code == 400


def test_06_unauthorized(ctx):
    assert _req(ctx, "GET", "/api/admin/plaid/accounts", auth=False).status_code == 401
    assert _req(ctx, "POST", "/api/admin/plaid/sync", auth=False).status_code == 401
