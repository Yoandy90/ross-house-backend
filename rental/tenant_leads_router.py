"""
Tenant Leads / Waitlist Router
--------------------------------
Public-facing waitlist for prospective tenants in Dumas, TX.
Allows interested renters to register, get notified when properties become
available, and convert into formal applications.

Admin features:
- List, filter, search, kanban-style status board
- Manual & automatic property matching
- Bulk notifications (email + SMS, both toggleable)
- Notes & activity history per lead
- CSV export
- Conversion metrics
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
import uuid

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, validator

from .shared import get_db, auth_admin, serialize

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Pydantic Models
# ============================================================

class TenantLeadCreate(BaseModel):
    """Public form submission."""
    name: str = Field(min_length=2, max_length=100)
    email: str
    phone: str = Field(min_length=10, max_length=20)
    bedrooms_wanted: int = Field(ge=1, le=10)
    max_budget: float = Field(ge=0, le=20000)
    move_in_date: Optional[str] = None  # ISO date string
    household_size: int = Field(ge=1, le=20, default=1)
    has_pets: bool = False
    pet_details: Optional[str] = None
    current_situation: Optional[str] = None  # "renting", "own", "with_family", "homeless"
    employment_status: Optional[str] = None  # "employed", "self_employed", "student", "retired", "unemployed"
    monthly_income: Optional[float] = None
    language_pref: str = Field(default='es', pattern='^(es|en)$')
    notes: Optional[str] = None  # additional comments from user
    source: Optional[str] = 'web'
    captcha_token: Optional[str] = None  # Cloudflare Turnstile token

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


class TenantLeadUpdate(BaseModel):
    status: Optional[str] = None  # new, contacted, qualified, applied, rented, rejected
    admin_notes: Optional[str] = None
    priority: Optional[str] = None  # low, medium, high


class LeadSettings(BaseModel):
    email_enabled: bool = True
    sms_enabled: bool = True
    auto_match_enabled: bool = False
    notify_admin_email: Optional[str] = None
    welcome_email_subject_es: Optional[str] = None
    welcome_email_subject_en: Optional[str] = None
    welcome_email_body_es: Optional[str] = None
    welcome_email_body_en: Optional[str] = None
    welcome_sms_es: Optional[str] = None
    welcome_sms_en: Optional[str] = None


# ============================================================
# Helpers
# ============================================================

VALID_STATUSES = ['new', 'contacted', 'qualified', 'applied', 'rented', 'rejected']

DEFAULT_SETTINGS = {
    "email_enabled": True,
    "sms_enabled": True,
    "auto_match_enabled": False,
    "notify_admin_email": "yoandyross@gmail.com",
    "welcome_email_subject_es": "¡Bienvenido a la lista de espera — Ross House Rentals!",
    "welcome_email_subject_en": "Welcome to the Ross House Rentals waitlist!",
    "welcome_email_body_es": """Hola {name},

¡Gracias por unirte a nuestra lista de espera de Ross House Rentals! 🏡

Hemos recibido tu solicitud y estos son los detalles que registraste:
- Habitaciones deseadas: {bedrooms}
- Presupuesto máximo: ${budget}/mes
- Fecha de mudanza: {move_in}

Te notificaremos por correo y SMS apenas tengamos una propiedad disponible que coincida con tus criterios.

Mientras tanto, puedes:
• Visitar nuestras propiedades actuales: https://www.rosshouserentals.com
• Llamarnos: (806) 934-2018
• Escribirnos: info@rosshouserentals.com

¡Esperamos darte la bienvenida pronto a tu nuevo hogar!

— Equipo Ross House Rentals""",
    "welcome_email_body_en": """Hi {name},

Thank you for joining our Ross House Rentals waitlist! 🏡

We've received your request. Here's what you registered:
- Bedrooms wanted: {bedrooms}
- Max budget: ${budget}/month
- Move-in date: {move_in}

We'll notify you by email and SMS as soon as a property matching your criteria becomes available.

In the meantime:
• Browse current listings: https://www.rosshouserentals.com
• Call us: (806) 934-2018
• Email us: info@rosshouserentals.com

We look forward to welcoming you to your new home soon!

