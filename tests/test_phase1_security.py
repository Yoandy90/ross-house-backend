"""
PHASE 1 — Security Test Suite
==============================
Run STANDALONE:  python -m pytest tests/test_phase1_security.py -v

Covers: server-side sessions + revocation, logout / logout-all, session
management endpoints (IDOR-safe), persistent rate limiting (survives
restarts), OTP hardening (hash, single-use, new-code invalidation), admin
audit log (creation, sanitization, access control, immutability), IDOR,
JWT claims (sid/jti/iat), and the production-DB test guard.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["MONGO_URL"] = "mongodb://localhost:27017"
os.environ["DB_NAME"] = "phase1_test_db"
os.environ["ENVIRONMENT"] = "test"
os.environ["TENANT_JWT_SECRET"] = "phase1-test-secret-do-not-use-in-prod"
os.environ["TWILIO_ACCOUNT_SID"] = "ACtest000000000000000000000000000"
os.environ["TWILIO_AUTH_TOKEN"] = "test-token"
os.environ["TWILIO_PHONE_NUMBER"] = "+15550000000"
os.environ.pop("TURNSTILE_SECRET_KEY", None)

import jwt as pyjwt  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental import shared  # noqa: E402
from rental import auth_router as ar  # noqa: E402
from rental import sessions_router  # noqa: E402
from rental import zelle_router  # noqa: E402
from rental import security  # noqa: E402

DB_NAME = "phase1_test_db"
PASSWORD = "Secret123!"


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
    app.include_router(sessions_router.router, prefix="/api")
    app.include_router(zelle_router.router, prefix="/api")

    async def seed():
        ua = ObjectId()  # tenant A
        ub = ObjectId()  # tenant B
        adm = ObjectId()
        await db.app_users.insert_many([
            {"_id": ua, "email": "a@test.local", "name": "Tenant A",
             "role": "tenant", "password_hash": ar.hash_password(PASSWORD),
             "failed_login_attempts": 0},
            {"_id": ub, "email": "b@test.local", "name": "Tenant B",
             "role": "tenant", "password_hash": ar.hash_password(PASSWORD),
             "failed_login_attempts": 0},
            {"_id": adm, "email": "adm@test.local", "name": "Admin",
             "role": "admin", "password_hash": ar.hash_password(PASSWORD)},
        ])
        return str(ua), str(ub), str(adm)

    ua, ub, adm = loop.run_until_complete(seed())
    tok_a = loop.run_until_complete(shared.create_session_token(ua, "a@test.local", "tenant"))
    tok_b = loop.run_until_complete(shared.create_session_token(ub, "b@test.local", "tenant"))
    tok_adm = loop.run_until_complete(shared.create_session_token(adm, "adm@test.local", "admin"))

    yield {"loop": loop, "db": db, "app": app,
           "ua": ua, "ub": ub, "adm": adm,
           "tok_a": tok_a, "tok_b": tok_b, "tok_adm": tok_adm}

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


def _claims(tok):
    return pyjwt.decode(tok, os.environ["TENANT_JWT_SECRET"], algorithms=["HS256"])


@pytest.fixture(autouse=True)
def no_inmem_rate_limit(monkeypatch):
    monkeypatch.setattr(ar, "check_rate_limit", lambda ip, ep: True)


# ══════════════════ JWT CLAIMS ══════════════════

def test_01_new_tokens_carry_sid_jti_iat(ctx):
    p = _claims(ctx["tok_a"])
    assert len(p["sid"]) == 32 and len(p["jti"]) == 32
    assert p["iat"] and p["exp"] and p["sub"] == ctx["ua"]


def test_02_valid_sid_token_works(ctx):
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token=ctx["tok_a"]))
    assert r.status_code == 200


def test_03_unknown_sid_rejected(ctx):
    p = _claims(ctx["tok_a"])
    p["sid"] = "f" * 32
    forged = pyjwt.encode(p, os.environ["TENANT_JWT_SECRET"], algorithm="HS256")
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token=forged))
    assert r.status_code == 401 and "session" in r.json()["detail"]


def test_04_malformed_sid_rejected(ctx):
    p = _claims(ctx["tok_a"])
    p["sid"] = "short"
    forged = pyjwt.encode(p, os.environ["TENANT_JWT_SECRET"], algorithm="HS256")
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token=forged))
    assert r.status_code == 401


def test_05_sid_of_other_user_rejected(ctx):
    """Token signed for A but pointing at B's session → user mismatch."""
    pb = _claims(ctx["tok_b"])
    pa = _claims(ctx["tok_a"])
    pa["sid"] = pb["sid"]
    forged = pyjwt.encode(pa, os.environ["TENANT_JWT_SECRET"], algorithm="HS256")
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token=forged))
    assert r.status_code == 401


