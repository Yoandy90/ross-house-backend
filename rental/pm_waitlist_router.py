"""
Property Management Service Waitlist Router
============================================
Public waitlist for future 3rd-party Property Management service
(pending Texas Real Estate Broker License — expected Q4 2026 / 2027).

Legally, Ross House Rentals LLC currently operates as an
owner-operator of its own rental properties. Managing 3rd-party
properties requires a Texas Real Estate Broker License.

This endpoint captures interested property owners who want to be
notified when the service becomes available.
"""

import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, validator

from .shared import get_db, auth_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Models ────────────────────────────────────────────────────────────────


class PmWaitlistCreate(BaseModel):
    """Public submission for the property management waitlist."""
    name: str = Field(min_length=2, max_length=100)
    email: str
    phone: str = Field(min_length=10, max_length=20)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default='TX', max_length=2)
    property_count: int = Field(ge=1, le=1000, default=1)
    property_types: Optional[List[str]] = None
    current_situation: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    language_pref: str = Field(default='es', pattern='^(es|en)$')
    captcha_token: Optional[str] = None

    @validator('email')
    def validate_email(cls, v):
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError('Invalid email')
        return v.lower().strip()

    @validator('phone')
    def validate_phone(cls, v):
        digits = re.sub(r'\D', '', v)
        if len(digits) < 10:
            raise ValueError('Phone must have at least 10 digits')
        return digits


# ─── Email helper (reuses SendGrid via env vars) ──────────────────────────


async def _send_email(to_email: str, subject: str, html: str) -> bool:
    try:
        api_key = os.environ.get('SENDGRID_API_KEY')
        from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
        if not api_key:
            logger.warning('[pm_waitlist] SENDGRID_API_KEY missing, skipping email')
            return False
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        msg = Mail(
            from_email=(from_email, 'Ross House Rentals'),
            to_emails=to_email,
            subject=subject,
            html_content=html,
        )
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        return 200 <= resp.status_code < 300
    except Exception as e:
        logger.exception(f"[pm_waitlist] email send failed: {e}")
        return False