— The Ross House Rentals Team""",
    "welcome_sms_es": "Ross House: ¡Gracias {name}! Estás en nuestra lista. Te avisaremos cuando haya una casa de {bedrooms} hab. disponible.",
    "welcome_sms_en": "Ross House: Thanks {name}! You're on our waitlist. We'll alert you when a {bedrooms}-BR home is available.",
}


async def _get_settings(db) -> Dict[str, Any]:
    doc = await db.lead_settings.find_one({"_id": "config"})
    if not doc:
        await db.lead_settings.insert_one({"_id": "config", **DEFAULT_SETTINGS, "updated_at": datetime.utcnow()})
        return DEFAULT_SETTINGS
    out = {**DEFAULT_SETTINGS, **{k: v for k, v in doc.items() if k != '_id'}}
    return out


def _format_template(tpl: str, lead: Dict[str, Any]) -> str:
    """Replace placeholders {name}, {bedrooms}, {budget}, {move_in}."""
    move_in = lead.get('move_in_date') or ('Flexible' if lead.get('language_pref', 'es') == 'es' else 'Flexible')
    return tpl.format(
        name=lead.get('name', ''),
        bedrooms=lead.get('bedrooms_wanted', '-'),
        budget=f"{lead.get('max_budget', 0):,.0f}",
        move_in=move_in,
    )


async def _send_email(to_email: str, subject: str, body: str, html: Optional[str] = None) -> bool:
    """Send transactional email via SendGrid.

    Args:
        to_email: recipient
        subject: email subject
        body: plain-text body (used as fallback + when html is None)
        html: optional pre-rendered HTML content. If provided, used as-is.
              If None, plain body is wrapped in a basic HTML div.
    """
    try:
        api_key = os.environ.get('SENDGRID_API_KEY')
        from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
        if not api_key:
            logger.warning('[tenant_leads] SENDGRID_API_KEY missing, skipping email')
            return False
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        if html is None:
            # Fallback: wrap plain-text in basic HTML
            html_body = body.replace('\n', '<br>')
            html = f"<div style='font-family:Helvetica,Arial,sans-serif;line-height:1.6;color:#1a1a1a;'>{html_body}</div>"
        msg = Mail(
            from_email=(from_email, 'Ross House Rentals'),
            to_emails=to_email,
            subject=subject,
            plain_text_content=body,
            html_content=html,
        )
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        return 200 <= resp.status_code < 300
    except Exception as e:
        logger.exception(f"[tenant_leads] email send failed: {e}")
        return False


# ============================================================
# Branded email HTML template (Ross House Rentals — navy + amber)
# Matches _provider_email_templates.py style with Gmail dark-mode hardening
# ============================================================
def _render_branded_email(
    title: str,
    eyebrow: str,
    content_html: str,
    *,
    cta_label: Optional[str] = None,
    cta_url: Optional[str] = None,
    accent_color: str = "#fbbf24",
) -> str:
    """Wraps content in the official Ross House Rentals branded shell.

    title: large heading inside the card
    eyebrow: small amber label above title (e.g. 'Lista de espera · Bienvenido')
    content_html: pre-rendered HTML for the body
    cta_label/cta_url: optional CTA button
    """
    site = "https://www.rosshouserentals.com"
    phone = "(806) 934-2018"
    address = "Dumas, TX"
    name = "Ross House Rentals"

    cta_block = ""
    if cta_label and cta_url:
        cta_block = f"""
        <div style="text-align:center;margin:24px 0 8px 0;">
          <a href="{cta_url}" style="display:inline-block;background:#f59e0b;color:#ffffff;text-decoration:none;font-weight:700;padding:14px 28px;border-radius:10px;font-size:15px;box-shadow:0 4px 12px rgba(245,158,11,0.3);">
            {cta_label} &rarr;
          </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{title}</title>
