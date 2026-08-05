"""Tests for admin nav-summary + global-search endpoints."""
import asyncio
import os
import sys

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
from rental.admin_nav_router import router as nav_router  # noqa: E402


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
    app.include_router(nav_router, prefix="/api")

    async def _tok():
        admin = await db.app_users.find_one({"role": "admin"})
        return create_marketplace_token(str(admin["_id"]), admin["email"], "admin")

    token = loop.run_until_complete(_tok())
    yield {"app": app, "token": token, "loop": loop}
    client.close()


def _call(ctx, path, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.get(path, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def test_nav_summary(ctx):
    r = _call(ctx, "/api/admin/nav-summary")
    assert r.status_code == 200, r.text
    d = r.json()
    for key in ("total", "new_applications", "open_maintenance",
                "pending_signatures", "late_payments", "delinquent_taxes"):
        assert key in d
    assert d["total"] == (d["new_applications"] + d["open_maintenance"] +
                          d["pending_signatures"] + d["late_payments"] +
                          d["delinquent_taxes"]["count"])
    # 121 Oak is delinquent right now
    assert d["delinquent_taxes"]["count"] >= 1
    assert d["delinquent_taxes"]["total_due"] > 4000


def test_global_search_properties(ctx):
    r = _call(ctx, "/api/admin/global-search?q=oak")
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert any(x["type"] == "property" and "oak" in x["title"].lower() for x in results)


def test_global_search_tenant_and_contract(ctx):
    r = _call(ctx, "/api/admin/global-search?q=yandisleydis")
    assert r.status_code == 200
    types = {x["type"] for x in r.json()["results"]}
    assert "contract" in types or "tenant" in types or "application" in types

    r = _call(ctx, "/api/admin/global-search?q=CONT-2026")
    assert any(x["type"] == "contract" for x in r.json()["results"])


def test_global_search_short_query(ctx):
    r = _call(ctx, "/api/admin/global-search?q=a")
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_unauthorized(ctx):
    assert _call(ctx, "/api/admin/nav-summary", auth=False).status_code == 401
    assert _call(ctx, "/api/admin/global-search?q=oak", auth=False).status_code == 401
