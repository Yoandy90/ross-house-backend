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


def _campaign_html(subject: str, message: str, unsubscribe_url: str | None, extra_html: str = '') -> str:
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
        {extra_html}
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


async def _run_campaign(campaign_id: str, subject: str, message: str, audience: str, extra_html: str = ''):
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
                             _campaign_html(subject, message, unsub_url, extra_html))
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


# ═══════════════════ RESUMEN SEMANAL (digest) ═══════════════════

@router.post('/admin/newsletter/weekly-digest/send')
async def admin_send_weekly_digest(request: Request):
    """Manually trigger the weekly metrics digest email (also runs auto every Monday 8AM CT)."""
    await auth_admin(request)
    from rental.weekly_digest_cron import send_weekly_digest
    result = await send_weekly_digest(get_db())
    if not result.get('success'):
        raise HTTPException(status_code=502, detail=result.get('error', 'Error enviando digest'))
    return result


# ═══════════════════ AUTO-CAMPAÑA: propiedad disponible ═══════════════════

ANNOUNCE_COOLDOWN_DAYS = 14


async def announce_property_available(property_id: str):
    """Auto-send a newsletter campaign when a property becomes available.
    Skips if the property was already announced in the last 14 days."""
    from bson import ObjectId
    db = get_db()
    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        prop = None
    if not prop or prop.get('status') != 'available':
        return

    # Dedup: don't spam if the status is toggled repeatedly
    last = prop.get('newsletter_announced_at')
    if last and (datetime.utcnow() - last).days < ANNOUNCE_COOLDOWN_DAYS:
        logger.info(f"[newsletter] property {property_id} already announced {last} — skipping")
        return

    name = prop.get('name') or prop.get('address') or 'Nueva propiedad'
    address = prop.get('address', '')
    city = prop.get('city', 'Dumas')
    rent = float(prop.get('rent_amount') or 0)
    beds = prop.get('bedrooms', '')
    baths = prop.get('bathrooms', '')
    sqft = prop.get('square_feet', 0)

    # Property photo (first one)
    photo_html = ''
    photos = prop.get('photos') or []
    if photos and isinstance(photos[0], str):
        clean = photos[0]
        if clean.startswith('ross-rentals/'):
            clean = clean[len('ross-rentals/'):]
        photo_url = f"https://www.rosshouserentals.com/api/public/property-file/{clean}"
        photo_html = f'<img src="{photo_url}" alt="{name}" style="width:100%;border-radius:12px;margin-bottom:14px" />'

    sqft_txt = f" · {sqft} sqft" if sqft else ""
    extra_html = f"""
    {photo_html}
    <div style="background:#f0fdfa;border:1px solid #99f6e4;border-radius:12px;padding:16px;margin-bottom:16px">
      <div style="font-size:16px;font-weight:bold;color:#0f172a">{name}</div>
      <div style="font-size:13px;color:#475569;margin-top:2px">📍 {address}, {city}, TX</div>
      <div style="font-size:13px;color:#475569;margin-top:2px">🛏 {beds} hab · 🛁 {baths} baños{sqft_txt}</div>
      <div style="font-size:22px;font-weight:bold;color:#0d9488;margin-top:8px">${rent:,.0f}<span style="font-size:13px;color:#64748b;font-weight:normal">/mes</span></div>
      <a href="https://www.rosshouserentals.com/#properties"
         style="display:inline-block;margin-top:12px;background:#0d9488;color:#fff;font-weight:bold;font-size:13px;padding:10px 22px;border-radius:10px;text-decoration:none">
        Ver propiedad y aplicar →
      </a>
    </div>
    """
    subject = f"🏡 ¡Nueva casa disponible en {city}! — {name} · ${rent:,.0f}/mes"
    message = (
        "Acaba de quedar disponible esta propiedad y quisimos avisarte primero a ti.\n"
        "Las casas en Dumas se rentan rápido — si te interesa, aplica hoy mismo o "
        "contáctanos al (806) 934-2018."
    )

    campaign_id = str(uuid.uuid4())
    await db.newsletter_campaigns.insert_one({
        "_id": campaign_id,
        "subject": subject,
        "message": message,
        "audience": "newsletter",
        "status": "sending",
        "sent": 0, "failed": 0, "total_recipients": 0,
        "auto": True,
        "property_id": property_id,
        "created_by": "auto:property_available",
        "created_at": datetime.utcnow(),
    })
    await db.properties.update_one(
        {"_id": prop["_id"]},
        {"$set": {"newsletter_announced_at": datetime.utcnow()}},
    )
    await _run_campaign(campaign_id, subject, message, "newsletter", extra_html)
