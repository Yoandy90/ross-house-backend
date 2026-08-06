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


def test_07_large_unmatched_alert(ctx):
    """El cron alerta movimientos >= $500 sin conciliar y los marca alerted."""
    import rental.plaid_sync_cron as psc
    db, loop = ctx["db"], ctx["loop"]
    sent = []

    async def fake_send(to, subject, body, body_html="", from_email=""):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    import rental.email_inbox_router as eir
    orig = eir._send_via_sendgrid
    eir._send_via_sendgrid = fake_send

    async def _run():
        await db.bank_transactions.insert_many([
            {"transaction_id": f"big-{TEST_TAG}", "item_id": "x",
             "name": f"WIRE DESCONOCIDO {TEST_TAG}", "amount": 1200.0,
             "date": datetime.utcnow(), "match": {"status": "unmatched"}},
            {"transaction_id": f"small-{TEST_TAG}", "item_id": "x",
             "name": f"CAFE {TEST_TAG}", "amount": 12.0,
             "date": datetime.utcnow(), "match": {"status": "unmatched"}},
        ])
        n = await psc.check_large_unmatched(db)
        big = await db.bank_transactions.find_one({"transaction_id": f"big-{TEST_TAG}"})
        small = await db.bank_transactions.find_one({"transaction_id": f"small-{TEST_TAG}"})
        # segunda corrida no re-alerta
        n2 = await psc.check_large_unmatched(db)
        return n, n2, big, small

    try:
        n, n2, big, small = loop.run_until_complete(_run())
    finally:
        eir._send_via_sendgrid = orig
    assert n >= 1 and len(sent) >= 1
    assert any("WIRE DESCONOCIDO" in s["body"] for s in sent)
    assert big["alerted"] is True
    assert small.get("alerted") is not True  # bajo el umbral
    # el big no se re-alerta en corridas posteriores
    bodies_after_first = [s["body"] for s in sent[1:]]
    assert all("WIRE DESCONOCIDO" not in b for b in bodies_after_first)


def test_08_ai_suggestion_flow(ctx):
    """AI sugiere match aproximado (mock) y accept lo confirma."""
    db, loop = ctx["db"], ctx["loop"]

    async def _seed():
        await db.bank_transactions.insert_one({
            "transaction_id": f"sug-{TEST_TAG}", "item_id": "x",
            "name": f"CHECK 1042 {TEST_TAG}", "amount": 260.00,
            "date": datetime.utcnow(), "match": {"status": "unmatched"}})
        await db.provider_payments.insert_one({
            "_id": f"pp2-{TEST_TAG}", "provider_id": "x",
            "provider_name": f"Electricista {TEST_TAG}", "amount": 275.50,
            "method": "check", "status": "paid", "paid_at": datetime.utcnow()})
    loop.run_until_complete(_seed())

    class FakeChat:
        def __init__(self, *a, **k):
            self.system = k.get("system_message", "")

        def with_model(self, *a, **k):
            return self

        async def send_message(self, msg):
            if "Schedule E" in self.system:
                return f'[{{"id": "sug-{TEST_TAG}", "category": "repairs"}}]'
            return (f'[{{"id": "sug-{TEST_TAG}", "ref": "provider_payments:pp2-{TEST_TAG}",'
                    f'"confidence": 88, "reason": "Cheque 1042, difiere $15.50 por fee"}}]')

    import emergentintegrations.llm.chat as ll
    orig = ll.LlmChat
    ll.LlmChat = FakeChat
    try:
        from rental.plaid_router import run_ai_analysis
        r = loop.run_until_complete(run_ai_analysis())
    finally:
        ll.LlmChat = orig
    assert r["suggested"] >= 1

    async def _get():
        return await db.bank_transactions.find_one({"transaction_id": f"sug-{TEST_TAG}"})
    tx = loop.run_until_complete(_get())
    assert tx["ai_category"] == "repairs"
    assert tx["match_suggestion"]["confidence"] == 88

    # aceptar la sugerencia → matched
    resp = _req(ctx, "POST", f"/api/admin/plaid/transactions/sug-{TEST_TAG}/suggestion",
                json={"action": "accept"})
    assert resp.status_code == 200, resp.text
    tx = loop.run_until_complete(_get())
    assert tx["match"]["status"] == "matched"
    assert tx["match"]["ai_suggested"] is True
    assert "match_suggestion" not in tx
    # accept de nuevo → 400 (ya no hay sugerencia)
    assert _req(ctx, "POST", f"/api/admin/plaid/transactions/sug-{TEST_TAG}/suggestion",
                json={"action": "accept"}).status_code == 400