def test_06_expired_session_rejected(ctx):
    async def check():
        tok = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        sid = _claims(tok)["sid"]
        await ctx["db"].auth_sessions.update_one(
            {"sid": sid},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)}})
        return await _req(ctx, "GET", "/api/marketplace/me", token=tok)
    r = run(ctx, check())
    assert r.status_code == 401 and r.json()["detail"] == "session_expired"


def test_07_legacy_token_without_sid_still_valid(ctx):
    """Backward compat: pre-Phase-1 tokens work until natural expiry."""
    legacy = shared.create_marketplace_token(ctx["ua"], "a@test.local", "tenant")
    r = run(ctx, _req(ctx, "GET", "/api/marketplace/me", token=legacy))
    assert r.status_code == 200


# ══════════════════ LOGOUT / REVOCATION ══════════════════

def test_08_logout_invalidates_immediately_and_is_idempotent(ctx):
    async def check():
        tok = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        r_ok = await _req(ctx, "GET", "/api/marketplace/me", token=tok)
        r_lo1 = await _req(ctx, "POST", "/api/auth/logout", token=tok)
        r_after = await _req(ctx, "GET", "/api/marketplace/me", token=tok)
        r_lo2 = await _req(ctx, "POST", "/api/auth/logout", token=tok)  # idempotent
        return r_ok, r_lo1, r_after, r_lo2
    r_ok, r_lo1, r_after, r_lo2 = run(ctx, check())
    assert r_ok.status_code == 200
    assert r_lo1.status_code == 200 and r_lo1.json()["success"]
    assert r_after.status_code == 401 and r_after.json()["detail"] == "session_revoked"
    assert r_lo2.status_code == 200 and r_lo2.json()["success"]


def test_09_logout_all_kills_every_session(ctx):
    async def check():
        t1 = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        t2 = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        t3 = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        r = await _req(ctx, "POST", "/api/auth/logout-all", token=t3, body={})
        return r, [await _req(ctx, "GET", "/api/marketplace/me", token=t)
                   for t in (t1, t2, t3)]
    r, results = run(ctx, check())
    assert r.status_code == 200 and r.json()["revoked"] >= 3
    assert all(x.status_code == 401 for x in results)


def test_10_logout_all_keep_current(ctx):
    async def check():
        t1 = await shared.create_session_token(ctx["ub"], "b@test.local", "tenant")
        t2 = await shared.create_session_token(ctx["ub"], "b@test.local", "tenant")
        r = await _req(ctx, "POST", "/api/auth/logout-all", token=t2,
                       body={"keep_current_session": True})
        return r, await _req(ctx, "GET", "/api/marketplace/me", token=t1), \
            await _req(ctx, "GET", "/api/marketplace/me", token=t2)
    r, r1, r2 = run(ctx, check())
    assert r.status_code == 200
    assert r1.status_code == 401 and r2.status_code == 200


# ══════════════════ SESSION MANAGEMENT + IDOR ══════════════════

def test_11_list_sessions_safe_fields_only(ctx):
    async def check():
        tok = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        return tok, await _req(ctx, "GET", "/api/auth/sessions", token=tok)
    tok, r = run(ctx, check())
    assert r.status_code == 200
    ses = r.json()["sessions"]
    assert any(s["current_session"] for s in ses)
    banned = {"token", "ip_hash", "user_agent", "user_id"}
    assert all(not (banned & set(s.keys())) for s in ses)  # no sensitive fields
    ctx["tok_mgmt"] = tok


def test_12_revoke_own_other_device(ctx):
    async def check():
        t_dev = await shared.create_session_token(ctx["ua"], "a@test.local", "tenant")
        sid_dev = _claims(t_dev)["sid"]
        r = await _req(ctx, "DELETE", f"/api/auth/sessions/{sid_dev}",
                       token=ctx["tok_mgmt"])
        return r, await _req(ctx, "GET", "/api/marketplace/me", token=t_dev)
    r, r_dev = run(ctx, check())
    assert r.status_code == 200
    assert r_dev.status_code == 401  # revoked device dies immediately


