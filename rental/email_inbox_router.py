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
{ folder: inbox|sent, from_email, from_name, to, subject, text, html,
  read, in_reply_to, thread_key, scheduled_for, sendgrid_batch_id, created_at }
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from bson import ObjectId

from .shared import get_db, auth_admin

router = APIRouter()
logger = logging.getLogger(__name__)


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
    for k in ("created_at", "scheduled_for", "read_at"):
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


# ────────────────────────── Webhook entrante (Inbound Parse) ──────────────────────────

@router.post("/webhooks/email-inbound")
async def email_inbound(request: Request):
    """SendGrid Inbound Parse envía multipart/form-data con el correo entrante."""
    form = await request.form()
    from_raw = str(form.get("from", ""))
    # "Nombre <email@x.com>" → separar
    from_name, from_email = "", from_raw
    if "<" in from_raw and ">" in from_raw:
        from_name = from_raw.split("<")[0].strip().strip('"')
        from_email = from_raw.split("<")[1].split(">")[0].strip()

    subject = str(form.get("subject", "(sin asunto)"))
    doc = {
        "folder": "inbox",
        "from_email": from_email.lower(),
        "from_name": from_name,
        "to": str(form.get("to", "")),
        "subject": subject,
        "text": str(form.get("text", ""))[:100000],
        "html": str(form.get("html", ""))[:300000],
        "read": False,
        "thread_key": subject.lower().replace("re:", "").replace("fwd:", "").strip()[:200],
        "attachments_count": int(form.get("attachments", 0) or 0),
        "created_at": _now(),
    }
    await get_db().email_inbox.insert_one(doc)
    logger.info(f"[buzon] entrante de {from_email}: {subject[:60]}")

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
                     unread_only: int = 0, limit: int = 50, skip: int = 0):
    await auth_admin(request)
    db = get_db()
    query: dict = {"folder": folder}
    if unread_only:
        query["read"] = False
    if q:
        query["$or"] = [
            {"subject": {"$regex": q, "$options": "i"}},
            {"from_email": {"$regex": q, "$options": "i"}},
            {"to": {"$regex": q, "$options": "i"}},
            {"text": {"$regex": q, "$options": "i"}},
        ]
    total = await db.email_inbox.count_documents(query)
    unread = await db.email_inbox.count_documents({"folder": "inbox", "read": False})
    docs = await (db.email_inbox.find(query, {"html": 0})
                  .sort([("created_at", -1)]).skip(skip).limit(min(limit, 100)).to_list(100))
    items = []
    for d in docs:
        d["preview"] = (d.pop("text", "") or "")[:140]
        items.append(_doc_out(d))
    return {"success": True, "total": total, "unread_count": unread, "emails": items}


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
    """Redactar o responder. Body: {to, subject, body_html?, body_text?,
    reply_to_id?, send_at? (ISO), cc?}. Programación via SendGrid send_at (máx 72h)."""
    admin = await auth_admin(request)
    data = await request.json()
    to = str(data.get("to", "")).strip()
    subject = str(data.get("subject", "")).strip()
    body_html = data.get("body_html") or ""
    body_text = data.get("body_text") or ""
    if not to or not subject or not (body_html or body_text):
        raise HTTPException(status_code=400, detail="Faltan destinatario, asunto o cuerpo")

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Cc, BatchId, SendAt

    if not body_html:
        body_html = "<div style='font-family:system-ui,sans-serif;white-space:pre-wrap;'>" \
                    + body_text.replace("<", "&lt;") + "</div>"

    msg = Mail(
        from_email=(_from_email(), "Ross House Rentals"),
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
        "from_email": _from_email(),
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
