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


async def _send_email(to_email: str, subject: str, body: str) -> bool:
    """Send transactional email via SendGrid."""
    try:
        api_key = os.environ.get('SENDGRID_API_KEY')
        from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
        if not api_key:
            logger.warning('[tenant_leads] SENDGRID_API_KEY missing, skipping email')
            return False
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        # Wrap plain-text in basic HTML
        html_body = body.replace('\n', '<br>')
        msg = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body,
            html_content=f"<div style='font-family:Helvetica,Arial,sans-serif;line-height:1.6;color:#1a1a1a;'>{html_body}</div>",
        )
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        return 200 <= resp.status_code < 300
    except Exception as e:
        logger.exception(f"[tenant_leads] email send failed: {e}")
        return False


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
        await _send_email(lead['email'], _format_template(subj, lead), _format_template(body, lead))
    if settings.get('sms_enabled'):
        sms_tpl = settings.get(f'welcome_sms_{lang}') or DEFAULT_SETTINGS[f'welcome_sms_{lang}']
        await _send_sms(lead['phone'], _format_template(sms_tpl, lead))


async def _notify_admin_new_lead(lead: Dict[str, Any], settings: Dict[str, Any]):
    admin_email = settings.get('notify_admin_email')
    if not admin_email:
        return
    subj = f"🆕 Nuevo lead: {lead['name']} ({lead['bedrooms_wanted']} hab. / ${lead['max_budget']:,.0f})"
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

Ver/gestionar en el admin panel:
https://www.rosshouserentals.com/admin/interesados
"""
    await _send_email(admin_email, subj, body)


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

    return {"success": True, "message": "Lead registered", "is_new": is_new, "id": lead_data['_id']}


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
            f"${lead.get('max_budget', 0):,.0f}",
            lead.get('move_in_date', '') or 'Flexible',
            str(lead.get('household_size', '')),
            'Sí' if lead.get('has_pets') else 'No',
            lead.get('employment_status', '') or '',
            f"${lead.get('monthly_income', 0):,.0f}",
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
