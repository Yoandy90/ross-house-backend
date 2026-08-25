"""Tests — Manual Payment Confirmations (mongomock, CERO acceso a prod).
Run: cd /app/ross-house-backend && python -m pytest tests/test_manual_confirmations.py -q
"""
import asyncio
import pytest
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient

import rental.shared as shared
import rental.manual_confirmations_router as mcr
from rental.manual_confirmations_router import router

DB = AsyncMongoMockClient()["testdb"]
TENANT_ID = ObjectId()
OTHER_ID = ObjectId()
CONTRACT_ID = ObjectId()

app = FastAPI()
app.include_router(router, prefix="/api")


async def fake_tenant(request):
    tid = request.headers.get("x-test-tenant", str(TENANT_ID))
    return {"_id": ObjectId(tid), "name": "QA Tenant", "email": "qa@test.com"}

async def fake_admin(request):
    if request.headers.get("x-test-admin") != "1":
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="No autorizado")
    return {"_id": "admin1", "email": "admin@test.com"}

async def fake_audit(**kw):
    return None


PUSHES: list = []  # (kind, args) — captura de pushes para asserts


async def fake_push_user(user_id, title, body, data=None):
    PUSHES.append(("user", user_id, title, body, data or {}))


async def fake_push_admins(title, body, data=None):
    PUSHES.append(("admins", title, body, data or {}))


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(mcr, "get_db", lambda: DB)
    monkeypatch.setattr(mcr, "auth_tenant_flex", fake_tenant)
    monkeypatch.setattr(mcr, "auth_admin", fake_admin)
    monkeypatch.setattr(mcr, "send_rental_push_to_user", fake_push_user)
    monkeypatch.setattr(mcr, "send_rental_push_to_admins", fake_push_admins)
    import rental.admin_nav_router as anr
    monkeypatch.setattr(anr, "get_db", lambda: DB)
    monkeypatch.setattr(anr, "auth_admin", fake_admin)
    import rental.security as sec
    monkeypatch.setattr(sec, "audit_log", fake_audit)
    PUSHES.clear()
    yield


@pytest.fixture(autouse=True)
def seed():
    async def _s():
        await DB.rental_contracts.delete_many({})
        await DB.rental_payments.delete_many({})
        await DB.manual_payment_confirmations.delete_many({})
        await DB.rental_notifications.delete_many({})
        await DB.rental_contracts.insert_one({
            "_id": CONTRACT_ID, "tenant_id": str(TENANT_ID), "status": "active",
            "property_id": "prop1", "property_address": "121 Oak Ave", "rent_amount": 1100})
    asyncio.run(_s())
    yield


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def submit(c, method="cashapp", **over):
    body = {"method": method, "amount": 1100, "reference": over.pop("reference", "REF1"),
            "period_month": 6, "period_year": 2026, **over}
    return await c.post("/api/tenant/manual-payment/confirm", json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["cashapp", "money_order", "bank_transfer"])
async def test_1_2_3_submit_each_method(method):
    async with client() as c:
        r = await submit(c, method=method, reference=f"R-{method}")
        assert r.status_code == 200 and r.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_4_requires_active_lease():
    await DB.rental_contracts.update_one({"_id": CONTRACT_ID}, {"$set": {"status": "terminated"}})
    async with client() as c:
        r = await submit(c)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_5_no_completed_payment_on_submit():
    async with client() as c:
        await submit(c)
    assert await DB.rental_payments.count_documents({}) == 0


@pytest.mark.asyncio
async def test_6_tenant_id_derived_from_session_not_body():
    async with client() as c:
        r = await c.post("/api/tenant/manual-payment/confirm", json={
            "method": "cashapp", "amount": 1100, "tenant_id": "SPOOFED",
            "period_month": 6, "period_year": 2026})
        assert r.status_code == 200
    d = await DB.manual_payment_confirmations.find_one({})
    assert d["tenant_id"] == str(TENANT_ID)


@pytest.mark.asyncio
async def test_7_tenant_cannot_read_others():
    async with client() as c:
        await submit(c)
        r = await c.get("/api/tenant/manual-payment/confirmations",
                        headers={"x-test-tenant": str(OTHER_ID)})
        assert r.status_code == 200 and r.json()["confirmations"] == []


