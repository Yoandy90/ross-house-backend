"""Buzón de Email del Admin — bandeja de entrada, enviados y redacción.

- POST /api/webhooks/email-inbound : SendGrid Inbound Parse (correos entrantes).
  Requiere MX record en el subdominio (ej. inbox.rosshouserentals.com → mx.sendgrid.net)
  y configurar Inbound Parse en SendGrid apuntando a este endpoint.
- GET  /api/admin/inbox            : listar (folder=inbox|sent, unread_only, q)
- GET  /api/admin/inbox/{id}       : detalle (marca leído)
- POST /api/admin/inbox/send       : redactar/responder (SendGrid) + guarda en "sent".
  Soporta send_at (programado) con batch_id cancelable.
- POST /api/admin/inbox/{id}/read | /unread | DELETE /api/admin/inbox/{id}
- POST /api/admin/inbox/cancel-scheduled/{batch_id} : cancela un envío programado.

Colección: email_inbox
{ folder: inbox|sent|spam, from_email, from_name, to, subject, text, html,
  read, in_reply_to, thread_key, scheduled_for, sendgrid_batch_id,
  ai_draft, ai_draft_at, ai_status(none|draft|sent_auto|approved), created_at }
Config AI: colección app_settings doc {_id:'email_ai'}
{ auto_ack_enabled, auto_draft_enabled, auto_send_enabled, ack_message }
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from bson import ObjectId

from .shared import get_db, auth_admin

router = APIRouter()
logger = logging.getLogger(__name__)

# Remitentes automáticos: nunca responder ni generar borrador (evita loops)
AUTOMATED_SENDERS = ("no-reply", "noreply", "mailer-daemon", "postmaster",
                     "bounce", "notification", "donotreply", "do-not-reply")

# Categorías de clasificación AI de correos entrantes
EMAIL_CATEGORIES = ("lead", "tenant", "provider", "invoice", "other")

# Remitentes disponibles para enviar (dominio autenticado en SendGrid)
SENDER_ADDRESSES = {
    "info@rosshouserentals.com": "General (info)",
    "contact@rosshouserentals.com": "Contacto",
    "yoandy@rosshouserentals.com": "Yoandy",
    "yoandyross@rosshouserentals.com": "Yoandy Ross",
    "payments@rosshouserentals.com": "Pagos",
    "no-reply@rosshouserentals.com": "Notificaciones (no responder)",
    "rentas@rosshouserentals.com": "Rentas / Interesados",
    "mantenimiento@rosshouserentals.com": "Mantenimiento",
    "soporte@rosshouserentals.com": "Soporte",
}

DEFAULT_AI_CONFIG = {
    "auto_ack_enabled": True,      # confirmación automática de recibido
    "auto_draft_enabled": True,    # AI genera borrador de respuesta
    "auto_send_enabled": False,    # AI envía la respuesta SOLA (sin aprobar)
    "ack_message": ("Hola,\n\nGracias por escribir a Ross House Rentals. Hemos recibido tu "
                    "mensaje y te daremos respuesta lo antes posible (normalmente el mismo día).\n\n"
                    "Si es una emergencia de mantenimiento, llámanos al (806) 934-2018.\n\n"
                    "— Ross House Rentals\nDumas, TX · rosshouserentals.com"),
}


def _now():
    return datetime.now(timezone.utc)


def _sg_key() -> str:
    key = os.environ.get("SENDGRID_API_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="SENDGRID_API_KEY no configurada")
    return key


def _from_email() -> str:
    return os.environ.get("SENDGRID_FROM_EMAIL", "no-reply@rosshouserentals.com")


def _doc_out(d: dict) -> dict:
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "scheduled_for", "read_at", "ai_draft_at"):
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


async def get_ai_config() -> dict:
    doc = await get_db().app_settings.find_one({"_id": "email_ai"}) or {}
    return {**DEFAULT_AI_CONFIG, **{k: v for k, v in doc.items() if k != "_id"}}


def _is_automated_sender(email: str) -> bool:
    local = (email or "").lower().split("@")[0]
    return any(tag in local for tag in AUTOMATED_SENDERS)


def _pick_sender(to_field: str) -> str:
    """Responde desde el alias al que escribieron (si es uno permitido)."""
    txt = (to_field or "").lower()
    for addr in SENDER_ADDRESSES:
        if addr in txt:
            return addr
    return _from_email()


async def _send_via_sendgrid(to: str, subject: str, body_text: str,
                             body_html: str = "", from_email: str = "") -> bool:
    """Envío simple (usado por auto-ack y auto-send de AI)."""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        if not body_html:
            body_html = ("<div style='font-family:system-ui,sans-serif;white-space:pre-wrap;'>"
                         + body_text.replace("<", "&lt;") + "</div>")
        msg = Mail(from_email=(from_email or _from_email(), "Ross House Rentals"),
                   to_emails=to, subject=subject, html_content=body_html)
        resp = SendGridAPIClient(_sg_key()).send(msg)
        return resp.status_code in (200, 201, 202)
    except Exception as e:
        logger.error(f"[buzon] envío falló a {to}: {e}")
        return False


async def _generate_ai_draft(email_doc: dict) -> Optional[str]:
    """Genera un borrador de respuesta con el AI Brain (emergentintegrations)."""
    try:
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            logger.warning("[buzon] EMERGENT_LLM_KEY no configurada")
            return None
        from uuid import uuid4
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        from .ai_brain_router import MODEL_PROVIDER, MODEL_NAME
        system = (
            "Eres el asistente de email de Ross House Rentals, una empresa de casas de renta "
            "en Dumas, Texas (dueño: Yoandy Ross, tel (806) 934-2018, web rosshouserentals.com). "
            "Redacta una respuesta profesional, cálida y concisa al email recibido. "
            "REGLAS: responde en el MISMO idioma del email recibido; no inventes precios, "
            "disponibilidad ni fechas — si no tienes el dato, ofrece confirmarlo o agendar una "
            "llamada; firma como 'Ross House Rentals'; devuelve SOLO el cuerpo del email, "
            "sin asunto ni comentarios adicionales."
        )
        user = (f"Email recibido de: {email_doc.get('from_name') or email_doc.get('from_email')}\n"
                f"Asunto: {email_doc.get('subject')}\n\n"
                f"Mensaje:\n{(email_doc.get('text') or '')[:4000]}")
        chat = LlmChat(api_key=api_key, session_id=f"email_draft_{uuid4()}",
                       system_message=system).with_model(MODEL_PROVIDER, MODEL_NAME)
        raw = await chat.send_message(UserMessage(text=user))
        draft = str(raw or "").strip()
        return draft or None
    except Exception as e:
        logger.error(f"[buzon] error generando borrador AI: {e}")
        return None


async def _classify_email(email_doc: dict) -> Optional[str]:
    """Clasifica el correo entrante con AI en una de EMAIL_CATEGORIES."""
    try:
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not api_key:
            return None
        from uuid import uuid4
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        from .ai_brain_router import MODEL_PROVIDER, MODEL_NAME
        system = (
            "Clasifica emails para Ross House Rentals (casas de renta en Dumas, TX). "
            "Responde SOLO con UNA palabra de estas categorías:\n"
            "lead = persona interesada en rentar/aplicar a una propiedad\n"
            "tenant = inquilino actual (mantenimiento, renta, contrato, quejas)\n"
            "invoice = factura, cobro, recibo, bill o estado de cuenta POR PAGAR "
            "(aunque venga de un proveedor/utility, si es un cobro es invoice)\n"
            "provider = proveedor/contratista/servicio escribiendo por algo que NO es un cobro "
            "(plomero, seguro, banco, utility, gobierno)\n"
            "other = cualquier otro caso (promociones, spam suave, personal)"
        )
        user = (f"De: {email_doc.get('from_name') or ''} <{email_doc.get('from_email')}>\n"
                f"Asunto: {email_doc.get('subject')}\n\n"
                f"{(email_doc.get('text') or '')[:1500]}")
        chat = LlmChat(api_key=api_key, session_id=f"email_classify_{uuid4()}",
                       system_message=system).with_model(MODEL_PROVIDER, MODEL_NAME)
        raw = str(await chat.send_message(UserMessage(text=user)) or "").strip().lower()
        for cat in EMAIL_CATEGORIES:
            if cat in raw:
                return cat
        return "other"
    except Exception as e:
        logger.error(f"[buzon] error clasificando email: {e}")
        return None


async def _process_inbound_ai(email_id: str):
    """Background: clasificación + auto-ack + borrador AI (+ auto-envío si está activado)."""
    db = get_db()
    doc = await db.email_inbox.find_one({"_id": ObjectId(email_id)})
    if not doc or doc.get("folder") != "inbox":
        return
    sender = doc.get("from_email", "")

    # 0) Clasificación AI por categoría (siempre, incluso para remitentes automáticos)
    category = await _classify_email(doc)
    if category:
        await db.email_inbox.update_one(
            {"_id": doc["_id"]}, {"$set": {"category": category}})
        logger.info(f"[buzon] {sender} clasificado como '{category}'")

    if _is_automated_sender(sender):
        return
    cfg = await get_ai_config()

    # 1) Confirmación automática de recibido (máx 1 por remitente cada 24h)
    if cfg["auto_ack_enabled"]:
        recent_ack = await db.email_acks.find_one({
            "email": sender,
            "last_ack_at": {"$gt": _now() - timedelta(hours=24)},
        })
        if not recent_ack:
            subject = doc.get("subject") or "(sin asunto)"
            ok = await _send_via_sendgrid(
                sender, f"Re: {subject} — Recibimos tu mensaje ✅", cfg["ack_message"],
                from_email=_pick_sender(doc.get("to", "")))
            if ok:
                await db.email_acks.update_one(
                    {"email": sender}, {"$set": {"email": sender, "last_ack_at": _now()}},
                    upsert=True)
                await db.email_inbox.update_one(
                    {"_id": doc["_id"]}, {"$set": {"ack_sent": True}})
                logger.info(f"[buzon] auto-ack enviado a {sender}")

    # 2) Borrador AI
    if not cfg["auto_draft_enabled"]:
        return
    draft = await _generate_ai_draft(doc)
    if not draft:
        return
    await db.email_inbox.update_one(
        {"_id": doc["_id"]},
        {"$set": {"ai_draft": draft, "ai_draft_at": _now(), "ai_status": "draft"}})
    logger.info(f"[buzon] borrador AI listo para {sender}")

    # 3) Auto-envío (si el admin activó el modo automático)
    if cfg["auto_send_enabled"]:
        subject = doc.get("subject") or "(sin asunto)"
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        reply_from = _pick_sender(doc.get("to", ""))
        ok = await _send_via_sendgrid(sender, reply_subject, draft, from_email=reply_from)
        if ok:
            await db.email_inbox.update_one(
                {"_id": doc["_id"]}, {"$set": {"ai_status": "sent_auto"}})
            await db.email_inbox.insert_one({
                "folder": "sent", "from_email": reply_from,
                "from_name": "Ross House Rentals (AI)", "to": sender,
                "subject": reply_subject, "text": draft[:100000],
                "html": "", "read": True, "ai_sent": True,
                "in_reply_to": str(doc["_id"]),
                "thread_key": subject.lower().replace("re:", "").replace("fwd:", "").strip()[:200],
                "created_at": _now(),
            })
            logger.info(f"[buzon] respuesta AI AUTO-enviada a {sender}")


# ────────────────────────── Webhook entrante (Inbound Parse) ──────────────────────────

@router.post("/webhooks/email-inbound")
async def email_inbound(request: Request, background_tasks: BackgroundTasks):
    """SendGrid Inbound Parse envía multipart/form-data con el correo entrante."""
    form = await request.form()
    from_raw = str(form.get("from", ""))
    # "Nombre <email@x.com>" → separar
    from_name, from_email = "", from_raw
    if "<" in from_raw and ">" in from_raw:
        from_name = from_raw.split("<")[0].strip().strip('"')
        from_email = from_raw.split("<")[1].split(">")[0].strip()

    # Filtro de spam (SendGrid incluye spam_score de SpamAssassin; >=5 = spam)
    try:
        spam_score = float(form.get("spam_score") or 0)
    except (TypeError, ValueError):
        spam_score = 0.0
    is_spam = spam_score >= 5.0

    subject = str(form.get("subject", "(sin asunto)"))
    doc = {
        "folder": "spam" if is_spam else "inbox",
        "from_email": from_email.lower(),
        "from_name": from_name,
        "to": str(form.get("to", "")),
        "subject": subject,
        "text": str(form.get("text", ""))[:100000],
        "html": str(form.get("html", ""))[:300000],
        "read": False,
        "spam_score": spam_score,
        "thread_key": subject.lower().replace("re:", "").replace("fwd:", "").strip()[:200],
        "attachments_count": int(form.get("attachments", 0) or 0),
        "created_at": _now(),
    }
    res = await get_db().email_inbox.insert_one(doc)
    logger.info(f"[buzon] entrante de {from_email}: {subject[:60]}"
                + (" [SPAM]" if is_spam else ""))

    if not is_spam:
        # Auto-ack + borrador AI en background (no bloquea el webhook)
        background_tasks.add_task(_process_inbound_ai, str(res.inserted_id))
        # Notificar al admin que llegó correo nuevo
        try:
            from .notifications_helper import notify_admin  # si existe helper
            await notify_admin(f"📬 Nuevo email de {from_name or from_email}: {subject[:80]}")
        except Exception:
            pass
    return {"ok": True}


# ────────────────────────────── Admin: listar / leer ──────────────────────────────

@router.get("/admin/inbox")
async def list_inbox(request: Request, folder: str = "inbox", q: str = "",
                     unread_only: int = 0, category: str = "",
                     limit: int = 50, skip: int = 0):
    await auth_admin(request)
    db = get_db()
    query: dict = {"folder": folder}
    if unread_only:
        query["read"] = False
    if category and category in EMAIL_CATEGORIES:
        query["category"] = category
    if q:
        query["$or"] = [
            {"subject": {"$regex": q, "$options": "i"}},
            {"from_email": {"$regex": q, "$options": "i"}},
            {"to": {"$regex": q, "$options": "i"}},
            {"text": {"$regex": q, "$options": "i"}},
        ]
    total = await db.email_inbox.count_documents(query)
    unread = await db.email_inbox.count_documents({"folder": "inbox", "read": False})
    # Conteo por categoría (solo bandeja de entrada)
    cat_counts: dict = {}
    async for row in db.email_inbox.aggregate([
            {"$match": {"folder": "inbox"}},
            {"$group": {"_id": "$category", "n": {"$sum": 1}}}]):
        cat_counts[row["_id"] or "unclassified"] = row["n"]
    docs = await (db.email_inbox.find(query, {"html": 0, "ai_draft": 0})
                  .sort([("created_at", -1)]).skip(skip).limit(min(limit, 100)).to_list(100))
    items = []
    for d in docs:
        d["preview"] = (d.pop("text", "") or "")[:140]
        items.append(_doc_out(d))
    return {"success": True, "total": total, "unread_count": unread,
            "category_counts": cat_counts, "emails": items}


@router.get("/admin/inbox/ai-config")
async def read_ai_config(request: Request):
    await auth_admin(request)
    return {"success": True, "config": await get_ai_config(),
            "senders": SENDER_ADDRESSES, "default_sender": _from_email()}


@router.put("/admin/inbox/ai-config")
async def update_ai_config(request: Request):
    """Body: {auto_ack_enabled?, auto_draft_enabled?, auto_send_enabled?, ack_message?}"""
    await auth_admin(request)
    data = await request.json()
    sets = {}
    for key in ("auto_ack_enabled", "auto_draft_enabled", "auto_send_enabled"):
        if key in data:
            sets[key] = bool(data[key])
    if "ack_message" in data:
        msg = str(data["ack_message"]).strip()
        if not msg:
            raise HTTPException(status_code=400, detail="ack_message no puede estar vacío")
        sets["ack_message"] = msg[:2000]
    if not sets:
        raise HTTPException(status_code=400, detail="Sin cambios")
    sets["updated_at"] = _now()
    await get_db().app_settings.update_one({"_id": "email_ai"}, {"$set": sets}, upsert=True)
    return {"success": True, "config": await get_ai_config()}


@router.get("/admin/inbox/{email_id}")
async def get_email(email_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    try:
        oid = ObjectId(email_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    doc = await db.email_inbox.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Email no encontrado")
    if not doc.get("read"):
        await db.email_inbox.update_one({"_id": oid}, {"$set": {"read": True, "read_at": _now()}})
        doc["read"] = True
    # hilo: otros correos con mismo thread_key
    thread = []
    if doc.get("thread_key"):
        cursor = db.email_inbox.find(
            {"thread_key": doc["thread_key"], "_id": {"$ne": oid}},
            {"html": 0}).sort([("created_at", 1)]).limit(20)
        thread = [_doc_out(t) for t in await cursor.to_list(20)]
    return {"success": True, "email": _doc_out(doc), "thread": thread}


@router.post("/admin/inbox/{email_id}/read")
async def mark_read(email_id: str, request: Request):
    await auth_admin(request)
    await get_db().email_inbox.update_one(
        {"_id": ObjectId(email_id)}, {"$set": {"read": True, "read_at": _now()}})
    return {"success": True}


@router.post("/admin/inbox/{email_id}/unread")
async def mark_unread(email_id: str, request: Request):
    await auth_admin(request)
    await get_db().email_inbox.update_one(
        {"_id": ObjectId(email_id)}, {"$set": {"read": False}})
    return {"success": True}


@router.delete("/admin/inbox/{email_id}")
async def delete_email(email_id: str, request: Request):
    await auth_admin(request)
    await get_db().email_inbox.delete_one({"_id": ObjectId(email_id)})
    return {"success": True}


# ────────────────────────────── Enviar / responder ──────────────────────────────

@router.post("/admin/inbox/send")
async def send_email(request: Request):
    """Redactar o responder. Body: {to, subject, body_html?, body_text?, from_email?,
    reply_to_id?, send_at? (ISO), cc?}. Programación via SendGrid send_at (máx 72h)."""
    admin = await auth_admin(request)
    data = await request.json()
    to = str(data.get("to", "")).strip()
    subject = str(data.get("subject", "")).strip()
    body_html = data.get("body_html") or ""
    body_text = data.get("body_text") or ""
    if not to or not subject or not (body_html or body_text):
        raise HTTPException(status_code=400, detail="Faltan destinatario, asunto o cuerpo")
    sender = str(data.get("from_email") or _from_email()).strip().lower()
    if sender not in SENDER_ADDRESSES:
        raise HTTPException(status_code=400,
                            detail="Remitente no permitido — usa una dirección del dominio")

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Cc, BatchId, SendAt

    if not body_html:
        body_html = "<div style='font-family:system-ui,sans-serif;white-space:pre-wrap;'>" \
                    + body_text.replace("<", "&lt;") + "</div>"

    msg = Mail(
        from_email=(sender, "Ross House Rentals"),
        to_emails=[e.strip() for e in to.split(",") if e.strip()],
        subject=subject,
        html_content=body_html,
    )
    cc = data.get("cc")
    if cc:
        for c in str(cc).split(","):
            if c.strip():
                msg.add_cc(Cc(c.strip()))

    scheduled_for = None
    batch_id = None
    send_at_iso = data.get("send_at")
    sg = SendGridAPIClient(_sg_key())
    if send_at_iso:
        try:
            scheduled_for = datetime.fromisoformat(send_at_iso.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="send_at inválido (ISO 8601)")
        delta = (scheduled_for - _now()).total_seconds()
        if delta < 60 or delta > 72 * 3600:
            raise HTTPException(status_code=400,
                                detail="send_at debe estar entre 1 min y 72 horas en el futuro")
        batch_resp = sg.client.mail.batch.post()
        import json as _json
        batch_id = _json.loads(batch_resp.body).get("batch_id")
        msg.batch_id = BatchId(batch_id)
        msg.send_at = SendAt(int(scheduled_for.timestamp()))

    resp = sg.send(msg)
    if resp.status_code not in (200, 201, 202):
        raise HTTPException(status_code=502, detail=f"SendGrid HTTP {resp.status_code}")

    doc = {
        "folder": "sent",
        "from_email": sender,
        "from_name": "Ross House Rentals",
        "to": to,
        "cc": cc or None,
        "subject": subject,
        "text": body_text[:100000],
        "html": body_html[:300000],
        "read": True,
        "in_reply_to": data.get("reply_to_id"),
        "thread_key": subject.lower().replace("re:", "").replace("fwd:", "").strip()[:200],
        "scheduled_for": scheduled_for,
        "sendgrid_batch_id": batch_id,
        "sent_by": admin.get("email", ""),
        "created_at": _now(),
    }
    res = await get_db().email_inbox.insert_one(doc)
    return {"success": True, "id": str(res.inserted_id),
            "scheduled": bool(scheduled_for), "batch_id": batch_id,
            "message": ("Programado para " + scheduled_for.isoformat()) if scheduled_for
                       else "Email enviado"}


@router.post("/admin/inbox/cancel-scheduled/{batch_id}")
async def cancel_scheduled(batch_id: str, request: Request):
    """Cancela un envío programado (antes de su send_at)."""
    await auth_admin(request)
    from sendgrid import SendGridAPIClient
    sg = SendGridAPIClient(_sg_key())
    resp = sg.client.user.scheduled_sends.post(request_body={
        "batch_id": batch_id, "status": "cancel"})
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"SendGrid HTTP {resp.status_code}")
    await get_db().email_inbox.update_one(
        {"sendgrid_batch_id": batch_id},
        {"$set": {"cancelled": True, "subject_note": "CANCELADO"}})
    return {"success": True, "message": "Envío programado cancelado"}


# ────────────────────────────── AI: config y borradores ──────────────────────────────

@router.post("/admin/inbox/{email_id}/ai-draft")
async def regenerate_ai_draft(email_id: str, request: Request):
    """(Re)genera el borrador AI para un email recibido."""
    await auth_admin(request)
    db = get_db()
    try:
        oid = ObjectId(email_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    doc = await db.email_inbox.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Email no encontrado")
    if doc.get("folder") == "sent":
        raise HTTPException(status_code=400, detail="Solo aplica a correos recibidos")
    draft = await _generate_ai_draft(doc)
    if not draft:
        raise HTTPException(status_code=502, detail="AI no disponible — revisa la LLM key en AI Brain")
    await db.email_inbox.update_one(
        {"_id": oid}, {"$set": {"ai_draft": draft, "ai_draft_at": _now(), "ai_status": "draft"}})
    return {"success": True, "ai_draft": draft}


@router.post("/admin/inbox/{email_id}/approve-draft")
async def approve_ai_draft(email_id: str, request: Request):
    """Aprueba y envía la respuesta AI. Body opcional: {body} (borrador editado)."""
    admin = await auth_admin(request)
    db = get_db()
    try:
        oid = ObjectId(email_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    doc = await db.email_inbox.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Email no encontrado")
    data = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    body = str(data.get("body") or doc.get("ai_draft") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="No hay borrador que enviar")

    subject = doc.get("subject") or "(sin asunto)"
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    reply_from = str(data.get("from_email") or "").strip().lower()
    if reply_from and reply_from not in SENDER_ADDRESSES:
        raise HTTPException(status_code=400, detail="Remitente no permitido")
    if not reply_from:
        reply_from = _pick_sender(doc.get("to", ""))
    ok = await _send_via_sendgrid(doc["from_email"], reply_subject, body,
                                  from_email=reply_from)
    if not ok:
        raise HTTPException(status_code=502, detail="SendGrid rechazó el envío")

    await db.email_inbox.update_one(
        {"_id": oid}, {"$set": {"ai_status": "approved", "ai_draft": body}})
    await db.email_inbox.insert_one({
        "folder": "sent", "from_email": reply_from,
        "from_name": "Ross House Rentals", "to": doc["from_email"],
        "subject": reply_subject, "text": body[:100000], "html": "",
        "read": True, "in_reply_to": str(oid), "ai_approved": True,
        "thread_key": subject.lower().replace("re:", "").replace("fwd:", "").strip()[:200],
        "sent_by": admin.get("email", ""), "created_at": _now(),
    })
    return {"success": True, "message": "Respuesta aprobada y enviada"}


@router.post("/admin/inbox/classify-pending")
async def classify_pending(request: Request):
    """Clasifica con AI los correos de la bandeja que aún no tienen categoría (máx 15)."""
    await auth_admin(request)
    db = get_db()
    docs = await (db.email_inbox
                  .find({"folder": "inbox", "category": {"$exists": False}}, {"html": 0})
                  .sort([("created_at", -1)]).limit(15).to_list(15))
    classified = 0
    for doc in docs:
        cat = await _classify_email(doc)
        if cat:
            await db.email_inbox.update_one(
                {"_id": doc["_id"]}, {"$set": {"category": cat}})
            classified += 1
    return {"success": True, "classified": classified, "pending_found": len(docs)}


@router.post("/admin/inbox/{email_id}/category")
async def set_category(email_id: str, request: Request):
    """Cambia la categoría manualmente. Body: {category: lead|tenant|provider|invoice|other}"""
    await auth_admin(request)
    data = await request.json()
    category = str(data.get("category", "")).strip().lower()
    if category not in EMAIL_CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"category debe ser una de: {', '.join(EMAIL_CATEGORIES)}")
    try:
        oid = ObjectId(email_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    res = await get_db().email_inbox.update_one(
        {"_id": oid}, {"$set": {"category": category, "category_manual": True}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Email no encontrado")
    return {"success": True, "category": category}


@router.post("/admin/inbox/{email_id}/move")
async def move_email(email_id: str, request: Request):
    """Mueve un email entre carpetas. Body: {folder: inbox|spam}"""
    await auth_admin(request)
    data = await request.json()
    folder = str(data.get("folder", "")).strip()
    if folder not in ("inbox", "spam"):
        raise HTTPException(status_code=400, detail="folder debe ser inbox o spam")
    try:
        oid = ObjectId(email_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    res = await get_db().email_inbox.update_one({"_id": oid}, {"$set": {"folder": folder}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Email no encontrado")
    return {"success": True}
