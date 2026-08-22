"""
PHASE 0 — Payment Safety Baseline Tests
========================================
Run STANDALONE (fresh process):  python -m pytest tests/test_phase0_payments.py -v

SAFETY:
  * Forces MONGO_URL=localhost + DB_NAME=phase0_test_db BEFORE importing any
    rental module → NEVER touches the production Atlas database.
  * All Helcim HTTP calls are mocked → NO real charges, NO real API traffic.

Covers:
  - active processor resolution (helcim active / default stripe fallback)
  - invalid/unconfigured processor → HTTPException (no silent charge)
  - Helcim checkout creation (mocked) + server-side amount (client cannot set it)
  - ACH single-method init + automatic cc-ach fallback (Fee Saver accounts)
  - webhook signature-less dedupe (helcim /webhooks/hpay + clover) — duplicate=True
  - monthly duplicate payment guard ("Ya existe un pago registrado para este mes")
  - autopay duplicate guard (same-month last_attempt_date marker + day gating)
  - Zelle: duplicate receipt, wrong amount (ai_valid=False), approval idempotent,
    rejection (and double-reject rejected)
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── SAFETY: isolated local test DB + deterministic secrets (BEFORE imports) ──
os.environ["MONGO_URL"] = "mongodb://localhost:27017"
os.environ["DB_NAME"] = "phase0_test_db"
os.environ["ENVIRONMENT"] = "test"
os.environ["TENANT_JWT_SECRET"] = "phase0-test-secret-do-not-use-in-prod"
os.environ.setdefault("EMERGENT_LLM_KEY", "test-key-not-real")

from bson import ObjectId  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental import shared  # noqa: E402
from rental import payment_processors_router as ppr  # noqa: E402
from rental import zelle_router  # noqa: E402
from rental import autopay_cron  # noqa: E402

DB_NAME = "phase0_test_db"
SCREENSHOT_OK = "iVBORw0KGgo" + ("A" * 3000)  # >1000 chars → passes size guard


# ────────────────────────── fixtures / helpers ──────────────────────────

@pytest.fixture(scope="module")
def ctx():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=loop)
    db = client[DB_NAME]
    loop.run_until_complete(client.drop_database(DB_NAME))
    shared.set_db(db)

    app = FastAPI()
    app.include_router(ppr.router, prefix="/api")
    app.include_router(zelle_router.router, prefix="/api")

    # ── seed: tenant user + tenants doc + active contract ──
    async def seed():
        user_id = ObjectId()
        tenant_id = ObjectId()
        await db.app_users.insert_one({
            "_id": user_id, "email": "p0tenant@test.local", "name": "P0 Tenant",
            "role": "tenant", "password_hash": "x", "created_at": datetime.utcnow()})
        await db.tenants.insert_one({
            "_id": tenant_id, "email": "p0tenant@test.local", "name": "P0 Tenant",
            "app_user_id": str(user_id)})
        contract_id = ObjectId()
        await db.rental_contracts.insert_one({
            "_id": contract_id, "tenant_id": str(tenant_id),
            "status": "active", "rent_amount": 1100.0,
            "property_id": "prop-p0", "property_address": "121 Test Ave"})
        # Helcim configured + active
        await db.rental_config.insert_one({
            "type": "payment_processors", "active_processor": "helcim",
            "processors": {"helcim": {
                "environment": "production",
                "credentials": {
                    "production": {"api_token": "test-api-token",
                                   "webhook_verifier_token": "dGVzdC12ZXJpZmllcg=="},
                    "sandbox": {}}}}})
        await db.rental_config.insert_one({
            "type": "zelle_config", "enabled": True,
            "email": "pay@test.local", "holder_name": "Ross Test"})
        return str(user_id), str(tenant_id), str(contract_id)

    user_id, tenant_id, contract_id = loop.run_until_complete(seed())
    token = shared.create_marketplace_token(user_id, "p0tenant@test.local", "tenant")

    admin_id = ObjectId()
    loop.run_until_complete(db.app_users.insert_one({
        "_id": admin_id, "email": "p0admin@test.local", "name": "P0 Admin",
        "role": "admin", "password_hash": "x"}))
    admin_token = shared.create_marketplace_token(str(admin_id), "p0admin@test.local", "admin")

    yield {"loop": loop, "db": db, "app": app, "token": token,
           "admin_token": admin_token, "tenant_id": tenant_id,
           "user_id": user_id, "contract_id": contract_id}

    loop.run_until_complete(client.drop_database(DB_NAME))
    client.close()
    loop.close()


def run(ctx, coro):
    return ctx["loop"].run_until_complete(coro)


async def _req(ctx, method, path, token=None, body=None, headers=None):
    transport = ASGITransport(app=ctx["app"])
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.request(method, path, json=body, headers=h)
    return r


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Drop-in mock for httpx.AsyncClient — records every POST, returns queued
    responses. Guarantees ZERO real network traffic to Helcim."""
    calls: list = []
    queue: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, **k):
        import copy
        _FakeAsyncClient.calls.append({"url": url, "json": copy.deepcopy(json)})
        if _FakeAsyncClient.queue:
            return _FakeAsyncClient.queue.pop(0)
        return _FakeResponse(200, {"checkoutToken": "tok_test", "secretToken": "sec_test"})

    async def get(self, url, **k):
        _FakeAsyncClient.calls.append({"url": url, "json": None})
        return _FakeResponse(200, {})