def test_13_idor_cannot_revoke_another_users_session(ctx):
    async def check():
        t_b = await shared.create_session_token(ctx["ub"], "b@test.local", "tenant")
        sid_b = _claims(t_b)["sid"]
        r = await _req(ctx, "DELETE", f"/api/auth/sessions/{sid_b}",
                       token=ctx["tok_mgmt"])  # A attacks B's sid
        return r, await _req(ctx, "GET", "/api/marketplace/me", token=t_b)
    r, r_b = run(ctx, check())
    assert r.status_code == 404  # no info leak, no revocation
    assert r_b.status_code == 200  # B unaffected


def test_14_idor_tenant_cannot_access_admin_endpoints(ctx):
    for path in ("/api/admin/audit-logs", "/api/admin/zelle-payments"):
        r = run(ctx, _req(ctx, "GET", path, token=ctx["tok_a"]))
        assert r.status_code in (401, 403), path


def test_15_idor_tenant_cannot_read_other_zelle_submission(ctx):
    async def check():
        sub = await ctx["db"].zelle_submissions.insert_one({
            "tenant_id": ctx["ub"], "status": "pending_review",
            "amount": 999, "reference": "X", "created_at": datetime.utcnow()})
        # detail endpoint is admin-only → tenant A gets 401/403, never B's data
        return await _req(ctx, "GET", f"/api/admin/zelle-payments/{sub.inserted_id}",
                          token=ctx["tok_a"])
    r = run(ctx, check())
    assert r.status_code in (401, 403)


# ══════════════════ PERSISTENT RATE LIMIT ══════════════════

def test_16_rate_limit_survives_restart(ctx):
    """Events live in Mongo — seed 'pre-restart' events, then the very first
    call of the 'new process' must already be blocked."""
    async def check():
        now = datetime.now(timezone.utc)
        await ctx["db"].rate_limit_events.insert_many([
            {"endpoint": "login", "key": "restart-ip", "created_at": now}
            for _ in range(20)])
        try:
            await security.check_rate_limit_persistent("login", "restart-ip",
                                                       max_requests=20,
                                                       window_seconds=300)
            return None
        except Exception as e:
            return e
    e = run(ctx, check())
    assert e is not None and getattr(e, "status_code", None) == 429


def test_17_rate_limit_by_account(ctx):
    async def check():
        for _ in range(10):
            await security.check_rate_limit_persistent(
                "login-acct", "victim@test.local", max_requests=10,
                window_seconds=300)
        try:
            await security.check_rate_limit_persistent(
                "login-acct", "victim@test.local", max_requests=10,
                window_seconds=300)
            return None
        except Exception as e:
            return e
    e = run(ctx, check())
    assert getattr(e, "status_code", None) == 429
    assert "@" not in str(getattr(e, "detail", ""))  # anti-enumeration: generic msg


def test_18_login_endpoint_returns_429(ctx):
    async def check():
        for _ in range(10):
            await security.check_rate_limit_persistent(
                "login-acct", "flood@test.local", max_requests=10,
                window_seconds=300)
        return await _req(ctx, "POST", "/api/public/marketplace-login",
                          body={"email": "flood@test.local", "password": "x"})
    r = run(ctx, check())
    assert r.status_code == 429


# ══════════════════ OTP HARDENING ══════════════════

class _TW:
    last_body = ""

    def __init__(self, *a, **k):
        class Msgs:
            @staticmethod
            def create(**kw):
                _TW.last_body = kw.get("body", "")

                class M:
                    sid = "SM1"
                return M()
        self.messages = Msgs()


def _extract_code():
    import re
    return re.search(r"(\d{6})", _TW.last_body).group(1)


def test_19_otp_hashed_and_new_code_invalidates_old(ctx, monkeypatch):
    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", _TW)

    async def check():
        r1 = await _req(ctx, "POST", "/api/rental/phone/send-otp",
                        body={"phone": "8065551001"})
        code1 = _extract_code()
        r2 = await _req(ctx, "POST", "/api/rental/phone/send-otp",
                        body={"phone": "8065551001"})
        code2 = _extract_code()
        # old code must now be rejected
        r_old = await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                           body={"phone": "8065551001", "code": code1})
        docs = [d async for d in ctx["db"].phone_otps.find({"phone": "+18065551001"})]
        r_new = await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                           body={"phone": "8065551001", "code": code2})
        return r1, r2, r_old, docs, r_new, code1, code2
    r1, r2, r_old, docs, r_new, code1, code2 = run(ctx, check())
    assert r1.status_code == 200 and r2.status_code == 200
    assert all("code" not in d for d in docs)          # plaintext never stored
    assert all(d.get("code_hash") for d in docs)
    if code1 != code2:
        assert r_old.status_code == 400                # invalidated by new code
    assert r_new.status_code == 200                    # newest code works


