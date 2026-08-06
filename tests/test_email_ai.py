"""Tests del buzón con AI: spam, auto-ack, borradores, aprobar y config."""
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

from bson import ObjectId  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental.shared import set_db, create_marketplace_token  # noqa: E402
from rental import email_inbox_router as eir  # noqa: E402

TEST_TAG = "__buzon_pytest__"


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def ctx(loop, monkeypatch_module=None):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=loop)
    db = client[os.environ.get("DB_NAME", "taxportal")]
    set_db(db)
    app = FastAPI()
    app.include_router(eir.router, prefix="/api")

    async def _tok():
        admin = await db.app_users.find_one({"role": "admin"})
        return create_marketplace_token(str(admin["_id"]), admin["email"], "admin")

    token = loop.run_until_complete(_tok())

    # No enviar emails reales ni llamar al LLM en tests
    sent = []

    async def fake_send(to, subject, body_text, body_html="", from_email=""):
        sent.append({"to": to, "subject": subject, "body": body_text,
                     "from": from_email})
        return True

    async def fake_draft(doc):
        return f"Borrador de prueba para {doc.get('from_email')}"

    async def fake_classify(doc):
        text = (doc.get("text") or "").lower()
        if "factura" in text or "invoice" in text:
            return "invoice"
        return "lead"

    eir._send_via_sendgrid = fake_send
    eir._generate_ai_draft = fake_draft
    eir._classify_email = fake_classify

    # Estado conocido de config para los tests (BD compartida con prod)
    async def _cfg_reset():
        await db.app_settings.update_one(
            {"_id": "email_ai"},
            {"$set": {"auto_ack_enabled": True, "auto_draft_enabled": True,
                      "auto_send_enabled": False}},
            upsert=True)
        await db.email_acks.delete_many({"email": {"$regex": "buzon-pytest"}})
    loop.run_until_complete(_cfg_reset())

    yield {"app": app, "db": db, "token": token, "loop": loop, "sent": sent}

    async def _teardown():
        await db.email_inbox.delete_many({"subject": {"$regex": TEST_TAG}})
        await db.email_acks.delete_many({"email": {"$regex": "buzon-pytest"}})

    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, form=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.request(method, path, json=json, data=form, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def test_01_inbound_normal_triggers_ack_and_draft(ctx):
    r = _req(ctx, "POST", "/api/webhooks/email-inbound", form={
        "from": "Juan Prueba <buzon-pytest-1@example.com>",
        "to": "admin@inbox.rosshouserentals.com",
        "subject": f"Consulta de renta {TEST_TAG}",
        "text": "Hola, ¿tienen casas disponibles de 3 cuartos?",
        "spam_score": "0.5",
    })
    assert r.status_code == 200

    async def _check():
        doc = await ctx["db"].email_inbox.find_one({"subject": {"$regex": TEST_TAG},
                                                    "from_email": "buzon-pytest-1@example.com"})
        return doc
    doc = ctx["loop"].run_until_complete(_check())
    assert doc is not None
    assert doc["folder"] == "inbox"
    # background task ya corrió (ASGI espera los background tasks)
    assert doc.get("ack_sent") is True
    assert doc.get("ai_draft", "").startswith("Borrador de prueba")
    assert doc.get("ai_status") == "draft"
    assert any(s["to"] == "buzon-pytest-1@example.com" and "Recibimos" in s["subject"]
               for s in ctx["sent"])


def test_02_spam_goes_to_spam_folder_no_ack(ctx):
    before = len(ctx["sent"])
    r = _req(ctx, "POST", "/api/webhooks/email-inbound", form={
        "from": "spammer <buzon-pytest-spam@example.com>",
        "to": "admin@inbox.rosshouserentals.com",
        "subject": f"BUY NOW {TEST_TAG}",
        "text": "viagra casino crypto",
        "spam_score": "9.1",
    })
    assert r.status_code == 200

    async def _check():
        return await ctx["db"].email_inbox.find_one({"from_email": "buzon-pytest-spam@example.com"})
    doc = ctx["loop"].run_until_complete(_check())
    assert doc["folder"] == "spam"
    assert not doc.get("ack_sent")
    assert len(ctx["sent"]) == before  # no ack, no draft-send


def test_03_no_ack_for_automated_senders(ctx):
    before = len(ctx["sent"])
    _req(ctx, "POST", "/api/webhooks/email-inbound", form={
        "from": "No Reply <no-reply-buzon-pytest@example.com>",
        "to": "admin@inbox.rosshouserentals.com",
        "subject": f"Notificación automática {TEST_TAG}",
        "text": "mensaje automático",
        "spam_score": "0",
    })
    assert len(ctx["sent"]) == before


def test_04_no_double_ack_within_24h(ctx):
    before = len([s for s in ctx["sent"] if s["to"] == "buzon-pytest-1@example.com"])
    _req(ctx, "POST", "/api/webhooks/email-inbound", form={
        "from": "Juan Prueba <buzon-pytest-1@example.com>",
        "to": "admin@inbox.rosshouserentals.com",
        "subject": f"Segunda consulta {TEST_TAG}",
        "text": "otra pregunta",
        "spam_score": "0",
    })
    acks = [s for s in ctx["sent"] if s["to"] == "buzon-pytest-1@example.com"
            and "Recibimos" in s["subject"]]
    assert len(acks) == 1  # solo el primero recibió ack
    assert before >= 1


def test_05_ai_config_get_and_update(ctx):
    r = _req(ctx, "GET", "/api/admin/inbox/ai-config")
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert "auto_ack_enabled" in cfg and "auto_send_enabled" in cfg

    r = _req(ctx, "PUT", "/api/admin/inbox/ai-config", json={"auto_send_enabled": True})
    assert r.status_code == 200
    assert r.json()["config"]["auto_send_enabled"] is True
    # restaurar
    r = _req(ctx, "PUT", "/api/admin/inbox/ai-config", json={"auto_send_enabled": False})
    assert r.json()["config"]["auto_send_enabled"] is False


def test_06_auto_send_mode(ctx):
    _req(ctx, "PUT", "/api/admin/inbox/ai-config", json={"auto_send_enabled": True})
    _req(ctx, "POST", "/api/webhooks/email-inbound", form={
        "from": "Ana Auto <buzon-pytest-auto@example.com>",
        "to": "admin@inbox.rosshouserentals.com",
        "subject": f"Pregunta con auto-send {TEST_TAG}",
        "text": "¿Cuánto es la renta?",
        "spam_score": "0",
    })
    _req(ctx, "PUT", "/api/admin/inbox/ai-config", json={"auto_send_enabled": False})

    async def _check():
        return await ctx["db"].email_inbox.find_one({"from_email": "buzon-pytest-auto@example.com",
                                                     "folder": "inbox"})
    doc = ctx["loop"].run_until_complete(_check())
    assert doc.get("ai_status") == "sent_auto"
    assert any(s["to"] == "buzon-pytest-auto@example.com" and s["body"].startswith("Borrador de prueba")
               for s in ctx["sent"])


def test_07_regenerate_and_approve_draft(ctx):
    async def _get_id():
        d = await ctx["db"].email_inbox.find_one({"from_email": "buzon-pytest-1@example.com"})
        return str(d["_id"])
    eid = ctx["loop"].run_until_complete(_get_id())

    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/ai-draft")
    assert r.status_code == 200, r.text
    assert r.json()["ai_draft"].startswith("Borrador de prueba")

    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/approve-draft",
             json={"body": "Respuesta editada por el admin"})
    assert r.status_code == 200, r.text

    async def _check():
        d = await ctx["db"].email_inbox.find_one({"_id": ObjectId(eid)})
        s = await ctx["db"].email_inbox.find_one({"folder": "sent", "in_reply_to": eid})
        return d, s
    d, s = ctx["loop"].run_until_complete(_check())
    assert d["ai_status"] == "approved"
    assert s is not None and s.get("ai_approved") is True


