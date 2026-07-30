"""
Weekly Digest cron — sends the admin a weekly email summary with metrics:
new newsletter subscribers, new leads, site visits, and payments received.

Runs continuously; every 30 min checks if it's the configured weekday+hour
(America/Chicago). Idempotency marker prevents duplicates within the week.

Config stored in `app_settings` document with _id='weekly_digest':
  { enabled: bool (default True), weekday: 0 (Mon), hour_ct: 8, last_run_at }
"""
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone.utc

CHECK_INTERVAL_SECONDS = 30 * 60
logger = logging.getLogger(__name__)


async def _gather_metrics(db) -> dict:
    """Collect the last-7-days metrics."""
    since = datetime.utcnow() - timedelta(days=7)

    # Newsletter
    new_subs = await db.newsletter_subscribers.count_documents({"created_at": {"$gte": since}})
    total_subs = await db.newsletter_subscribers.count_documents({"unsubscribed": {"$ne": True}})
    recent_subs = await db.newsletter_subscribers.find(
        {"created_at": {"$gte": since}}).sort("created_at", -1).limit(10).to_list(10)

    # Leads (waitlist)
    new_leads = await db.tenant_leads.count_documents({"created_at": {"$gte": since}})

    # Visits
    visits = await db.visitor_sessions.count_documents(
        {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}})
    pageviews = await db.visitor_events.count_documents({"ts": {"$gte": since}})

    # Payments received (rent + vault + payment links)
    pay_total = 0.0
    pay_count = 0
    async for p in db.rental_payments.find(
            {"status": {"$in": ["completed", "paid"]}, "created_at": {"$gte": since}}):
        pay_total += float(p.get("total_paid") or p.get("amount") or 0)
        pay_count += 1
    async for c in db.vault_charges.find({"created_at": {"$gte": since}, "status": {"$in": ["succeeded", "processing"]}}):
        pay_total += float(c.get("amount") or 0)
        pay_count += 1
    async for pl in db.payment_links.find({"paid_at": {"$gte": since}}):
        pay_total += float(pl.get("amount_paid") or pl.get("amount") or 0)
        pay_count += 1

    # Portfolio snapshot
    props_total = await db.properties.count_documents({})
    props_rented = await db.properties.count_documents({"status": "rented"})
    props_available = await db.properties.count_documents({"status": "available"})

    return {
        "new_subs": new_subs, "total_subs": total_subs, "recent_subs": recent_subs,
        "new_leads": new_leads,
        "visits": visits, "pageviews": pageviews,
        "pay_total": pay_total, "pay_count": pay_count,
        "props_total": props_total, "props_rented": props_rented, "props_available": props_available,
    }


