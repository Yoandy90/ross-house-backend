"""Tests Drip Email + Blog: config, plantillas CRUD, blog público, drip_tick (sin enviar)."""
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
from rental.drip_router import router as drip_router, _slugify, _bilingual_message  # noqa: E402


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
    app.include_router(drip_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        token = create_marketplace_token(str(admin["_id"]), admin["email"], "admin")
        # plantilla de prueba
        from datetime import datetime
        res = await db.email_templates.insert_one({
            "category": "rentar", "subject_es": "PYTEST asunto es", "subject_en": "PYTEST subject en",
            "body_es": "cuerpo es", "body_en": "body en", "status": "active",
            "sent_at": None, "sent_count": 0, "published_to_blog": False,
            "slug": "pytest-asunto-es", "ai_generated": True, "created_at": datetime.utcnow(),
        })
        return token, str(res.inserted_id)

    token, tpl_id = loop.run_until_complete(_setup())
    state = {"app": app, "db": db, "token": token, "tpl_id": tpl_id, "loop": loop}
    yield state

    async def _teardown():
        await db.email_templates.delete_many({"subject_es": {"$regex": "^PYTEST"}})
    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.request(method, path, json=json, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def test_01_slugify_and_bilingual():
    assert _slugify("¿Qué necesitas para rentar?") == "que-necesitas-para-rentar"
    msg = _bilingual_message({"body_es": "hola", "subject_en": "Hi", "body_en": "hello"})
    assert "hola" in msg and "hello" in msg and "English" in msg


def test_02_config(ctx):
    r = _req(ctx, "GET", "/api/admin/drip/config")
    assert r.status_code == 200
    d = r.json()
    assert d["config"]["per_week"] in (1, 2, 3)
    assert "pending" in d["queue"]

    r = _req(ctx, "PATCH", "/api/admin/drip/config", json={"per_week": 5})
    assert r.status_code == 400
    r = _req(ctx, "PATCH", "/api/admin/drip/config", json={"per_week": 2})
    assert r.status_code == 200


def test_03_templates_crud(ctx):
    r = _req(ctx, "GET", "/api/admin/drip/templates?q=PYTEST")
    assert r.status_code == 200
    assert r.json()["total"] >= 1

    tid = ctx["tpl_id"]
    r = _req(ctx, "PATCH", f"/api/admin/drip/templates/{tid}",
             json={"subject_es": "PYTEST editado", "status": "draft"})
    assert r.status_code == 200
    assert r.json()["template"]["subject_es"] == "PYTEST editado"
    assert r.json()["template"]["status"] == "draft"

    r = _req(ctx, "PATCH", f"/api/admin/drip/templates/{tid}", json={"status": "malo"})
    assert r.status_code == 400


def test_04_blog_publish_and_public(ctx):
    tid = ctx["tpl_id"]
    r = _req(ctx, "PATCH", f"/api/admin/drip/templates/{tid}", json={"published_to_blog": True})
    assert r.status_code == 200

    r = _req(ctx, "GET", "/api/public/blog/posts", auth=False)
    assert r.status_code == 200
    slugs = [p["slug"] for p in r.json()["posts"]]
    assert "pytest-asunto-es" in slugs

    r = _req(ctx, "GET", "/api/public/blog/posts/pytest-asunto-es", auth=False)
    assert r.status_code == 200
    assert r.json()["post"]["body_es"]

    # quitar del blog
    r = _req(ctx, "PATCH", f"/api/admin/drip/templates/{tid}", json={"published_to_blog": False})
    r = _req(ctx, "GET", "/api/public/blog/posts/pytest-asunto-es", auth=False)
    assert r.status_code == 404


def test_05_drip_tick_no_send(ctx):
    """drip_tick no envía si está deshabilitado / fuera de horario."""
    from rental import drip_cron

    async def _run():
        db = ctx["db"]
        prev = await db.app_settings.find_one({"_id": "drip"})
        await db.app_settings.update_one({"_id": "drip"}, {"$set": {"enabled": False}}, upsert=True)
        sent = await drip_cron.drip_tick(db)
        # restaurar
        if prev:
            await db.app_settings.replace_one({"_id": "drip"}, prev, upsert=True)
        else:
            await db.app_settings.update_one({"_id": "drip"}, {"$set": {"enabled": True}}, upsert=True)
        return sent
    assert ctx["loop"].run_until_complete(_run()) is False


def test_06_auth_required(ctx):
    r = _req(ctx, "GET", "/api/admin/drip/templates", auth=False)
    assert r.status_code == 401
