"""
PHASE 0 — Auth Baseline Tests
==============================
Run STANDALONE (fresh process):  python -m pytest tests/test_phase0_auth.py -v

SAFETY:
  * Forces MONGO_URL=localhost + DB_NAME=phase0_auth_test_db BEFORE imports
    → NEVER touches production. No brute-force against any live server.
  * Twilio is fully mocked → no SMS sent, no Twilio spend.

Covers:
  - valid login / invalid password / unknown account (anti-enumeration:
    identical error for both failure modes)
  - admin cannot login via marketplace endpoint (must use 2FA flow)
  - account lockout after MAX_LOGIN_FAILURES
  - in-memory rate limiter unit behaviour (HTTP 429)
  - phone OTP: request (Twilio mocked), wrong code, expired code, success
  - invalid JWT / expired JWT / no token → 401
  - tenant token cannot access admin endpoints
  - admin JWT works on admin endpoints
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── SAFETY: isolated local test DB + deterministic secret (BEFORE imports) ──
os.environ["MONGO_URL"] = "mongodb://localhost:27017"
os.environ["DB_NAME"] = "phase0_auth_test_db"
os.environ["ENVIRONMENT"] = "test"
os.environ["TENANT_JWT_SECRET"] = "phase0-test-secret-do-not-use-in-prod"
os.environ["TWILIO_ACCOUNT_SID"] = "ACtest000000000000000000000000000"
os.environ["TWILIO_AUTH_TOKEN"] = "test-token"
os.environ["TWILIO_PHONE_NUMBER"] = "+15550000000"
os.environ.pop("TURNSTILE_SECRET_KEY", None)  # captcha optional in tests

import jwt as pyjwt  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental import shared  # noqa: E402
from rental import auth_router as ar  # noqa: E402
from rental import zelle_router  # noqa: E402

DB_NAME = "phase0_auth_test_db"
PASSWORD = "Secret123!"
GENERIC = None  # captured from module
_REAL_RATE_LIMIT = ar.check_rate_limit  # snapshot before any monkeypatch


@pytest.fixture(scope="module")
def ctx():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=loop)
    db = client[DB_NAME]
    loop.run_until_complete(client.drop_database(DB_NAME))
    shared.set_db(db)

    app = FastAPI()
    app.include_router(ar.router, prefix="/api")
    app.include_router(zelle_router.router, prefix="/api")  # admin endpoint target

    async def seed():
        uid = ObjectId()
        await db.app_users.insert_one({
            "_id": uid, "email": "p0auth@test.local", "name": "P0 Auth",
            "role": "tenant", "password_hash": ar.hash_password(PASSWORD),
            "failed_login_attempts": 0, "created_at": datetime.utcnow()})
        aid = ObjectId()
        await db.app_users.insert_one({
            "_id": aid, "email": "p0admin@test.local", "name": "P0 Admin",
            "role": "admin", "password_hash": ar.hash_password(PASSWORD)})
        return str(uid), str(aid)

    uid, aid = loop.run_until_complete(seed())

    yield {"loop": loop, "db": db, "app": app, "uid": uid, "aid": aid}

    loop.run_until_complete(client.drop_database(DB_NAME))
    client.close()
    loop.close()


def run(ctx, coro):
    return ctx["loop"].run_until_complete(coro)


async def _req(ctx, method, path, token=None, body=None):
    transport = ASGITransport(app=ctx["app"])
    h = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.request(method, path, json=body, headers=h)


@pytest.fixture(autouse=True)
def no_ip_rate_limit(monkeypatch):
    """Login/OTP tests share one fake client IP — bypass the per-IP limiter so
    each scenario is exercised. The limiter itself is unit-tested separately."""
    monkeypatch.setattr(ar, "check_rate_limit", lambda ip, ep: True)


# ══════════════════════ LOGIN ══════════════════════

def test_01_valid_login(ctx):
    r = run(ctx, _req(ctx, "POST", "/api/public/marketplace-login",
                      body={"email": "p0auth@test.local", "password": PASSWORD}))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["success"] and data["user"]["role"] == "tenant"
    payload = pyjwt.decode(data["token"], os.environ["TENANT_JWT_SECRET"],
                           algorithms=["HS256"])
    assert payload["type"] == "marketplace" and payload["user_id"] == ctx["uid"]
    assert "exp" in payload  # token expires


def test_02_invalid_password_401(ctx):
    global GENERIC
    r = run(ctx, _req(ctx, "POST", "/api/public/marketplace-login",
                      body={"email": "p0auth@test.local", "password": "WRONG"}))
    assert r.status_code == 401
    GENERIC = r.json()["detail"]
    # reset failure counter so later tests aren't affected
    run(ctx, ctx["db"].app_users.update_one(
        {"email": "p0auth@test.local"},
        {"$set": {"failed_login_attempts": 0, "locked_until": None}}))


def test_03_unknown_account_anti_enumeration(ctx):
    r = run(ctx, _req(ctx, "POST", "/api/public/marketplace-login",
                      body={"email": "ghost-does-not-exist@test.local",
                            "password": "whatever"}))
    assert r.status_code == 401
    assert r.json()["detail"] == GENERIC  # IDENTICAL error → no user enumeration


def test_04_admin_blocked_on_marketplace_login(ctx):
    """Admins MUST use the 2FA flow — marketplace login must refuse them."""
    r = run(ctx, _req(ctx, "POST", "/api/public/marketplace-login",
                      body={"email": "p0admin@test.local", "password": PASSWORD}))
    assert r.status_code == 403
    assert r.json()["detail"] == "admin_2fa_required"


def test_05_lockout_after_max_failures(ctx):
    async def check():
        for _ in range(ar.MAX_LOGIN_FAILURES):
            await _req(ctx, "POST", "/api/public/marketplace-login",
                       body={"email": "p0auth@test.local", "password": "WRONG"})
        # correct password AFTER lockout must still be rejected
        r = await _req(ctx, "POST", "/api/public/marketplace-login",
                       body={"email": "p0auth@test.local", "password": PASSWORD})
        user = await ctx["db"].app_users.find_one({"email": "p0auth@test.local"})
        # cleanup: unlock for remaining tests
        await ctx["db"].app_users.update_one(
            {"_id": user["_id"]},
            {"$set": {"failed_login_attempts": 0, "locked_until": None}})
        return r, user
    r, user = run(ctx, check())
    assert r.status_code == 401
    assert user.get("locked_until") is not None


def test_06_rate_limiter_unit_429():
    real = _REAL_RATE_LIMIT  # captured before the autouse fixture patched it
    ar._rate_limit_store.clear()
    for _ in range(ar.RATE_LIMIT_MAX_REQUESTS):
        assert real("9.9.9.9", "login") is True
    with pytest.raises(HTTPException) as e:
        real("9.9.9.9", "login")
    assert e.value.status_code == 429


# ══════════════════════ PHONE OTP ══════════════════════

class _FakeTwilioMessages:
    last_body = ""

    def create(self, **kw):
        _FakeTwilioMessages.last_body = kw.get("body", "")

        class M:
            sid = "SMtest"
        return M()


class _FakeTwilioClient:
    def __init__(self, *a, **k):
        self.messages = _FakeTwilioMessages()


def test_07_otp_request_twilio_mocked(ctx, monkeypatch):
    import re
    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", _FakeTwilioClient)
    r = run(ctx, _req(ctx, "POST", "/api/rental/phone/send-otp",
                      body={"phone": "8065550101"}))
    assert r.status_code == 200, r.text
    doc = run(ctx, ctx["db"].phone_otps.find_one({"phone": "+18065550101"}))
    assert doc and doc["attempts"] == 0 and doc["verified"] is False
    assert "code" not in doc and doc.get("code_hash")  # OTP never in plaintext
    m = re.search(r"(\d{6})", _FakeTwilioMessages.last_body)
    assert m, "OTP code not found in mocked SMS body"
    ctx["otp_code"] = m.group(1)


def test_08_otp_wrong_code(ctx):
    r = run(ctx, _req(ctx, "POST", "/api/rental/phone/verify-otp",
                      body={"phone": "8065550101", "code": "000000"}))
    assert r.status_code == 400
    doc = run(ctx, ctx["db"].phone_otps.find_one({"phone": "+18065550101"}))
    assert doc["attempts"] >= 1  # failed attempt counted


def test_09_otp_expired(ctx):
    async def check():
        from rental.security import hash_otp
        await ctx["db"].phone_otps.insert_one({
            "phone": "+18065550202", "code_hash": hash_otp("123456"),
            "verified": False, "invalidated": False,
            "source": "rental", "attempts": 0,
            "expires_at": datetime.utcnow() - timedelta(minutes=1),
            "created_at": datetime.utcnow() - timedelta(minutes=6)})
        return await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                          body={"phone": "8065550202", "code": "123456"})
    r = run(ctx, check())
    assert r.status_code == 400


def test_10_otp_correct_code_logs_in(ctx):
    r = run(ctx, _req(ctx, "POST", "/api/rental/phone/verify-otp",
                      body={"phone": "8065550101", "code": ctx["otp_code"],
                            "name": "OTP User"}))
    assert r.status_code == 200, r.text
    assert r.json().get("token")


# ══════════════════════ JWT / AUTHZ ══════════════════════

def test_11_no_token_401(ctx):
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me"))
    assert r.status_code == 401


def test_12_garbage_token_401(ctx):
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token="not-a-jwt"))
    assert r.status_code == 401


def test_13_expired_jwt_401(ctx):
    expired = pyjwt.encode(
        {"user_id": ctx["uid"], "email": "p0auth@test.local", "role": "tenant",
         "type": "marketplace", "exp": datetime.utcnow() - timedelta(hours=1)},
        os.environ["TENANT_JWT_SECRET"], algorithm="HS256")
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token=expired))
    assert r.status_code == 401


def test_14_wrong_signature_jwt_401(ctx):
    forged = pyjwt.encode(
        {"user_id": ctx["uid"], "email": "p0auth@test.local", "role": "admin",
         "type": "marketplace", "exp": datetime.utcnow() + timedelta(days=1)},
        "attacker-secret", algorithm="HS256")
    r = run(ctx, _req(ctx, "GET", "/api/admin/zelle-payments", token=forged))
    assert r.status_code == 401


def test_15_tenant_cannot_access_admin(ctx):
    tenant_tok = shared.create_marketplace_token(
        ctx["uid"], "p0auth@test.local", "tenant")
    r = run(ctx, _req(ctx, "GET", "/api/admin/zelle-payments", token=tenant_tok))
    assert r.status_code in (401, 403)


def test_16_role_escalation_in_token_rejected(ctx):
    """A tenant-signed token claiming role=admin must fail because the DB user
    role is checked server-side."""
    fake_admin = shared.create_marketplace_token(
        ctx["uid"], "p0auth@test.local", "admin")  # uid is a TENANT in DB
    r = run(ctx, _req(ctx, "GET", "/api/admin/zelle-payments", token=fake_admin))
    assert r.status_code in (401, 403)


def test_17_real_admin_jwt_works(ctx):
    admin_tok = shared.create_marketplace_token(
        ctx["aid"], "p0admin@test.local", "admin")
    r = run(ctx, _req(ctx, "GET", "/api/admin/zelle-payments", token=admin_tok))
    assert r.status_code == 200