def test_20_otp_single_use(ctx, monkeypatch):
    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", _TW)

    async def check():
        await _req(ctx, "POST", "/api/rental/phone/send-otp",
                   body={"phone": "8065551002"})
        code = _extract_code()
        r1 = await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                        body={"phone": "8065551002", "code": code})
        r2 = await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                        body={"phone": "8065551002", "code": code})
        return r1, r2
    r1, r2 = run(ctx, check())
    assert r1.status_code == 200
    assert r2.status_code == 400  # already consumed


def test_21_otp_attempts_exhausted(ctx, monkeypatch):
    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", _TW)

    async def check():
        await _req(ctx, "POST", "/api/rental/phone/send-otp",
                   body={"phone": "8065551003"})
        code = _extract_code()
        for _ in range(5):
            await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                       body={"phone": "8065551003", "code": "000000"})
        # correct code AFTER 5 wrong attempts must be rejected (attempts<5 filter)
        return await _req(ctx, "POST", "/api/rental/phone/verify-otp",
                          body={"phone": "8065551003", "code": code})
    r = run(ctx, check())
    assert r.status_code == 400


# ══════════════════ AUDIT LOG ══════════════════

def test_22_sensitive_action_creates_sanitized_audit(ctx):
    async def check():
        await security.audit_log(
            admin_user_id=ctx["adm"], action="processor_config_updated",
            resource_type="payment_config", resource_id="helcim",
            metadata={"api_token": "SUPER-SECRET", "environment": "production"})
        return await ctx["db"].admin_audit_logs.find_one(
            {"action": "processor_config_updated"})
    doc = run(ctx, check())
    assert doc and doc["admin_user_id"] == ctx["adm"]
    assert doc["metadata"]["api_token"] == "[REDACTED]"       # secret scrubbed
    assert doc["metadata"]["environment"] == "production"


def test_23_zelle_confirm_generates_audit(ctx):
    async def check():
        sub = await ctx["db"].zelle_submissions.insert_one({
            "tenant_id": ctx["ua"], "tenant_name": "Tenant A",
            "contract_id": "c1", "amount": 100.0, "late_fee": 0,
            "reference": "REF1", "period_month": "january", "period_year": 2030,
            "status": "pending_review", "created_at": datetime.utcnow()})
        r = await _req(ctx, "POST",
                       f"/api/admin/zelle-payments/{sub.inserted_id}/confirm",
                       token=ctx["tok_adm"])
        log = await ctx["db"].admin_audit_logs.find_one(
            {"action": "zelle_payment_confirmed"})
        return r, log
    r, log = run(ctx, check())
    assert r.status_code == 200
    assert log and log["admin_user_id"] == ctx["adm"]
    assert "screenshot" not in str(log.get("metadata", {}))


def test_24_audit_api_admin_only_with_filters(ctx):
    r_tenant = run(ctx, _req(ctx, "GET", "/api/admin/audit-logs", token=ctx["tok_a"]))
    r_admin = run(ctx, _req(ctx, "GET",
                            "/api/admin/audit-logs?action=zelle_payment_confirmed",
                            token=ctx["tok_adm"]))
    assert r_tenant.status_code in (401, 403)
    assert r_admin.status_code == 200
    items = r_admin.json()["items"]
    assert items and all(i["action"] == "zelle_payment_confirmed" for i in items)


def test_25_audit_log_has_no_mutation_endpoints(ctx):
    for method in ("DELETE", "PUT", "PATCH"):
        r = run(ctx, _req(ctx, method, "/api/admin/audit-logs", token=ctx["tok_adm"]))
        assert r.status_code == 405  # method not allowed — append-only


# ══════════════════ TEST DB GUARD ══════════════════

def test_26_production_db_guard_blocks_atlas():
    sys.path.insert(0, os.path.dirname(__file__))
    from conftest import assert_not_production_database
    with pytest.raises(RuntimeError):
        assert_not_production_database("mongodb+srv://u:p@cluster.mongodb.net/x", "db")
    with pytest.raises(RuntimeError):
        assert_not_production_database("mongodb://localhost:27017", "rossapp")
    assert_not_production_database("mongodb://localhost:27017", "safe_test_db")  # ok
