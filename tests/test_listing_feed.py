"""Tests Fase 5: feed XML público y generación de anuncio AI (mock LLM)."""
import asyncio
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
from rental.listing_feed_router import router as feed_router  # noqa: E402

TEST_TAG = "__feed_pytest__"


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
    app.include_router(feed_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        token = create_marketplace_token(str(admin["_id"]), admin["email"], "admin")
        # propiedad disponible single
        p1 = await db.properties.insert_one({
            "name": f"Casa {TEST_TAG}", "address": "700 Feed St", "city": "Dumas",
            "state": "TX", "zip_code": "79029", "type": "house", "bedrooms": 3,
            "bathrooms": 2.0, "square_feet": 1400, "rent_amount": 1100.0,
            "deposit_amount": 1100.0, "status": "available",
            "features": ["Patio grande", "Garaje"], "description": "Casa amplia & bonita <test>",
        })
        # propiedad multi-unidad con 1 libre y 1 rentada
        p2 = await db.properties.insert_one({
            "name": f"Edificio {TEST_TAG}", "address": "701 Feed St", "city": "Dumas",
            "state": "TX", "type": "apartment", "status": "available",
            "is_multi_unit": True, "features": [],
        })
        now = datetime.utcnow()
        await db.property_units.insert_many([
            {"property_id": str(p2.inserted_id), "unit_name": "Apt A", "bedrooms": 1,
             "bathrooms": 1.0, "square_feet": 600, "rent_amount": 650.0,
             "deposit_amount": 650.0, "status": "available", "created_at": now},
            {"property_id": str(p2.inserted_id), "unit_name": "Apt B", "bedrooms": 1,
             "bathrooms": 1.0, "square_feet": 600, "rent_amount": 650.0,
             "deposit_amount": 650.0, "status": "rented", "created_at": now},
        ])
        return token, str(p1.inserted_id), str(p2.inserted_id)

    token, p1, p2 = loop.run_until_complete(_setup())
    yield {"app": app, "db": db, "token": token, "loop": loop, "p1": p1, "p2": p2}

    async def _teardown():
        await db.property_units.delete_many({"property_id": p2})
        await db.properties.delete_many({"name": {"$regex": TEST_TAG}})

    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.request(method, path, json=json, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def test_01_feed_xml(ctx):
    r = _req(ctx, "GET", "/api/public/listings-feed.xml", auth=False)
    assert r.status_code == 200
    assert "application/xml" in r.headers["content-type"]
    xml = r.text
    assert xml.startswith('<?xml version="1.0"')
    assert "700 Feed St" in xml            # single disponible
    assert "Apt A" in xml                  # unidad libre
    assert "Apt B" not in xml              # unidad rentada NO
    assert "&lt;test&gt;" in xml           # escaping correcto
    assert "<Price>650</Price>" in xml


def test_02_feed_excludes_rented_single(ctx):
    async def _set(status):
        await ctx["db"].properties.update_one(
            {"_id": ObjectId(ctx["p1"])}, {"$set": {"status": status}})
    ctx["loop"].run_until_complete(_set("rented"))
    xml = _req(ctx, "GET", "/api/public/listings-feed.xml", auth=False).text
    assert "700 Feed St" not in xml
    ctx["loop"].run_until_complete(_set("available"))


def test_03_publish_info(ctx):
    r = _req(ctx, "GET", "/api/admin/listings/publish-info")
    assert r.status_code == 200
    d = r.json()
    assert d["feed_url"].endswith("/api/public/listings-feed.xml")
    names = [l["name"] for l in d["listings"]]
    assert any("Casa" in n and TEST_TAG in n for n in names)
    assert any("Apt A" in n for n in names)


def test_04_ad_copy_generation_mocked(ctx):
    import rental.listing_feed_router as lfr

    class FakeChat:
        def __init__(self, *a, **k):
            pass

        def with_model(self, *a, **k):
            return self

        async def send_message(self, *a, **k):
            return ('{"es": {"title": "Casa 3/2 en Dumas", "description": "desc es", '
                    '"bullets": ["Patio"], "social": "post es"}, '
                    '"en": {"title": "3/2 House in Dumas", "description": "desc en", '
                    '"bullets": ["Yard"], "social": "post en"}}')

    import emergentintegrations.llm.chat as ll
    orig = ll.LlmChat
    ll.LlmChat = FakeChat
    try:
        r = _req(ctx, "POST", f"/api/admin/listings/{ctx['p1']}/ad-copy", json={})
    finally:
        ll.LlmChat = orig
    assert r.status_code == 200, r.text
    ad = r.json()["ad_copy"]
    assert ad["es"]["title"] == "Casa 3/2 en Dumas"

    # cache visible en publish-info
    d = _req(ctx, "GET", "/api/admin/listings/publish-info").json()
    mine = [l for l in d["listings"] if l["property_id"] == ctx["p1"]][0]
    assert mine["ad_copy"]["en"]["title"] == "3/2 House in Dumas"


def test_05_unauthorized(ctx):
    assert _req(ctx, "GET", "/api/admin/listings/publish-info",
                auth=False).status_code == 401
    assert _req(ctx, "POST", f"/api/admin/listings/{ctx['p1']}/ad-copy",
                json={}, auth=False).status_code == 401