def test_08_move_between_folders(ctx):
    async def _get_id():
        d = await ctx["db"].email_inbox.find_one({"from_email": "buzon-pytest-spam@example.com"})
        return str(d["_id"])
    eid = ctx["loop"].run_until_complete(_get_id())
    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/move", json={"folder": "inbox"})
    assert r.status_code == 200
    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/move", json={"folder": "spam"})
    assert r.status_code == 200
    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/move", json={"folder": "archive"})
    assert r.status_code == 400


def test_09_unauthorized(ctx):
    assert _req(ctx, "GET", "/api/admin/inbox/ai-config", auth=False).status_code == 401


def test_10_inbound_gets_ai_category(ctx):
    async def _check():
        return await ctx["db"].email_inbox.find_one(
            {"from_email": "buzon-pytest-1@example.com"})
    doc = ctx["loop"].run_until_complete(_check())
    assert doc.get("category") == "lead"


def test_11_list_filter_by_category_and_counts(ctx):
    _req(ctx, "POST", "/api/webhooks/email-inbound", form={
        "from": "Xcel Energy <billing-buzon-pytest@example.com>",
        "to": "admin@inbox.rosshouserentals.com",
        "subject": f"Su factura de agosto {TEST_TAG}",
        "text": "Adjuntamos su factura del mes",
        "spam_score": "0",
    })
    r = _req(ctx, "GET", "/api/admin/inbox?folder=inbox&category=invoice")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "category_counts" in d
    assert d["category_counts"].get("invoice", 0) >= 1
    assert all(e.get("category") == "invoice" for e in d["emails"])
    assert any(e["from_email"] == "billing-buzon-pytest@example.com" for e in d["emails"])