async def _notify_admin_new_pm_lead(lead: dict):
    admin_email = os.getenv('ADMIN_NOTIFY_EMAIL') or 'yoandyross@gmail.com'
    subj = f"[PM Waitlist] Nuevo interesado: {lead.get('name')} · {lead.get('property_count')} propiedad(es)"
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;">
      <h2 style="color:#0f172a;">📋 Nuevo registro en waitlist de Property Management</h2>
      <table style="width:100%;border-collapse:collapse;margin-top:16px;">
        <tr><td style="padding:6px 0;color:#64748b;">Nombre:</td><td style="padding:6px 0;color:#0f172a;font-weight:600;">{lead.get('name')}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;">Email:</td><td style="padding:6px 0;color:#0f172a;">{lead.get('email')}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;">Teléfono:</td><td style="padding:6px 0;color:#0f172a;">{lead.get('phone')}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;">Ciudad:</td><td style="padding:6px 0;color:#0f172a;">{lead.get('city') or '—'}, {lead.get('state') or '—'}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;">Propiedades:</td><td style="padding:6px 0;color:#0f172a;font-weight:600;">{lead.get('property_count')}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;">Tipos:</td><td style="padding:6px 0;color:#0f172a;">{', '.join(lead.get('property_types') or []) or '—'}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;">Situación:</td><td style="padding:6px 0;color:#0f172a;">{lead.get('current_situation') or '—'}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b;vertical-align:top;">Notas:</td><td style="padding:6px 0;color:#0f172a;">{lead.get('notes') or '—'}</td></tr>
      </table>
      <p style="color:#94a3b8;font-size:11px;margin-top:20px;">Recibido: {lead.get('created_at')} · Idioma: {lead.get('language_pref')}</p>
    </div>
    """
    await _send_email(admin_email, subj, html)


async def _send_pm_welcome(lead: dict):
    is_es = lead.get('language_pref') == 'es'
    if is_es:
        subj = "✅ Ross House Rentals · Registrado en waitlist de Administración de Propiedades"
        html = f"""
        <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;background:#f8fafc;padding:32px;border-radius:16px;">
          <h2 style="color:#0f172a;margin:0 0 16px;">¡Gracias, {lead.get('name')}!</h2>
          <p style="color:#475569;font-size:15px;line-height:1.6;">
            Hemos recibido tu interés en nuestro futuro servicio de
            <strong>Administración de Propiedades para inversionistas</strong>.
          </p>
          <div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:14px 18px;border-radius:8px;margin:20px 0;">
            <p style="margin:0;color:#92400e;font-size:14px;"><strong>📅 Lanzamiento estimado:</strong> Q4 2026 – 2027</p>
            <p style="margin:8px 0 0;color:#92400e;font-size:13px;">Estamos tramitando la <strong>Real Estate Broker License</strong> de Texas para ofrecer este servicio de forma legal y profesional.</p>
          </div>
          <p style="color:#475569;font-size:14px;line-height:1.6;">
            Cuando el servicio esté disponible, serás una de las primeras personas en enterarse.
            Mientras tanto, escríbenos a
            <a href="mailto:info@rosshouserentals.com">info@rosshouserentals.com</a>
            o llama al <a href="tel:+18069342018">(806) 934-2018</a>.
          </p>
          <p style="color:#64748b;font-size:13px;margin-top:30px;">— El equipo de Ross House Rentals LLC</p>
          <p style="color:#94a3b8;font-size:11px;margin-top:20px;border-top:1px solid #e2e8f0;padding-top:12px;">
            Ross House Rentals LLC es actualmente propietario-operador de sus propias propiedades de alquiler.
            Los servicios de administración a terceros estarán disponibles próximamente sujetos a licencia.
          </p>
        </div>
        """
    else:
        subj = "✅ Ross House Rentals · You're on the Property Management waitlist"
        html = f"""
        <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;background:#f8fafc;padding:32px;border-radius:16px;">
          <h2 style="color:#0f172a;margin:0 0 16px;">Thank you, {lead.get('name')}!</h2>
          <p style="color:#475569;font-size:15px;line-height:1.6;">
            We've received your interest in our upcoming
            <strong>Property Management service for investors</strong>.
          </p>
          <div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:14px 18px;border-radius:8px;margin:20px 0;">
            <p style="margin:0;color:#92400e;font-size:14px;"><strong>📅 Estimated launch:</strong> Q4 2026 – 2027</p>
            <p style="margin:8px 0 0;color:#92400e;font-size:13px;">We're currently obtaining our <strong>Texas Real Estate Broker License</strong> to offer this service legally and professionally.</p>
          </div>
          <p style="color:#475569;font-size:14px;line-height:1.6;">
            When the service launches, you'll be among the first to know.
            In the meantime, reach us at
            <a href="mailto:info@rosshouserentals.com">info@rosshouserentals.com</a>
            or call <a href="tel:+18069342018">(806) 934-2018</a>.
          </p>
          <p style="color:#64748b;font-size:13px;margin-top:30px;">— The Ross House Rentals LLC Team</p>
          <p style="color:#94a3b8;font-size:11px;margin-top:20px;border-top:1px solid #e2e8f0;padding-top:12px;">
            Ross House Rentals LLC is currently an owner-operator of its own rental properties.
            Third-party property management services will be available soon pending licensing.
          </p>
        </div>
        """
    await _send_email(lead.get('email'), subj, html)


# ─── Public endpoint ──────────────────────────────────────────────────────


@router.post('/public/pm-service-waitlist')
async def public_create_pm_waitlist(request: Request, payload: PmWaitlistCreate):
    """Public waitlist submission for the upcoming Property Management service."""
    # CAPTCHA gate
    from .turnstile_helper import verify_turnstile_token
    await verify_turnstile_token(payload.captcha_token, request)

    db = get_db()

    existing = await db.pm_service_waitlist.find_one({"email": payload.email})

    lead_data = payload.dict()
    lead_data['created_at'] = datetime.utcnow()
    lead_data['updated_at'] = datetime.utcnow()
    lead_data['status'] = 'new'
    lead_data['ip_address'] = request.client.host if request.client else ''
    lead_data['user_agent'] = request.headers.get('user-agent', '')[:500]

    if existing:
        lead_id = existing['_id']
        await db.pm_service_waitlist.update_one(
            {"_id": lead_id},
            {"$set": {**lead_data, "created_at": existing.get('created_at', datetime.utcnow()), "status": existing.get('status', 'new')}}
        )
        lead_data['_id'] = lead_id
        is_new = False
    else:
        lead_data['_id'] = str(uuid.uuid4())
        await db.pm_service_waitlist.insert_one(lead_data)
        is_new = True

    try:
        await _send_pm_welcome(lead_data)
        if is_new:
            await _notify_admin_new_pm_lead(lead_data)
    except Exception as e:
        logger.exception(f"[pm_waitlist] notification failed: {e}")

    return {
        "success": True,
        "message": "Registered in PM service waitlist",
        "is_new": is_new,
        "id": lead_data['_id'],
    }


# ─── Admin endpoints ──────────────────────────────────────────────────────


@router.get('/admin/pm-service-waitlist')
async def admin_list_pm_waitlist(request: Request, limit: int = 100):
    """List PM waitlist signups (admin only)."""
    await auth_admin(request)
    db = get_db()
    cur = db.pm_service_waitlist.find({}).sort('created_at', -1).limit(limit)
    items = []
    async for doc in cur:
        d = dict(doc)
        d['id'] = d.pop('_id', None)
        items.append(d)
    total = await db.pm_service_waitlist.count_documents({})
    return {"items": items, "total": total}


@router.get('/admin/pm-service-waitlist/stats')
async def admin_pm_waitlist_stats(request: Request):
    """Aggregate stats for PM waitlist."""
    await auth_admin(request)
    db = get_db()
    total = await db.pm_service_waitlist.count_documents({})
    new_count = await db.pm_service_waitlist.count_documents({"status": "new"})
    pipeline = [{"$group": {"_id": None, "total_props": {"$sum": "$property_count"}}}]
    agg_cur = db.pm_service_waitlist.aggregate(pipeline)
    agg = await agg_cur.to_list(1)
    total_props = agg[0]['total_props'] if agg else 0
    return {"total": total, "new": new_count, "total_properties_interested": total_props}