@pytest.fixture()
def mock_helcim(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.queue = []
    monkeypatch.setattr(ppr.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


# ══════════════════════ PROCESSOR RESOLUTION ══════════════════════

def test_01_active_processor_is_helcim(ctx):
    name, creds = run(ctx, ppr.get_active_processor())
    assert name == "helcim"
    assert creds.get("api_token") == "test-api-token"
    assert creds.get("environment") == "production"


def test_02_default_processor_when_unconfigured(ctx):
    async def check():
        await ctx["db"].rental_config.update_one(
            {"type": "payment_processors"}, {"$rename": {"active_processor": "ap_bak"}})
        name, _ = await ppr.get_active_processor()
        await ctx["db"].rental_config.update_one(
            {"type": "payment_processors"}, {"$rename": {"ap_bak": "active_processor"}})
        return name
    assert run(ctx, check()) == "stripe"  # safe fallback, never crashes


def test_03_unconfigured_helcim_raises_no_silent_charge(ctx, mock_helcim):
    async def check():
        await ctx["db"].rental_config.update_one(
            {"type": "payment_processors"},
            {"$unset": {"processors.helcim.credentials.production.api_token": ""}})
        try:
            with pytest.raises(HTTPException):
                await ppr.create_hosted_checkout(
                    amount_cents=110000, reference="TEST", customer_email="x@y.z")
        finally:
            await ctx["db"].rental_config.update_one(
                {"type": "payment_processors"},
                {"$set": {"processors.helcim.credentials.production.api_token":
                          "test-api-token"}})
    run(ctx, check())
    assert mock_helcim.calls == []  # no network attempt without config


# ══════════════════════ CHECKOUT CREATION (MOCKED) ══════════════════════

def test_04_helcim_checkout_creation(ctx, mock_helcim):
    result = run(ctx, ppr.create_hosted_checkout(
        amount_cents=110000, reference="Renta P0", customer_email="p0@test.local"))
    assert len(mock_helcim.calls) == 1
    body = mock_helcim.calls[0]["json"]
    assert body["paymentType"] == "purchase"
    assert body["amount"] == 1100.0
    assert body["paymentMethod"] == "cc-ach"  # default shows both methods
    assert isinstance(result, dict)


def test_05_ach_only_payment_method(ctx, mock_helcim):
    run(ctx, ppr.create_hosted_checkout(
        amount_cents=110000, reference="Renta ACH", payment_method="ach"))
    assert mock_helcim.calls[0]["json"]["paymentMethod"] == "ach"


def test_06_ach_fallback_to_ccach_on_feesaver_reject(ctx, mock_helcim):
    mock_helcim.queue = [_FakeResponse(400, {"errors": "single method not allowed"}),
                         _FakeResponse(200, {"checkoutToken": "tok2", "secretToken": "sec2"})]
    run(ctx, ppr.create_hosted_checkout(
        amount_cents=110000, reference="Renta ACH FB", payment_method="ach"))
    assert len(mock_helcim.calls) == 2
    assert mock_helcim.calls[0]["json"]["paymentMethod"] == "ach"
    assert mock_helcim.calls[1]["json"]["paymentMethod"] == "cc-ach"  # auto-fallback


def test_07_invalid_payment_method_sanitized(ctx, mock_helcim):
    run(ctx, ppr.create_hosted_checkout(
        amount_cents=110000, reference="Renta X", payment_method="__evil__"))
    assert mock_helcim.calls[0]["json"]["paymentMethod"] == "cc-ach"


# ══════════════════════ TENANT CHECKOUT GUARDS ══════════════════════

def test_08_amount_is_server_side_from_contract(ctx, mock_helcim):
    """Client-sent amount MUST be ignored — server prices from the contract."""
    r = run(ctx, _req(ctx, "POST", "/api/tenant/create-checkout-payment",
                      token=ctx["token"], body={"amount": 1, "late_fee": 0}))
    assert r.status_code == 200, r.text
    assert mock_helcim.calls[0]["json"]["amount"] == 1100.0  # contract rent, NOT 1
    # cleanup the pending_checkout doc created by the endpoint
    run(ctx, ctx["db"].rental_payments.delete_many({"contract_id": ctx["contract_id"]}))


def test_09_monthly_duplicate_payment_guard(ctx, mock_helcim):
    now = datetime.utcnow()
    run(ctx, ctx["db"].rental_payments.insert_one({
        "contract_id": ctx["contract_id"], "status": "completed",
        "period_month": now.strftime("%B").lower(), "period_year": now.year,
        "total_paid": 1100.0}))
    r = run(ctx, _req(ctx, "POST", "/api/tenant/create-checkout-payment",
                      token=ctx["token"], body={"late_fee": 0}))
    assert r.status_code == 400
    assert "Ya existe un pago" in r.json()["detail"]
    assert mock_helcim.calls == []  # guard fires BEFORE any charge attempt


def test_10_no_contract_404(ctx, mock_helcim):
    async def check():
        uid = ObjectId()
        await ctx["db"].app_users.insert_one({
            "_id": uid, "email": "nocontract@test.local", "role": "tenant",
            "name": "NC", "password_hash": "x"})
        tok = shared.create_marketplace_token(str(uid), "nocontract@test.local", "tenant")
        return await _req(ctx, "POST", "/api/tenant/create-checkout-payment",
                          token=tok, body={})
    r = run(ctx, check())
    assert r.status_code == 404


# ══════════════════════ WEBHOOK DEDUPE ══════════════════════

def test_11_hpay_webhook_dedupe(ctx):
    async def check():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            h = {"webhook-id": "evt-p0-001", "webhook-timestamp": "1"}
            body = {"id": "txn-p0-1", "type": "cardTransaction"}
            # unsigned accepted only because verifier check needs webhook-signature;
            # dedupe is what we assert here
            r1 = await c.post("/api/webhooks/hpay", json=body, headers=h)
            r2 = await c.post("/api/webhooks/hpay", json=body, headers=h)
        return r1, r2
    r1, r2 = run(ctx, check())
    assert r1.status_code == 200 and not r1.json().get("duplicate")
    assert r2.status_code == 200 and r2.json().get("duplicate") is True


def test_12_clover_webhook_dedupe(ctx):
    async def check():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            body = {"id": "clv-evt-p0-1", "status": "paid"}
            r1 = await c.post("/api/webhooks/clover", json=body)
            r2 = await c.post("/api/webhooks/clover", json=body)
        return r1, r2
    r1, r2 = run(ctx, check())
    assert not r1.json().get("duplicate")
    assert r2.json().get("duplicate") is True


# ══════════════════════ AUTOPAY GUARDS ══════════════════════

def test_13_autopay_skips_if_already_attempted_this_month(ctx, mock_helcim):
    now = datetime.now(timezone.utc)
    cfg = {"enabled": True, "user_id": ctx["user_id"], "method_id": "m1",
           "day_of_month": now.day,
           "last_attempt_date": datetime(now.year, now.month, 1)}
    run(ctx, autopay_cron._process_autopay_for_config(ctx["db"], cfg))
    assert mock_helcim.calls == []  # gated: no charge attempt
    n = run(ctx, ctx["db"].rental_payments.count_documents(
        {"submitted_by": {"$regex": "autopay"}}))
    assert n == 0


def test_14_autopay_skips_before_configured_day(ctx, mock_helcim):
    now = datetime.now(timezone.utc)
    target = now.day + 1 if now.day < 28 else 28
    if target <= now.day:
        pytest.skip("end-of-month edge — gate not testable today")
    cfg = {"enabled": True, "user_id": ctx["user_id"], "method_id": "m1",
           "day_of_month": target, "last_attempt_date": None}
    run(ctx, autopay_cron._process_autopay_for_config(ctx["db"], cfg))
    assert mock_helcim.calls == []


# ══════════════════════ ZELLE ══════════════════════

@pytest.fixture()
def mock_zelle_ai(monkeypatch):
    state = {"result": {"valid": True, "summary": "ok", "amount_detected": 1100.0}}

    async def fake_ai(screenshot_b64, expected_amount, zelle_email, reference):
        return state["result"]

    monkeypatch.setattr(zelle_router, "_ai_validate", fake_ai)
    return state


def test_15_zelle_submit_ok(ctx, mock_zelle_ai):
    r = run(ctx, _req(ctx, "POST", "/api/tenant/zelle-submit", token=ctx["token"],
                      body={"screenshot_base64": SCREENSHOT_OK}))
    assert r.status_code == 200, r.text
    assert r.json()["ai_valid"] is True
    ctx["zelle_sub_id"] = r.json()["submission_id"]


def test_16_zelle_duplicate_receipt_blocked(ctx, mock_zelle_ai):
    r = run(ctx, _req(ctx, "POST", "/api/tenant/zelle-submit", token=ctx["token"],
                      body={"screenshot_base64": SCREENSHOT_OK}))
    assert r.status_code == 400
    assert "comprobante en revisión" in r.json()["detail"]


def test_17_zelle_wrong_amount_flagged_not_autoconfirmed(ctx, mock_zelle_ai):
    """AI flags amount mismatch → stored as pending_review with ai_valid=False.
    It must NEVER auto-complete a rental_payment."""
    async def check():
        await ctx["db"].zelle_submissions.delete_many({})  # fresh slate
        mock_zelle_ai_local = {"valid": False, "summary": "monto no coincide ($500 ≠ $1100)"}

        async def fake_ai(*a, **k):
            return mock_zelle_ai_local
        orig = zelle_router._ai_validate
        zelle_router._ai_validate = fake_ai
        try:
            r = await _req(ctx, "POST", "/api/tenant/zelle-submit", token=ctx["token"],
                           body={"screenshot_base64": SCREENSHOT_OK})
        finally:
            zelle_router._ai_validate = orig
        sub = await ctx["db"].zelle_submissions.find_one({})
        paid = await ctx["db"].rental_payments.count_documents(
            {"payment_method": "zelle", "status": "completed"})
        return r, sub, paid
    r, sub, paid = run(ctx, check())
    assert r.status_code == 200 and r.json()["ai_valid"] is False
    assert sub["status"] == "pending_review"  # requires human approval
    assert paid == 0  # nothing auto-completed


def test_18_zelle_admin_confirm_idempotent(ctx):
    async def check():
        sub = await ctx["db"].zelle_submissions.find_one({"status": "pending_review"})
        sid = str(sub["_id"])
        r1 = await _req(ctx, "POST", f"/api/admin/zelle-payments/{sid}/confirm",
                        token=ctx["admin_token"])
        r2 = await _req(ctx, "POST", f"/api/admin/zelle-payments/{sid}/confirm",
                        token=ctx["admin_token"])
        pays = await ctx["db"].rental_payments.count_documents(
            {"payment_method": "zelle", "status": "completed"})
        return r1, r2, pays
    r1, r2, pays = run(ctx, check())
    assert r1.status_code == 200 and r1.json()["receipt_number"].startswith("ZEL-")
    assert r2.status_code == 200 and r2.json().get("already") is True
    assert pays == 1  # double-confirm does NOT double-pay


def test_19_zelle_admin_reject(ctx, mock_zelle_ai):
    async def check():
        await ctx["db"].zelle_submissions.delete_many({})
        r = await _req(ctx, "POST", "/api/tenant/zelle-submit", token=ctx["token"],
                       body={"screenshot_base64": SCREENSHOT_OK})
        sid = r.json()["submission_id"]
        r1 = await _req(ctx, "POST", f"/api/admin/zelle-payments/{sid}/reject",
                        token=ctx["admin_token"], body={"reason": "ilegible"})
        r2 = await _req(ctx, "POST", f"/api/admin/zelle-payments/{sid}/reject",
                        token=ctx["admin_token"], body={"reason": "otra vez"})
        sub = await ctx["db"].zelle_submissions.find_one({"_id": ObjectId(sid)})
        return r1, r2, sub
    r1, r2, sub = run(ctx, check())
    assert r1.status_code == 200
    assert r2.status_code == 400  # cannot double-reject
    assert sub["status"] == "rejected" and sub["reject_reason"] == "ilegible"


def test_20_zelle_invalid_screenshot_rejected(ctx, mock_zelle_ai):
    r = run(ctx, _req(ctx, "POST", "/api/tenant/zelle-submit", token=ctx["token"],
                      body={"screenshot_base64": "tiny"}))
    assert r.status_code == 400
