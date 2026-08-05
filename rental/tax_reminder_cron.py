"""
Property Tax Reminder cron — emails the admin reminders to pay property taxes:
  - Dec 1  → "facturas disponibles, paga antes del 31 de enero"
  - Jan 15 → "última llamada — vence el 31 de enero"

Each email lists every property with its tax account number, estimated amount
and the exact payment links for Moore County.

Config in app_settings _id='tax_reminders' { enabled: True, last_run_at }.
Manual trigger: POST /api/admin/tax-reminders/send
"""
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException

from .shared import get_db, auth_admin

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone.utc

CHECK_INTERVAL_SECONDS = 60 * 60  # cada hora
FIRE_DATES = [(12, 1), (1, 15)]   # (mes, día)
FIRE_HOUR = 9                     # 9 AM CT

PAY_PORTAL_URL = "https://bttaxpayerportal.com/ITSPublicMO/TaxBillSearch"
ESEARCH_URL = "https://esearch.co.moore.tx.us"
ADDRESS_CHANGE_URL = "https://moorecad.org/change-of-address-request/"

router = APIRouter()
logger = logging.getLogger(__name__)


async def send_tax_reminder(db, final_call: bool = False) -> dict:
    props = await db.properties.find({}).to_list(100)
    if not props:
        return {"success": False, "error": "Sin propiedades"}

    # Live synced amounts from the county portal (property_taxes_router)
    statuses = {s.get('account_id'): s async for s in db.property_tax_status.find({})}

    rows = ""
    total_est = 0.0
    for p in props:
        acct = p.get('tax_account_id', '')
        est = float(p.get('tax_annual_estimate') or 0)
        st = statuses.get(str(acct)) if acct else None
        live_due = float(st.get('total_due') or 0) if st else 0.0
        if live_due > 0:
            total_est += live_due
            amount_html = f"<span style='color:#b91c1c;font-weight:bold'>${live_due:,.2f} VENCIDO</span>"
        else:
            total_est += est
            amount_html = f"${est:,.0f}" + (" <span style='color:#059669;font-size:11px'>(al día)</span>" if st else "")
        rows += f"""
        <tr>
          <td style="padding:8px;border:1px solid #e2e8f0;font-size:13px">{p.get('address','')}</td>
          <td style="padding:8px;border:1px solid #e2e8f0;font-size:13px;text-align:center"><b>{acct or '—'}</b></td>
          <td style="padding:8px;border:1px solid #e2e8f0;font-size:13px;text-align:right">{amount_html}</td>
        </tr>"""

    title = ("🚨 ÚLTIMA LLAMADA: impuestos de propiedad vencen el 31 de ENERO"
             if final_call else
             "📋 Recordatorio: ya están disponibles las facturas de impuestos de tus propiedades")
    urgency = ("<p style='background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:12px;color:#b91c1c;font-size:13px'>"
               "<b>Quedan ~2 semanas.</b> Después del 31 de enero: 6% de multa + 1% interés inmediato, "
               "y desde julio +20% de honorarios de abogados. Paga HOY.</p>"
               if final_call else
               "<p style='color:#475569;font-size:13px'>Fecha límite sin multas: <b>31 de enero</b>. "
               "Como tus propiedades no tienen hipoteca, nadie las paga por ti — te toca directo.</p>")

    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:620px;margin:0 auto">
      <div style="background:linear-gradient(135deg,#b45309,#d97706);padding:22px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="color:#fff;font-size:19px;margin:0">🏛️ Impuestos de Propiedad — Moore County</h1>
        <p style="color:#fef3c7;font-size:12px;margin:4px 0 0">Ross House Rentals · Recordatorio automático</p>
      </div>
      <div style="padding:24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
        <h2 style="font-size:15px;color:#0f172a;margin:0 0 10px">{title}</h2>
        {urgency}
        <table style="width:100%;border-collapse:collapse;margin:14px 0">
          <tr style="background:#f8fafc">
            <th style="padding:8px;border:1px solid #e2e8f0;font-size:11px;text-align:left">PROPIEDAD</th>
            <th style="padding:8px;border:1px solid #e2e8f0;font-size:11px">CUENTA #</th>
            <th style="padding:8px;border:1px solid #e2e8f0;font-size:11px;text-align:right">ESTIMADO/AÑO</th>
          </tr>
          {rows}
          <tr style="background:#fffbeb">
            <td colspan="2" style="padding:8px;border:1px solid #e2e8f0;font-size:13px;font-weight:bold">TOTAL estimado</td>
            <td style="padding:8px;border:1px solid #e2e8f0;font-size:14px;font-weight:bold;text-align:right;color:#b45309">${total_est:,.0f}</td>
          </tr>
        </table>
        <h3 style="font-size:13px;color:#0f172a">💳 Cómo pagar online (5 minutos):</h3>
        <ol style="font-size:13px;color:#334155;line-height:1.7;padding-left:20px">
          <li>Abre el portal: <a href="{PAY_PORTAL_URL}" style="color:#0891b2">{PAY_PORTAL_URL}</a></li>
          <li>Busca por <b>Account Number</b> (números de la tabla de arriba)</li>
          <li>Verifica el monto exacto del mes y paga con tarjeta o eCheck</li>
        </ol>
        <p style="font-size:12px;color:#64748b">
          Consultar valores/tasaciones: <a href="{ESEARCH_URL}" style="color:#0891b2">{ESEARCH_URL}</a> ·
          Cambiar dirección postal: <a href="{ADDRESS_CHANGE_URL}" style="color:#0891b2">moorecad.org/change-of-address-request</a> ·
          Tax Office: (806) 935-2175
        </p>
      </div>
    </div>
    """

    # SendGrid
    sg_key = os.getenv('SENDGRID_API_KEY', '')
    from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
    if not sg_key:
        cfg = await db.api_config.find_one({'_id': 'main'}) or {}
        sg_key = cfg.get('sendgrid_api_key', '')
        from_email = cfg.get('sendgrid_from_email', from_email)
    if not sg_key:
        return {"success": False, "error": "SendGrid no configurado"}

    company = await db.rental_config.find_one({'type': 'company'}) or {}
    sent = 0
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        subject = ("🚨 ÚLTIMA LLAMADA — Impuestos vencen 31 enero (Moore County)"
                   if final_call else
                   f"📋 Recordatorio: impuestos de propiedad ~${total_est:,.0f} — paga antes del 31 de enero")
        for rcpt in {e for e in ['yoandyross@gmail.com', company.get('email', '')] if e}:
            mail = Mail(from_email=Email(from_email, "Ross House Rentals"),
                        to_emails=To(rcpt), subject=subject,
                        html_content=Content("text/html", html))
            resp = sg.client.mail.send.post(request_body=mail.get())
            if resp.status_code in (200, 201, 202):
                sent += 1
    except Exception as e:
        logger.exception(f"[tax_reminder] send failed: {e}")
        return {"success": False, "error": str(e)[:200]}

    await db.app_settings.update_one(
        {"_id": "tax_reminders"},
        {"$set": {"last_run_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    logger.info(f"🏛️ Tax reminder sent to {sent} recipient(s) (final_call={final_call})")
    return {"success": True, "sent": sent, "total_estimate": total_est}


@router.post('/admin/tax-reminders/send')
async def manual_send(request: Request):
    """Manually trigger the property tax reminder email."""
    await auth_admin(request)
    result = await send_tax_reminder(get_db())
    if not result.get('success'):
        raise HTTPException(status_code=502, detail=result.get('error', 'Error'))
    return result


async def _should_run_now(db) -> bool:
    cfg = await db.app_settings.find_one({"_id": "tax_reminders"}) or {}
    if not cfg.get("enabled", True):
        return False
    now_ct = datetime.now(CT)
    if (now_ct.month, now_ct.day) not in FIRE_DATES or now_ct.hour != FIRE_HOUR:
        return False
    last_run = cfg.get("last_run_at")
    if last_run and isinstance(last_run, datetime):
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_run) < timedelta(days=2):
            return False
    return True


async def tax_reminder_loop():
    from rental.shared import get_db
    logging.info("🚀 Tax reminder cron started (Dec 1 & Jan 15, 9AM CT)")
    while True:
        try:
            db = get_db()
            if db is not None and await _should_run_now(db):
                now_ct = datetime.now(CT)
                await send_tax_reminder(db, final_call=(now_ct.month == 1))
        except Exception as e:
            logging.exception(f"Tax reminder loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