@pytest.mark.asyncio
async def test_8_admin_list_and_rbac():
    async with client() as c:
        await submit(c)
        assert (await c.get("/api/admin/manual-payment/confirmations")).status_code == 401
        r = await c.get("/api/admin/manual-payment/confirmations", headers={"x-test-admin": "1"})
        assert r.status_code == 200 and len(r.json()["confirmations"]) == 1


@pytest.mark.asyncio
async def test_9_10_approve_creates_one_payment_idempotent():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        h = {"x-test-admin": "1"}
        r1 = await c.post(f"/api/admin/manual-payment/confirmations/{cid}/approve", json={}, headers=h)
        r2 = await c.post(f"/api/admin/manual-payment/confirmations/{cid}/approve", json={}, headers=h)
        assert r1.status_code == 200 and r2.json().get("already") is True
    assert await DB.rental_payments.count_documents({"status": "completed"}) == 1


@pytest.mark.asyncio
async def test_11_duplicate_month_method_blocked():
    async with client() as c:
        await submit(c)
        r = await submit(c, reference="OTRA")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_12_reject_creates_no_payment_and_reason_visible():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        r = await c.post(f"/api/admin/manual-payment/confirmations/{cid}/reject",
                         json={"reason": "Monto incorrecto"}, headers={"x-test-admin": "1"})
        assert r.status_code == 200
        mine = (await c.get("/api/tenant/manual-payment/confirmations")).json()["confirmations"]
        assert mine[0]["status"] == "rejected" and mine[0]["reject_reason"] == "Monto incorrecto"
    assert await DB.rental_payments.count_documents({}) == 0


@pytest.mark.asyncio
async def test_13_receipt_admin_only_and_never_in_lists():
    async with client() as c:
        cid = (await submit(c, receipt_base64="aGVsbG8=")).json()["id"]
        mine = (await c.get("/api/tenant/manual-payment/confirmations")).json()["confirmations"]
        assert "receipt_base64" not in mine[0] and mine[0]["has_receipt"] is True
        assert (await c.get(f"/api/admin/manual-payment/confirmations/{cid}/receipt")).status_code == 401
        ok = await c.get(f"/api/admin/manual-payment/confirmations/{cid}/receipt", headers={"x-test-admin": "1"})
        assert ok.json()["receipt_base64"] == "aGVsbG8="


@pytest.mark.asyncio
async def test_14_invalid_file_rejected():
    async with client() as c:
        r = await submit(c, receipt_base64="<script>alert(1)</script>")
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_15_already_paid_month_cannot_approve():
    await DB.rental_payments.insert_one({
        "contract_id": str(CONTRACT_ID), "period_month": 6, "period_year": 2026,
        "status": "completed", "created_at": datetime.now(timezone.utc)})
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        r = await c.post(f"/api/admin/manual-payment/confirmations/{cid}/approve",
                         json={}, headers={"x-test-admin": "1"})
        assert r.status_code == 409

# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICACIONES INTERNAS + BADGE (nav-summary) — tests 16-25
# ═══════════════════════════════════════════════════════════════════════════
from rental.admin_nav_router import router as nav_router  # noqa: E402

nav_app = FastAPI()
nav_app.include_router(nav_router, prefix="/api")


def nav_client():
    return AsyncClient(transport=ASGITransport(app=nav_app), base_url="http://t")


async def nav_count():
    async with nav_client() as c:
        r = await c.get("/api/admin/nav-summary", headers={"x-test-admin": "1"})
        assert r.status_code == 200
        return r.json()


@pytest.mark.asyncio
async def test_16_submit_creates_admin_notification():
    async with client() as c:
        await submit(c)
    n = await DB.rental_notifications.find_one({"target": "admin"})
    assert n is not None
    assert n["type"] == "manual_confirmation_new"
    assert n["title"] == "Nueva confirmación de pago recibida"
    assert "QA Tenant" in n["body"] and "Cash App" in n["body"] and "121 Oak Ave" in n["body"]
    # push interno a admins también disparado (best-effort)
    assert any(p[0] == "admins" for p in PUSHES)