def test_12_set_category_manual(ctx):
    async def _get_id():
        d = await ctx["db"].email_inbox.find_one({"from_email": "buzon-pytest-1@example.com"})
        return str(d["_id"])
    eid = ctx["loop"].run_until_complete(_get_id())
    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/category", json={"category": "tenant"})
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "tenant"

    async def _check():
        return await ctx["db"].email_inbox.find_one({"_id": ObjectId(eid)})
    doc = ctx["loop"].run_until_complete(_check())
    assert doc["category"] == "tenant" and doc.get("category_manual") is True

    r = _req(ctx, "POST", f"/api/admin/inbox/{eid}/category", json={"category": "banana"})
    assert r.status_code == 400


def test_13_classify_pending(ctx):
    async def _insert():
        res = await ctx["db"].email_inbox.insert_one({
            "folder": "inbox", "from_email": "buzon-pytest-old@example.com",
            "from_name": "Viejo", "to": "admin@inbox.rosshouserentals.com",
            "subject": f"Correo sin clasificar {TEST_TAG}",
            "text": "necesito una factura por favor", "read": True,
        })
        return str(res.inserted_id)
    eid = ctx["loop"].run_until_complete(_insert())
    r = _req(ctx, "POST", "/api/admin/inbox/classify-pending")
    assert r.status_code == 200, r.text
    assert r.json()["classified"] >= 1

    async def _check():
        return await ctx["db"].email_inbox.find_one({"_id": ObjectId(eid)})
    doc = ctx["loop"].run_until_complete(_check())
    assert doc.get("category") == "invoice"


def test_14_send_rejects_invalid_sender(ctx):
    r = _req(ctx, "POST", "/api/admin/inbox/send", json={
        "to": "x@example.com", "subject": "t", "body_text": "b",
        "from_email": "hacker@otrodominio.com"})
    assert r.status_code == 400


def test_15_ai_config_includes_senders(ctx):
    r = _req(ctx, "GET", "/api/admin/inbox/ai-config")
    assert r.status_code == 200
    d = r.json()
    assert "info@rosshouserentals.com" in d.get("senders", {})
    assert d.get("default_sender")


def test_16_reply_from_alias_derivation(ctx):
    from rental.email_inbox_router import _pick_sender
    assert _pick_sender("Contact <contact@rosshouserentals.com>") == "contact@rosshouserentals.com"
    assert _pick_sender("admin@inbox.rosshouserentals.com") == "info@rosshouserentals.com"
    assert _pick_sender("") == "info@rosshouserentals.com"