<style>
  :root {{ color-scheme: light only; supported-color-schemes: light only; }}
  u + .body .gmail-dark-bg {{ background-color: #0d1a2e !important; }}
  u + .body .gmail-dark-text {{ color: #ffffff !important; }}
  u + .body .gmail-amber {{ color: {accent_color} !important; }}
  [data-ogsc] .gmail-dark-bg {{ background-color: #0d1a2e !important; }}
  [data-ogsc] .gmail-dark-text {{ color: #ffffff !important; }}
  [data-ogsc] .gmail-amber {{ color: {accent_color} !important; }}
  [data-ogsb] .gmail-dark-bg {{ background-color: #0d1a2e !important; }}
  @media only screen and (max-width: 520px) {{
    .rh-header-cell {{ padding: 22px 20px !important; }}
    .rh-header-table, .rh-header-table tbody, .rh-header-table tr, .rh-header-table td {{ display: block !important; width: 100% !important; }}
    .rh-header-brand-cell {{ padding-bottom: 14px !important; text-align: left !important; }}
    .rh-header-phone-cell {{ text-align: left !important; padding-top: 4px !important; }}
    .rh-content-cell {{ padding: 24px 20px 12px 20px !important; }}
    .rh-footer-cell {{ padding: 22px 20px !important; }}
  }}
</style>
</head>
<body class="body" style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#0f172a;">
<span style="display:none!important;opacity:0;color:transparent;height:0;width:0;overflow:hidden;">{title}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f1f5f9" style="background:#f1f5f9;padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">

      <!-- Header -->
      <tr><td bgcolor="#0d1a2e" class="gmail-dark-bg rh-header-cell" style="background-color:#0d1a2e;padding:28px 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="rh-header-table">
          <tr>
            <td class="rh-header-brand-cell" style="vertical-align:middle;">
              <div style="display:inline-block;background-color:#3b2a06;border:1px solid #b45309;padding:5px 12px;border-radius:999px;">
                <font color="{accent_color}" face="Helvetica,Arial,sans-serif"><span class="gmail-amber" style="font-size:11px;font-weight:700;color:{accent_color};letter-spacing:0.5px;text-transform:uppercase;">{eyebrow}</span></font>
              </div>
              <div style="margin-top:10px;line-height:1.2;">
                <font color="#ffffff" face="Helvetica,Arial,sans-serif"><span class="gmail-dark-text" style="font-size:22px;font-weight:800;color:#ffffff;">🏠 {name}</span></font>
              </div>
            </td>
            <td align="right" class="rh-header-phone-cell" style="vertical-align:middle;white-space:nowrap;">
              <a href="tel:+18069342018" style="text-decoration:none;background-color:#1e3050;padding:8px 12px;border-radius:999px;border:1px solid #334e74;display:inline-block;white-space:nowrap;">
                <font color="#ffffff" face="Helvetica,Arial,sans-serif"><span class="gmail-dark-text" style="color:#ffffff;font-size:13px;font-weight:600;white-space:nowrap;">📞 {phone}</span></font>
              </a>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- Content -->
      <tr><td bgcolor="#ffffff" class="rh-content-cell" style="padding:32px 32px 20px 32px;background-color:#ffffff;">
        <div style="font-size:13px;font-weight:700;color:#d97706;text-transform:uppercase;letter-spacing:1px;">{eyebrow}</div>
        <h1 style="font-size:24px;font-weight:800;color:#0f172a;margin:8px 0 16px 0;line-height:1.25;">{title}</h1>
        <div style="font-size:15px;color:#334155;line-height:1.65;">
          {content_html}
        </div>
        {cta_block}
      </td></tr>

      <!-- Footer -->
      <tr><td bgcolor="#f8fafc" class="rh-footer-cell" style="background-color:#f8fafc;padding:24px 32px;border-top:1px solid #e2e8f0;">
        <div style="font-size:12px;color:#64748b;line-height:1.6;text-align:center;">
          <strong style="color:#0f172a;">{name}</strong> · {address} · <a href="tel:+18069342018" style="color:#d97706;text-decoration:none;">{phone}</a><br>
          <a href="{site}" style="color:#d97706;text-decoration:none;">{site}</a>
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


async def _send_sms(to_phone: str, body: str) -> bool:
    """Send SMS via Twilio."""
    try:
        sid = os.environ.get('TWILIO_ACCOUNT_SID')
        token = os.environ.get('TWILIO_AUTH_TOKEN')
        from_phone = os.environ.get('TWILIO_PHONE_NUMBER') or os.environ.get('TWILIO_FROM_NUMBER')
        if not (sid and token and from_phone):
            logger.warning('[tenant_leads] Twilio creds missing, skipping SMS')
            return False
        from twilio.rest import Client
        client = Client(sid, token)
        if not to_phone.startswith('+'):
            to_phone = '+1' + re.sub(r'\D', '', to_phone)
        client.messages.create(body=body[:1500], from_=from_phone, to=to_phone)
        return True
    except Exception as e:
        logger.exception(f"[tenant_leads] sms send failed: {e}")
        return False


async def _send_welcome(lead: Dict[str, Any], settings: Dict[str, Any]):
    lang = lead.get('language_pref', 'es')
    if settings.get('email_enabled'):
        subj = settings.get(f'welcome_email_subject_{lang}') or DEFAULT_SETTINGS[f'welcome_email_subject_{lang}']
        body = settings.get(f'welcome_email_body_{lang}') or DEFAULT_SETTINGS[f'welcome_email_body_{lang}']
        subject_rendered = _format_template(subj, lead)
        body_rendered = _format_template(body, lead)

        # Build rich branded HTML
        eyebrow = "Lista de Espera · ¡Bienvenido!" if lang == 'es' else "Waitlist · Welcome!"
        title = "¡Estás en la lista!" if lang == 'es' else "You're on the list!"
        intro = (
            f"Hola <strong>{lead.get('name', '')}</strong>," if lang == 'es'
            else f"Hi <strong>{lead.get('name', '')}</strong>,"
        )
        message_html = body_rendered.replace('\n\n', '</p><p style="margin:12px 0;">').replace('\n', '<br>')

        # Lead preferences summary card
        budget = f"${lead.get('max_budget', 0):,.0f}"
        prefs_label = "Tus preferencias" if lang == 'es' else "Your preferences"
        beds_label = "habitaciones" if lang == 'es' else "bedrooms"
        budget_label = "presupuesto" if lang == 'es' else "budget"
        move_label = "fecha de mudanza" if lang == 'es' else "move-in date"
        move_value = lead.get('move_in_date') or ('Flexible' if lang == 'es' else 'Flexible')

        prefs_card = f"""
        <div style="margin:18px 0;padding:16px 18px;background:#f8fafc;border-left:4px solid #f59e0b;border-radius:10px;">
          <div style="font-size:12px;font-weight:700;color:#78350f;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">{prefs_label}</div>
          <table cellpadding="0" cellspacing="0" border="0" style="font-size:14px;color:#334155;">
            <tr><td style="padding:3px 0;color:#64748b;width:140px;">🛏️ {beds_label.capitalize()}:</td><td style="padding:3px 0;font-weight:600;color:#0f172a;">{lead.get('bedrooms_wanted', '?')}+</td></tr>
            <tr><td style="padding:3px 0;color:#64748b;">💰 {budget_label.capitalize()}:</td><td style="padding:3px 0;font-weight:600;color:#0f172a;">{budget}/mo</td></tr>
            <tr><td style="padding:3px 0;color:#64748b;">📅 {move_label.capitalize()}:</td><td style="padding:3px 0;font-weight:600;color:#0f172a;">{move_value}</td></tr>
          </table>
        </div>
        """

        content_html = f"""
          <p style="margin:0 0 12px 0;">{intro}</p>
          <p style="margin:0 0 12px 0;">{message_html}</p>
          {prefs_card}
        """

        cta_label = "Ver propiedades disponibles" if lang == 'es' else "View available properties"
        html = _render_branded_email(
            title=title,
            eyebrow=eyebrow,
            content_html=content_html,
            cta_label=cta_label,
            cta_url="https://www.rosshouserentals.com/propiedades",
        )
        await _send_email(lead['email'], subject_rendered, body_rendered, html=html)
    if settings.get('sms_enabled'):
        sms_tpl = settings.get(f'welcome_sms_{lang}') or DEFAULT_SETTINGS[f'welcome_sms_{lang}']
        await _send_sms(lead['phone'], _format_template(sms_tpl, lead))


async def _notify_admin_new_lead(lead: Dict[str, Any], settings: Dict[str, Any]):
    admin_email = settings.get('notify_admin_email')
    if not admin_email:
        return
    subj = f"🆕 Nuevo lead: {lead['name']} ({lead['bedrooms_wanted']} hab. / ${lead['max_budget']:,.0f})"

    # Plain text version (fallback for clients that block HTML)
    body = f"""Nuevo prospecto en la lista de espera de Ross House Rentals:

Nombre: {lead['name']}
Email: {lead['email']}
Teléfono: {lead['phone']}
Habitaciones: {lead['bedrooms_wanted']}
Presupuesto: ${lead['max_budget']:,.0f}/mes
Fecha mudanza: {lead.get('move_in_date') or 'Flexible'}
Personas en hogar: {lead.get('household_size', 1)}
Mascotas: {'Sí - ' + (lead.get('pet_details') or '') if lead.get('has_pets') else 'No'}
Empleo: {lead.get('employment_status') or '-'}
Ingreso mensual: ${lead.get('monthly_income', 0):,.0f}
Situación actual: {lead.get('current_situation') or '-'}
Idioma: {lead.get('language_pref', 'es').upper()}

Notas del prospecto:
{lead.get('notes') or '-'}

Ver/gestionar: https://www.rosshouserentals.com/admin/interesados
"""

    # Build the rich HTML version
    pets_label = ('Sí — ' + (lead.get('pet_details') or '(sin detalles)')) if lead.get('has_pets') else 'No'
    notes_text = lead.get('notes') or '—'

    def _row(emoji: str, label: str, value: str, highlight: bool = False) -> str:
        v_style = "color:#0f172a;font-weight:700;" if highlight else "color:#0f172a;font-weight:600;"
        return f"""
        <tr>
          <td style="padding:6px 10px 6px 0;color:#64748b;font-size:13px;white-space:nowrap;width:160px;">{emoji} {label}</td>
          <td style="padding:6px 0;font-size:14px;{v_style}">{value}</td>
        </tr>
        """

    info_table = "".join([
        _row("👤", "Nombre:", lead['name'], highlight=True),
        _row("📧", "Email:", f'<a href="mailto:{lead["email"]}" style="color:#1e40af;text-decoration:none;">{lead["email"]}</a>'),
        _row("📞", "Teléfono:", f'<a href="tel:{lead["phone"]}" style="color:#1e40af;text-decoration:none;">{lead["phone"]}</a>'),
        _row("🛏️", "Habitaciones:", f"{lead['bedrooms_wanted']}+ habitaciones"),
        _row("💰", "Presupuesto:", f"${lead['max_budget']:,.0f}/mes", highlight=True),
        _row("📅", "Mudanza:", lead.get('move_in_date') or 'Flexible'),
        _row("👨‍👩‍👧", "Hogar:", f"{lead.get('household_size', 1)} personas"),
        _row("🐾", "Mascotas:", pets_label),
        _row("💼", "Empleo:", lead.get('employment_status') or '—'),
        _row("💵", "Ingreso:", f"${lead.get('monthly_income', 0):,.0f}/mes" if lead.get('monthly_income') else '—'),
        _row("🏠", "Situación:", lead.get('current_situation') or '—'),
        _row("🌐", "Idioma:", lead.get('language_pref', 'es').upper()),
    ])

    notes_block = f"""
    <div style="margin-top:18px;padding:14px 16px;background:#fffbeb;border-left:4px solid #f59e0b;border-radius:8px;font-size:13px;color:#78350f;line-height:1.6;">
      <strong>📝 Notas del prospecto:</strong><br>
      {notes_text.replace(chr(10), '<br>') if notes_text != '—' else '<em>Sin notas adicionales</em>'}
    </div>
    """ if notes_text != '—' else ""

    content_html = f"""
      <p style="margin:0 0 14px 0;color:#334155;">
        Un nuevo prospecto se acaba de registrar en la lista de espera. Revisa los detalles abajo y contáctalo lo antes posible para maximizar la conversión.
      </p>
      <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 18px;margin-top:8px;">
        <table cellpadding="0" cellspacing="0" border="0" style="width:100%;">
          {info_table}
        </table>
      </div>
      {notes_block}
    """

    html = _render_branded_email(
        title=f"Nuevo prospecto: {lead['name']}",
        eyebrow="🆕 Lead · Lista de espera",
        content_html=content_html,
        cta_label="Ver y gestionar en el admin panel",
        cta_url="https://www.rosshouserentals.com/admin/interesados",
    )

    await _send_email(admin_email, subj, body, html=html)


def _match_lead_to_property(lead: Dict[str, Any], prop: Dict[str, Any]) -> bool:
    """Returns True if a lead's criteria matches a property."""
    try:
        if int(prop.get('bedrooms', 0)) != int(lead.get('bedrooms_wanted', -1)):
            return False
        rent = float(prop.get('monthly_rent', 0) or prop.get('rent', 0) or 0)
        if rent > float(lead.get('max_budget', 0)):
            return False
        # Optional: pet policy (skip for now)
        return True
    except Exception:
        return False


# ============================================================
# PUBLIC endpoints
# ============================================================

@router.post('/public/tenant-leads')
async def public_create_lead(request: Request, payload: TenantLeadCreate):
    # CAPTCHA gate (anti-bot)
    from .turnstile_helper import verify_turnstile_token
    await verify_turnstile_token(payload.captcha_token, request)

    db = get_db()

    # Dedup: if same email + phone exists in last 30 days, just refresh entry
    existing = await db.tenant_leads.find_one({"email": payload.email})
    settings = await _get_settings(db)

    lead_data = payload.dict()
    lead_data['created_at'] = datetime.utcnow()
    lead_data['updated_at'] = datetime.utcnow()
    lead_data['status'] = 'new'
    lead_data['priority'] = 'medium'
    lead_data['admin_notes'] = ''
    lead_data['notifications_sent'] = []
    lead_data['matched_properties'] = []
    lead_data['ip_address'] = request.client.host if request.client else ''
    lead_data['user_agent'] = request.headers.get('user-agent', '')[:500]

    if existing:
        # Update existing record
        lead_id = existing['_id']
        await db.tenant_leads.update_one(
            {"_id": lead_id},
            {"$set": {**lead_data, "created_at": existing.get('created_at', datetime.utcnow()), "status": existing.get('status', 'new'), "admin_notes": existing.get('admin_notes', '')}}
        )
        lead_data['_id'] = lead_id
        is_new = False
    else:
        lead_data['_id'] = str(uuid.uuid4())
        await db.tenant_leads.insert_one(lead_data)
        is_new = True

    # Fire-and-forget notifications
    try:
        await _send_welcome(lead_data, settings)
        if is_new:
            await _notify_admin_new_lead(lead_data, settings)
    except Exception as e:
        logger.exception(f"[tenant_leads] notification failed: {e}")

    # Auto-score lead (Phase 2 AI Brain)
    try:
        from .lead_scoring import score_and_persist
        await score_and_persist(db, lead_data['_id'])
    except Exception as e:
        logger.warning(f"[tenant_leads] auto-scoring failed: {e}")

    return {"success": True, "message": "Lead registered", "is_new": is_new, "id": lead_data['_id']}


# ============================================================
# Lead Scoring endpoints (Phase 2 AI Brain)
# ============================================================

@router.post('/admin/tenant-leads/{lead_id}/score')
async def admin_score_lead(request: Request, lead_id: str):
    """Recompute the score for a specific lead using Claude Sonnet 4.5."""
    await auth_admin(request)
    db = get_db()
    from .lead_scoring import score_and_persist
    result = await score_and_persist(db, lead_id)
    if not result:
        raise HTTPException(404, "Lead not found")
    return {"success": True, "lead_id": lead_id, **result}


@router.post('/admin/tenant-leads/score-all')
async def admin_score_all_leads(request: Request):
    """Score every lead that doesn't have a score yet (or rescore all if force=1)."""
    await auth_admin(request)
    db = get_db()
    from .lead_scoring import score_and_persist
    qs = request.query_params
    force = qs.get('force') in ('1', 'true', 'yes')
    query: Dict[str, Any] = {} if force else {"score": {"$exists": False}}
    scored = 0
    failed = 0
    cursor = db.tenant_leads.find(query).sort("created_at", -1).limit(200)
    async for lead in cursor:
        try:
            r = await score_and_persist(db, lead["_id"])
            if r:
                scored += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"[score-all] failed on {lead.get('_id')}: {e}")
            failed += 1
    return {"success": True, "scored": scored, "failed": failed, "forced": force}


@router.get('/public/tenant-leads/check')
async def public_check_lead(email: str):
    db = get_db()
    doc = await db.tenant_leads.find_one({"email": email.lower().strip()})
    return {"exists": bool(doc), "status": doc.get('status') if doc else None}


# ============================================================
# ADMIN endpoints
# ============================================================

@router.get('/admin/tenant-leads')
async def admin_list_leads(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    bedrooms: Optional[int] = None,
    min_budget: Optional[float] = None,
    max_budget: Optional[float] = None,
    limit: int = Query(default=200, le=1000),
    skip: int = 0,
):
    await auth_admin(request)
    db = get_db()
    query: Dict[str, Any] = {}
    if status and status != 'all':
        query['status'] = status
    if bedrooms:
        query['bedrooms_wanted'] = bedrooms
    if min_budget is not None:
        query.setdefault('max_budget', {})['$gte'] = min_budget
    if max_budget is not None:
        query.setdefault('max_budget', {})['$lte'] = max_budget
    if search:
        rgx = re.compile(re.escape(search), re.IGNORECASE)
        query['$or'] = [
            {'name': {'$regex': rgx}},
            {'email': {'$regex': rgx}},
            {'phone': {'$regex': rgx}},
            {'admin_notes': {'$regex': rgx}},
        ]

    total = await db.tenant_leads.count_documents(query)
    cursor = db.tenant_leads.find(query).sort([('created_at', -1)]).skip(skip).limit(limit)
    leads = [serialize(d) async for d in cursor]
    return {"success": True, "total": total, "leads": leads}


@router.get('/admin/tenant-leads/stats')
async def admin_lead_stats(request: Request):
    await auth_admin(request)
    db = get_db()
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    counts = {s: 0 for s in VALID_STATUSES}
    async for row in db.tenant_leads.aggregate(pipeline):
        counts[row['_id']] = row['count']
    total = sum(counts.values())
    converted = counts.get('rented', 0)
    return {
        "success": True,
        "total": total,
        "by_status": counts,
        "conversion_rate": (converted / total * 100) if total else 0,
    }


@router.get('/admin/tenant-leads/{lead_id}')
async def admin_get_lead(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.tenant_leads.find_one({"_id": lead_id})
    if not doc:
        raise HTTPException(404, "Lead not found")
    return {"success": True, "lead": serialize(doc)}


@router.patch('/admin/tenant-leads/{lead_id}')
async def admin_update_lead(request: Request, lead_id: str, payload: TenantLeadUpdate):
    await auth_admin(request)
    db = get_db()
    update = {"updated_at": datetime.utcnow()}
    if payload.status:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status. Use: {VALID_STATUSES}")
        update['status'] = payload.status
        if payload.status == 'contacted':
            update['last_contacted_at'] = datetime.utcnow()
    if payload.admin_notes is not None:
        update['admin_notes'] = payload.admin_notes
    if payload.priority:
        update['priority'] = payload.priority

    res = await db.tenant_leads.update_one({"_id": lead_id}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Lead not found")
    doc = await db.tenant_leads.find_one({"_id": lead_id})
    return {"success": True, "lead": serialize(doc)}


@router.delete('/admin/tenant-leads/{lead_id}')
async def admin_delete_lead(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    res = await db.tenant_leads.delete_one({"_id": lead_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Lead not found")
    return {"success": True}


@router.post('/admin/tenant-leads/{lead_id}/notify')
async def admin_notify_lead(request: Request, lead_id: str):
    """Send a custom notification to one lead."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    subject = data.get('subject', 'Ross House Rentals')
    body = data.get('body', '')
    via_email = data.get('email', True)
    via_sms = data.get('sms', True)

    lead = await db.tenant_leads.find_one({"_id": lead_id})
    if not lead:
        raise HTTPException(404, "Lead not found")

    settings = await _get_settings(db)
    email_sent = sms_sent = False
    if via_email and settings.get('email_enabled'):
        email_sent = await _send_email(lead['email'], subject, body)
    if via_sms and settings.get('sms_enabled'):
        sms_sent = await _send_sms(lead['phone'], body)

    await db.tenant_leads.update_one(
        {"_id": lead_id},
        {"$push": {"notifications_sent": {
            "type": "custom",
            "sent_at": datetime.utcnow(),
            "subject": subject,
            "email": email_sent,
            "sms": sms_sent,
        }}, "$set": {"updated_at": datetime.utcnow()}}
    )
    return {"success": True, "email_sent": email_sent, "sms_sent": sms_sent}


@router.post('/admin/tenant-leads/notify-property')
async def admin_notify_property(request: Request):
    """Notify matching leads about a newly available property."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    property_id = data.get('property_id')
    custom_message = data.get('message')
    via_email = data.get('email', True)
    via_sms = data.get('sms', True)
    target_lead_ids = data.get('lead_ids')  # optional: specific leads

    prop = await db.properties.find_one({"_id": property_id})
    if not prop:
        raise HTTPException(404, "Property not found")

    settings = await _get_settings(db)
    sent_count = 0
    sent_to = []

    # Determine target leads
    if target_lead_ids:
        leads = [lead async for lead in db.tenant_leads.find({"_id": {"$in": target_lead_ids}, "status": {"$nin": ['rented', 'rejected']}})]
    else:
        # Auto-match
        leads = [lead async for lead in db.tenant_leads.find({"status": {"$nin": ['rented', 'rejected']}})]
        leads = [lead for lead in leads if _match_lead_to_property(lead, prop)]

    for lead in leads:
        lang = lead.get('language_pref', 'es')
        addr = prop.get('address') or prop.get('name') or 'Propiedad'
        rent = prop.get('monthly_rent') or prop.get('rent') or 0
        beds = prop.get('bedrooms') or '-'

        if lang == 'es':
            subject = "🏡 ¡Tenemos una casa disponible para ti!"
            default_body = f"""Hola {lead['name']},

¡Buenas noticias! Tenemos una propiedad disponible que coincide con tu búsqueda:

🏠 {addr}
🛏️  {beds} habitaciones
💰 ${rent:,.0f}/mes

Si te interesa aplicar, llámanos al (806) 934-2018 o responde este correo. Las propiedades se rentan rápido.

— Equipo Ross House Rentals"""
            sms_body = f"Ross House: ¡Disponible! {addr} - {beds}hab - ${rent:,.0f}/mes. Llama (806) 934-2018."
        else:
            subject = "🏡 We have a home available for you!"
            default_body = f"""Hi {lead['name']},

Great news! We have a property matching your search:

🏠 {addr}
🛏️  {beds} bedrooms
💰 ${rent:,.0f}/month

If you'd like to apply, call us at (806) 934-2018 or reply to this email. Homes get rented fast.

— Ross House Rentals Team"""
            sms_body = f"Ross House: Available! {addr} - {beds}BR - ${rent:,.0f}/mo. Call (806) 934-2018."

        body = custom_message or default_body

        es = ss = False
        if via_email and settings.get('email_enabled'):
            es = await _send_email(lead['email'], subject, body)
        if via_sms and settings.get('sms_enabled'):
            ss = await _send_sms(lead['phone'], sms_body)

        await db.tenant_leads.update_one(
            {"_id": lead['_id']},
            {"$push": {"notifications_sent": {
                "type": "property_match",
                "property_id": property_id,
                "sent_at": datetime.utcnow(),
                "email": es,
                "sms": ss,
            }}, "$addToSet": {"matched_properties": property_id}, "$set": {"updated_at": datetime.utcnow()}}
        )
        if es or ss:
            sent_count += 1
            sent_to.append(lead['_id'])

    return {"success": True, "total_matched": len(leads), "sent": sent_count, "sent_to": sent_to}


@router.get('/admin/tenant-leads/match/{property_id}')
async def admin_match_property(request: Request, property_id: str):
    """Return list of leads matching a given property."""
    await auth_admin(request)
    db = get_db()
    prop = await db.properties.find_one({"_id": property_id})
    if not prop:
        raise HTTPException(404, "Property not found")
    leads = [lead async for lead in db.tenant_leads.find({"status": {"$nin": ['rented', 'rejected']}})]
    matched = [serialize(lead) for lead in leads if _match_lead_to_property(lead, prop)]
    return {"success": True, "property": serialize(prop), "matched_leads": matched, "count": len(matched)}


@router.get('/admin/tenant-leads/export/csv')
async def admin_export_csv(request: Request):
    await auth_admin(request)
    from fastapi.responses import Response
    db = get_db()
    leads = [d async for d in db.tenant_leads.find({}).sort([('created_at', -1)])]
    headers = ['Nombre', 'Email', 'Teléfono', 'Hab.', 'Presupuesto', 'Mudanza', 'Personas', 'Mascotas', 'Empleo', 'Ingreso', 'Idioma', 'Estado', 'Prioridad', 'Notas', 'Creado']
    rows = [headers]
    for lead in leads:
        rows.append([
            lead.get('name', ''),
            lead.get('email', ''),
            lead.get('phone', ''),
            str(lead.get('bedrooms_wanted', '')),
            f"${(lead.get('max_budget') or 0):,.0f}",
            lead.get('move_in_date', '') or 'Flexible',
            str(lead.get('household_size', '')),
            'Sí' if lead.get('has_pets') else 'No',
            lead.get('employment_status', '') or '',
            f"${(lead.get('monthly_income') or 0):,.0f}",
            lead.get('language_pref', ''),
            lead.get('status', ''),
            lead.get('priority', ''),
            (lead.get('admin_notes') or '').replace('\n', ' '),
            lead.get('created_at').isoformat() if lead.get('created_at') else '',
        ])
    csv = '\n'.join([','.join([f'"{c}"' for c in r]) for r in rows])
    return Response(content=csv, media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="tenant-leads-{datetime.utcnow():%Y%m%d}.csv"'
    })


# ============================================================
# Settings
# ============================================================

@router.get('/admin/lead-settings')
async def get_lead_settings(request: Request):
    await auth_admin(request)
    db = get_db()
    return {"success": True, "settings": await _get_settings(db)}


@router.put('/admin/lead-settings')
async def update_lead_settings(request: Request, payload: LeadSettings):
    await auth_admin(request)
    db = get_db()
    update = {k: v for k, v in payload.dict().items() if v is not None}
    update['updated_at'] = datetime.utcnow()
    await db.lead_settings.update_one({"_id": "config"}, {"$set": update}, upsert=True)
    return {"success": True, "settings": await _get_settings(db)}
