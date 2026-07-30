"""Newsletter — public email subscription + admin campaign management.

Public:
  POST /public/newsletter/subscribe        → register an email (idempotent)
  GET  /public/newsletter/unsubscribe      → one-click unsubscribe (token link)

Admin:
  GET    /admin/newsletter/subscribers     → list + stats
  DELETE /admin/newsletter/subscribers/{id}
  POST   /admin/newsletter/campaigns       → send email blast (newsletter / leads / both)
  GET    /admin/newsletter/campaigns       → campaign history
"""
import os
import re
import uuid
import secrets
import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse

from .shared import get_db, auth_admin, serialize

router = APIRouter()
logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


async def _sendgrid():
    """Return (api_key, from_email) from env or DB config."""
    db = get_db()
    key = os.getenv('SENDGRID_API_KEY', '')
    from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
    if not key:
        cfg = await db.api_config.find_one({'_id': 'main'}) or {}
        key = cfg.get('sendgrid_api_key', '')
        from_email = cfg.get('sendgrid_from_email', from_email)
    return key, from_email


def _campaign_html(subject: str, message: str, unsubscribe_url: str | None) -> str:
    body_html = message.replace('\n', '<br>')
    unsub = (
        f'<p style="font-size:11px;color:#94a3b8;margin-top:24px">'
        f'¿No quieres recibir más noticias? '
        f'<a href="{unsubscribe_url}" style="color:#94a3b8">Cancelar suscripción</a></p>'
        if unsubscribe_url else ''
    )
    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:0 auto;background:#ffffff">
      <div style="background:linear-gradient(135deg,#0891b2,#0e7490);padding:24px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="color:#fff;font-size:20px;margin:0">Ross House Rentals</h1>
        <p style="color:#cffafe;font-size:12px;margin:4px 0 0">Dumas, Texas</p>
      </div>
      <div style="padding:28px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
        <h2 style="color:#0f172a;font-size:17px;margin:0 0 14px">{subject}</h2>
        <div style="color:#334155;font-size:14px;line-height:1.6">{body_html}</div>
        <p style="margin-top:24px;font-size:12px;color:#64748b">
          Ross House Rentals LLC · Dumas, TX · (806) 934-2018 ·
          <a href="https://www.rosshouserentals.com" style="color:#0891b2">rosshouserentals.com</a>
        </p>
        {unsub}
      </div>
    </div>
    """


async def _send_one(sg_key: str, from_email: str, to_email: str, subject: str, html: str) -> bool:
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        mail = Mail(
            from_email=Email(from_email, "Ross House Rentals"),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html),
        )
        resp = sg.client.mail.send.post(request_body=mail.get())
        return resp.status_code in (200, 201, 202)
    except Exception as e:
        logger.warning(f"[newsletter] send to {to_email} failed: {e}")
        return False


# ════════════════════════════════ PUBLIC ════════════════════════════════

@router.post('/public/newsletter/subscribe')
async def public_subscribe(request: Request):
    """Register an email for news/rentals updates. Idempotent."""
    data = await request.json()
    email = (data.get('email') or '').strip().lower()
    name = (data.get('name') or '').strip()[:120]
    source = (data.get('source') or 'web')[:40]
    lang = (data.get('lang') or 'es')[:5]

    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Email inválido")

    db = get_db()
    now = datetime.utcnow()
    existing = await db.newsletter_subscribers.find_one({"email": email})
    if existing:
        # Re-activate if previously unsubscribed
        if existing.get('unsubscribed'):
            await db.newsletter_subscribers.update_one(
                {"_id": existing["_id"]},
                {"$set": {"unsubscribed": False, "resubscribed_at": now}},
            )
            return {"success": True, "already_subscribed": False, "message": "¡Suscripción reactivada!"}
        return {"success": True, "already_subscribed": True, "message": "Ya estás suscrito 🙌"}

    await db.newsletter_subscribers.insert_one({
        "_id": str(uuid.uuid4()),
        "email": email,
        "name": name,
        "source": source,          # modal | footer | section | web
        "lang": lang,
        "unsubscribed": False,
        "unsubscribe_token": secrets.token_urlsafe(24),
        "ip_address": request.client.host if request.client else '',
        "created_at": now,
    })
    logger.info(f"📬 Newsletter: new subscriber {email} (source={source})")
    return {"success": True, "already_subscribed": False, "message": "¡Listo! Te mantendremos al día 🎉"}


@router.get('/public/newsletter/unsubscribe')
async def public_unsubscribe(token: str = ''):
    """One-click unsubscribe from campaign email footer link."""
    db = get_db()
    sub = await db.newsletter_subscribers.find_one({"unsubscribe_token": token}) if token else None
    if sub:
        await db.newsletter_subscribers.update_one(
            {"_id": sub["_id"]},
            {"$set": {"unsubscribed": True, "unsubscribed_at": datetime.utcnow()}},
        )
        msg = "Has sido dado de baja. No recibirás más noticias nuestras. 👋"
    else:
        msg = "Enlace inválido o ya procesado."
    return HTMLResponse(f"""
    <html><head><meta charset='utf-8'><title>Ross House Rentals</title></head>
    <body style='font-family:Arial;background:#f1f5f9;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>
      <div style='background:#fff;padding:40px;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,.08);text-align:center;max-width:420px'>
        <h2 style='color:#0f172a;margin:0 0 10px'>Ross House Rentals</h2>
        <p style='color:#475569'>{msg}</p>
        <a href='https://www.rosshouserentals.com' style='color:#0891b2;font-size:14px'>← Volver al sitio</a>
      </div>
    </body></html>
    """)


# ════════════════════════════════ ADMIN ════════════════════════════════

@router.get('/admin/newsletter/subscribers')
async def admin_list_subscribers(request: Request, search: str = '', limit: int = 500):
    await auth_admin(request)
    db = get_db()
    q: dict = {}
    if search:
        q = {"$or": [
            {"email": {"$regex": re.escape(search), "$options": "i"}},
            {"name": {"$regex": re.escape(search), "$options": "i"}},
        ]}
    subs = await db.newsletter_subscribers.find(q).sort("created_at", -1).limit(limit).to_list(limit)
    total = await db.newsletter_subscribers.count_documents({})
    active = await db.newsletter_subscribers.count_documents({"unsubscribed": {"$ne": True}})
    leads_count = await db.tenant_leads.count_documents({})
    return {
        "success": True,
        "subscribers": [serialize(s) for s in subs],
        "stats": {"total": total, "active": active, "unsubscribed": total - active, "leads": leads_count},
    }


@router.delete('/admin/newsletter/subscribers/{sub_id}')
async def admin_delete_subscriber(sub_id: str, request: Request):
    await auth_admin(request)
    await get_db().newsletter_subscribers.delete_one({"_id": sub_id})
    return {"success": True}


async def _run_campaign(campaign_id: str, subject: str, message: str, audience: str):
    """Background task: send the blast and update campaign stats."""
    db = get_db()
    sg_key, from_email = await _sendgrid()
    if not sg_key:
        await db.newsletter_campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"status": "failed", "error": "SendGrid no configurado"}})
        return

    # Build recipient list (deduped by email)
    recipients: dict[str, dict] = {}
    if audience in ('newsletter', 'both'):
        async for s in db.newsletter_subscribers.find({"unsubscribed": {"$ne": True}}):
            recipients[s['email']] = {"email": s['email'], "unsub_token": s.get('unsubscribe_token')}
    if audience in ('leads', 'both'):
        async for l in db.tenant_leads.find({}, {"email": 1}):
            em = (l.get('email') or '').strip().lower()
            if em and em not in recipients:
                recipients[em] = {"email": em, "unsub_token": None}

    sent = failed = 0
    for r in recipients.values():
        unsub_url = (
            f"https://www.rosshouserentals.com/api/public/newsletter/unsubscribe?token={r['unsub_token']}"
            if r.get('unsub_token') else None
        )
        ok = await _send_one(sg_key, from_email, r['email'], subject,
                             _campaign_html(subject, message, unsub_url))
        sent += 1 if ok else 0
        failed += 0 if ok else 1

    await db.newsletter_campaigns.update_one(
        {"_id": campaign_id},
        {"$set": {"status": "sent", "sent": sent, "failed": failed,
                  "total_recipients": len(recipients), "completed_at": datetime.utcnow()}})
    logger.info(f"📣 Campaign {campaign_id}: sent={sent} failed={failed}")


@router.post('/admin/newsletter/campaigns')
async def admin_create_campaign(request: Request, background_tasks: BackgroundTasks):
    admin = await auth_admin(request)
    data = await request.json()
    subject = (data.get('subject') or '').strip()
    message = (data.get('message') or '').strip()
    audience = data.get('audience') or 'newsletter'   # newsletter | leads | both

    if not subject or not message:
        raise HTTPException(status_code=400, detail="Asunto y mensaje son requeridos")
    if audience not in ('newsletter', 'leads', 'both'):
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    campaign_id = str(uuid.uuid4())
    await get_db().newsletter_campaigns.insert_one({
        "_id": campaign_id,
        "subject": subject,
        "message": message,
        "audience": audience,
        "status": "sending",
        "sent": 0, "failed": 0, "total_recipients": 0,
        "created_by": admin.get('email', ''),
        "created_at": datetime.utcnow(),
    })
    background_tasks.add_task(_run_campaign, campaign_id, subject, message, audience)
    return {"success": True, "campaign_id": campaign_id,
            "message": "Campaña en envío — refresca en unos segundos para ver el progreso."}


@router.get('/admin/newsletter/campaigns')
async def admin_list_campaigns(request: Request, limit: int = 50):
    await auth_admin(request)
    camps = await get_db().newsletter_campaigns.find().sort("created_at", -1).limit(limit).to_list(limit)
    return {"success": True, "campaigns": [serialize(c) for c in camps]}