def _digest_html(m: dict) -> str:
    week_of = datetime.now(CT).strftime("%d/%m/%Y")
    subs_rows = "".join(
        f"<li style='font-size:12px;color:#475569'>{s.get('email')} <span style='color:#94a3b8'>({(s.get('source') or 'web')})</span></li>"
        for s in m["recent_subs"]
    ) or "<li style='font-size:12px;color:#94a3b8'>Sin nuevos suscriptores esta semana</li>"

    def card(emoji, label, value, sub=''):
        return f"""
        <td style="width:33%;padding:6px">
          <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:14px;text-align:center">
            <div style="font-size:20px">{emoji}</div>
            <div style="font-size:22px;font-weight:bold;color:#0f172a;margin-top:2px">{value}</div>
            <div style="font-size:11px;color:#64748b">{label}</div>
            {f"<div style='font-size:10px;color:#94a3b8'>{sub}</div>" if sub else ''}
          </div>
        </td>"""

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;background:#ffffff">
      <div style="background:linear-gradient(135deg,#0f172a,#1e293b);padding:26px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="color:#fff;font-size:20px;margin:0">📊 Resumen Semanal</h1>
        <p style="color:#94a3b8;font-size:12px;margin:6px 0 0">Ross House Rentals · Semana del {week_of} · Últimos 7 días</p>
      </div>
      <div style="padding:24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">

        <table style="width:100%;border-collapse:collapse"><tr>
          {card('💰', 'Pagos recibidos', f"${m['pay_total']:,.0f}", f"{m['pay_count']} transacción(es)")}
          {card('👀', 'Visitas al sitio', f"{m['visits']:,}", f"{m['pageviews']:,} páginas vistas")}
          {card('📬', 'Nuevos suscriptores', f"+{m['new_subs']}", f"{m['total_subs']} activos en total")}
        </tr><tr>
          {card('📝', 'Nuevos interesados', f"+{m['new_leads']}", 'waitlist')}
          {card('🏠', 'Rentadas', f"{m['props_rented']}/{m['props_total']}", 'propiedades')}
          {card('✨', 'Disponibles', str(m['props_available']), 'listas para rentar')}
        </tr></table>

        <h3 style="font-size:13px;color:#0f172a;margin:20px 0 6px">📬 Últimos suscriptores</h3>
        <ul style="margin:0;padding-left:18px">{subs_rows}</ul>

        <a href="https://www.rosshouserentals.com/admin"
           style="display:inline-block;margin-top:20px;background:#0891b2;color:#fff;font-weight:bold;font-size:13px;padding:11px 24px;border-radius:10px;text-decoration:none">
          Abrir panel de administración →
        </a>
        <p style="margin-top:20px;font-size:11px;color:#94a3b8">
          Este resumen se envía automáticamente cada semana. Ross House Rentals LLC · Dumas, TX
        </p>
      </div>
    </div>
    """


async def send_weekly_digest(db) -> dict:
    """Build and email the digest to the admin. Returns result stats."""
    m = await _gather_metrics(db)
    html = _digest_html(m)

    # SendGrid credentials (env or DB config)
    sg_key = os.getenv('SENDGRID_API_KEY', '')
    from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
    if not sg_key:
        cfg = await db.api_config.find_one({'_id': 'main'}) or {}
        sg_key = cfg.get('sendgrid_api_key', '')
        from_email = cfg.get('sendgrid_from_email', from_email)
    if not sg_key:
        return {"success": False, "error": "SendGrid no configurado"}

    company = await db.rental_config.find_one({'type': 'company'}) or {}
    recipients = {e for e in ['yoandyross@gmail.com', company.get('email', '')] if e}

    subject = f"📊 Resumen semanal — ${m['pay_total']:,.0f} recibidos · {m['visits']} visitas · +{m['new_subs']} suscriptores"
    sent = 0
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        for rcpt in recipients:
            mail = Mail(
                from_email=Email(from_email, "Ross House Rentals"),
                to_emails=To(rcpt),
                subject=subject,
                html_content=Content("text/html", html),
            )
            resp = sg.client.mail.send.post(request_body=mail.get())
            if resp.status_code in (200, 201, 202):
                sent += 1
    except Exception as e:
        logger.exception(f"[weekly_digest] send failed: {e}")
        return {"success": False, "error": str(e)[:200]}

    await db.app_settings.update_one(
        {"_id": "weekly_digest"},
        {"$set": {"last_run_at": datetime.now(timezone.utc), "last_metrics": {
            "pay_total": m['pay_total'], "visits": m['visits'],
            "new_subs": m['new_subs'], "new_leads": m['new_leads']}}},
        upsert=True,
    )
    logger.info(f"📊 Weekly digest sent to {sent} recipient(s)")
    return {"success": True, "sent": sent, "metrics": {k: v for k, v in m.items() if k != 'recent_subs'}}


async def _should_run_now(db) -> bool:
    cfg = await db.app_settings.find_one({"_id": "weekly_digest"}) or {}
    if not cfg.get("enabled", True):
        return False

    target_weekday = int(cfg.get("weekday", 0))   # 0 = lunes
    target_hour = int(cfg.get("hour_ct", 8))      # 8 AM CT

    now_ct = datetime.now(CT)
    if now_ct.weekday() != target_weekday or now_ct.hour != target_hour:
        return False

    last_run = cfg.get("last_run_at")
    if last_run and isinstance(last_run, datetime):
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_run) < timedelta(days=6):
            return False
    return True


async def weekly_digest_loop():
    """Background task — runs forever, checks every 30 min."""
    from rental.shared import get_db
    logging.info("🚀 Weekly digest cron started (lunes 8AM CT, checks every 30 min)")
    while True:
        try:
            db = get_db()
            if db is not None and await _should_run_now(db):
                logging.info("📊 Weekly digest cron: firing")
                result = await send_weekly_digest(db)
                logging.info(f"📊 Weekly digest done: {result}")
        except Exception as e:
            logging.exception(f"Weekly digest loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
