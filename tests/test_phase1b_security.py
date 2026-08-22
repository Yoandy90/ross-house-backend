"""
PHASE 1B — Security Completion Tests
Run STANDALONE: python -m pytest tests/test_phase1b_security.py -v
Covers: admin 2FA persistent rate limiting (persistence, account/IP isolation),
last_seen throttling, REQUIRE_SESSION_SID cutoff flag, FAQ router auth fix.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["MONGO_URL"] = "mongodb://localhost:27017"
os.environ["DB_NAME"] = "phase1b_test_db"
os.environ["ENVIRONMENT"] = "test"
os.environ["TENANT_JWT_SECRET"] = "phase1b-test-secret"
os.environ.pop("REQUIRE_SESSION_SID", None)

from bson import ObjectId  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental import shared, security  # noqa: E402
from rental import admin_2fa_router  # noqa: E402
from rental import auth_router as ar  # noqa: E402
from rental import faq_router  # noqa: E402

DB = "phase1b_test_db"


@pytest.fixture(scope="module")
def ctx():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=loop)
    db = client[DB]
    loop.run_until_complete(client.drop_database(DB))
    shared.set_db(db)
    app = FastAPI()
    app.include_router(admin_2fa_router.router, prefix="/api")
    app.include_router(faq_router.router, prefix="/api")

    aid = ObjectId()
    loop.run_until_complete(db.app_users.insert_one({
        "_id": aid, "email": "adm1b@test.local", "role": "admin",
        "password_hash": ar.hash_password("Secret123!"), "name": "A"}))
    tok = loop.run_until_complete(
        shared.create_session_token(str(aid), "adm1b@test.local", "admin"))
    yield {"loop": loop, "db": db, "app": app, "adm": str(aid), "tok": tok}
    loop.run_until_complete(client.drop_database(DB))
    client.close()
    loop.close()


def run(ctx, coro):
    return ctx["loop"].run_until_complete(coro)


async def _req(ctx, method, path, token=None, body=None):
    transport = ASGITransport(app=ctx["app"])
    h = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.request(method, path, json=body, headers=h)


# ══════════ P1B-1: 2FA RATE LIMITING ══════════

def test_01_step1_account_limit_5(ctx):
    async def check():
        rs = []
        for _ in range(6):
            rs.append(await _req(ctx, "POST", "/api/admin/auth/login-step1",
                                 body={"email": "adm1b@test.local", "password": "WRONG"}))
        return rs
    rs = run(ctx, check())
    assert all(r.status_code in (400, 401, 403) for r in rs[:5])  # generic fails
    assert rs[5].status_code == 429  # 6th blocked persistently
    assert "@" not in rs[5].json()["detail"]  # anti-enumeration


def test_02_account_isolation(ctx):
    """Other account NOT affected by victim's exhausted limit (same IP has
    10/10min so still available)."""
    r = run(ctx, _req(ctx, "POST", "/api/admin/auth/login-step1",
                      body={"email": "otra@test.local", "password": "x"}))
    assert r.status_code != 429


def test_03_ip_isolation_and_persistence(ctx):
    async def check():
        # persistence: events pre-seeded in Mongo == 'previous process'
        now = datetime.now(timezone.utc)
        await ctx["db"].rate_limit_events.insert_many([
            {"endpoint": "admin-2fa-step2", "key": "ip-A", "created_at": now}
            for _ in range(15)])
        try:
            await security.check_rate_limit_persistent(
                "admin-2fa-step2", "ip-A", max_requests=15, window_seconds=600)
            blocked_a = False
        except HTTPException as e:
            blocked_a = e.status_code == 429
        # different IP hash unaffected
        await security.check_rate_limit_persistent(
            "admin-2fa-step2", "ip-B", max_requests=15, window_seconds=600)
        return blocked_a
    assert run(ctx, check()) is True


def test_04_step2_challenge_limit(ctx):
    async def check():
        rs = []
        for _ in range(11):
            rs.append(await _req(ctx, "POST", "/api/admin/auth/login-step2",
                                 body={"challenge_id": "chal-x", "code": "000000"}))
        return rs
    rs = run(ctx, check())
    assert rs[0].status_code == 400  # unknown challenge (generic)
    assert rs[10].status_code == 429  # brute-force of the 6-digit code blocked


# ══════════ P1B-3: last_seen THROTTLE ══════════

def test_05_last_seen_throttled(ctx):
    async def check():
        tok = await shared.create_session_token(ctx["adm"], "adm1b@test.local", "admin")
        import jwt as pyjwt
        sid = pyjwt.decode(tok, options={"verify_signature": False})["sid"]
        s1 = await ctx["db"].auth_sessions.find_one({"sid": sid})
        # two immediate validations must NOT bump last_seen twice
        payload = pyjwt.decode(tok, os.environ["TENANT_JWT_SECRET"], algorithms=["HS256"])
        await shared._validate_session_claims(payload)
        await shared._validate_session_claims(payload)
        s2 = await ctx["db"].auth_sessions.find_one({"sid": sid})
        # force stale last_seen → next validation bumps it
        await ctx["db"].auth_sessions.update_one(
            {"sid": sid},
            {"$set": {"last_seen_at": datetime.now(timezone.utc) - timedelta(minutes=10)}})
        await shared._validate_session_claims(payload)
        s3 = await ctx["db"].auth_sessions.find_one({"sid": sid})
        return s1, s2, s3
    s1, s2, s3 = run(ctx, check())
    assert s2["last_seen_at"] == s1["last_seen_at"]  # throttled (fresh)
    assert s3["last_seen_at"] > s2["last_seen_at"]   # bumped when stale


# ══════════ P1B-5: SID CUTOFF FLAG ══════════

def test_06_require_sid_flag_rejects_legacy(ctx):
    async def check():
        legacy = shared.create_marketplace_token(ctx["adm"], "adm1b@test.local", "admin")
        import jwt as pyjwt
        payload = pyjwt.decode(legacy, os.environ["TENANT_JWT_SECRET"], algorithms=["HS256"])
        # sin flag → permitido
        await shared._validate_session_claims(payload)
        os.environ["REQUIRE_SESSION_SID"] = "true"
        try:
            try:
                await shared._validate_session_claims(payload)
                return False
            except HTTPException as e:
                return e.status_code == 401 and e.detail == "session_invalid"
        finally:
            os.environ.pop("REQUIRE_SESSION_SID", None)
    assert run(ctx, check()) is True


# ══════════ P1B-4: FAQ ROUTER AUTH FIX ══════════

def test_07_faq_admin_endpoints_now_require_auth(ctx):
    async def check():
        return [
            await _req(ctx, "GET", "/api/admin/faqs"),
            await _req(ctx, "POST", "/api/admin/faqs", body={"question_es": "x"}),
            await _req(ctx, "DELETE", "/api/admin/faqs/abc"),
            await _req(ctx, "POST", "/api/admin/faqs/seed"),
        ]
    rs = run(ctx, check())
    assert all(r.status_code == 401 for r in rs)  # antes: TODOS abiertos


def test_08_faq_public_and_admin_with_token_work(ctx):
    ctx["app"].include_router(faq_router.public_router, prefix="/api")
    r_pub = run(ctx, _req(ctx, "GET", "/api/public/faqs?lang=es"))
    r_adm = run(ctx, _req(ctx, "GET", "/api/admin/faqs", token=ctx["tok"]))
    assert r_pub.status_code == 200
    assert r_adm.status_code == 200