@pytest.mark.asyncio
async def test_17_approve_creates_tenant_notification():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        await c.post(f"/api/admin/manual-payment/confirmations/{cid}/approve",
                     json={}, headers={"x-test-admin": "1"})
    n = await DB.rental_notifications.find_one({"type": "manual_confirmation_approved"})
    assert n is not None and n["user_id"] == str(TENANT_ID)
    assert n["title"] == "Tu confirmación de pago fue aprobada."


@pytest.mark.asyncio
async def test_18_reject_creates_tenant_notification_with_reason():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        await c.post(f"/api/admin/manual-payment/confirmations/{cid}/reject",
                     json={"reason": "Monto incorrecto"}, headers={"x-test-admin": "1"})
    n = await DB.rental_notifications.find_one({"type": "manual_confirmation_rejected"})
    assert n is not None and n["user_id"] == str(TENANT_ID)
    assert n["title"] == "Tu confirmación de pago fue rechazada."
    assert "Monto incorrecto" in n["body"]


@pytest.mark.asyncio
async def test_19_receipt_never_in_notifications_or_push():
    async with client() as c:
        cid = (await submit(c, receipt_base64="aGVsbG8=")).json()["id"]
        h = {"x-test-admin": "1"}
        await c.post(f"/api/admin/manual-payment/confirmations/{cid}/approve", json={}, headers=h)
    async for n in DB.rental_notifications.find({}):
        blob = str(n)
        assert "receipt_base64" not in blob and "aGVsbG8=" not in blob
    for p in PUSHES:
        assert "aGVsbG8=" not in str(p) and "receipt_base64" not in str(p)


@pytest.mark.asyncio
async def test_20_tenant_cannot_spoof_notification_recipient():
    async with client() as c:
        await c.post("/api/tenant/manual-payment/confirm", json={
            "method": "cashapp", "amount": 1100, "period_month": 6, "period_year": 2026,
            "tenant_id": "SPOOFED", "user_id": "SPOOFED", "target": "all"})
    n = await DB.rental_notifications.find_one({})
    assert n["target"] == "admin" and "user_id" not in n
    assert "SPOOFED" not in str(n)


@pytest.mark.asyncio
async def test_21_badge_increments_on_submit():
    before = await nav_count()
    assert before["manual_confirmations"] == 0
    async with client() as c:
        await submit(c)
    after = await nav_count()
    assert after["manual_confirmations"] == 1
    assert after["total"] == before["total"] + 1


@pytest.mark.asyncio
async def test_22_approve_decrements_badge():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        assert (await nav_count())["manual_confirmations"] == 1
        await c.post(f"/api/admin/manual-payment/confirmations/{cid}/approve",
                     json={}, headers={"x-test-admin": "1"})
    assert (await nav_count())["manual_confirmations"] == 0


@pytest.mark.asyncio
async def test_23_reject_decrements_badge():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        assert (await nav_count())["manual_confirmations"] == 1
        await c.post(f"/api/admin/manual-payment/confirmations/{cid}/reject",
                     json={"reason": ""}, headers={"x-test-admin": "1"})
    assert (await nav_count())["manual_confirmations"] == 0


@pytest.mark.asyncio
async def test_24_duplicate_submit_no_duplicate_notification():
    async with client() as c:
        await submit(c)
        r2 = await submit(c, reference="OTRA")
        assert r2.status_code == 409
    assert await DB.rental_notifications.count_documents({"type": "manual_confirmation_new"}) == 1


@pytest.mark.asyncio
async def test_25_notifications_i18n_es_en():
    async with client() as c:
        cid = (await submit(c)).json()["id"]
        await c.post(f"/api/admin/manual-payment/confirmations/{cid}/reject",
                     json={"reason": "X"}, headers={"x-test-admin": "1"})
    new_n = await DB.rental_notifications.find_one({"type": "manual_confirmation_new"})
    assert new_n["title"] == "Nueva confirmación de pago recibida"
    assert new_n["title_en"] == "New payment confirmation received"
    rej = await DB.rental_notifications.find_one({"type": "manual_confirmation_rejected"})
    assert rej["title"] == "Tu confirmación de pago fue rechazada."
    assert rej["title_en"] == "Your payment confirmation was rejected."
    assert rej["body_en"].startswith("Your payment confirmation was rejected.")
