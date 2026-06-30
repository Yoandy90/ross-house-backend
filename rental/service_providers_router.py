"""
Service Providers / Vendors Directory Router
---------------------------------------------
Captures plumbers, electricians, HVAC techs, masons, painters, etc. who want
to receive maintenance calls from Ross House Rentals.

Public:
- Self-registration form (`POST /public/service-providers`)

Admin:
- Searchable directory (by service type, area, rating)
- Detail view with notes & job history
- Dispatch jobs via SMS / Email
- Rating system after each job
- CSV export
- Settings: toggle email / SMS, edit templates
"""

import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, validator

from .shared import get_db, auth_admin, serialize

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Constants
# ============================================================

VALID_SERVICES = [
    'plumber', 'electrician', 'hvac', 'mason', 'painter',
    'gardener', 'cleaner', 'locksmith', 'roofer', 'appliance_repair',
    'pest_control', 'handyman', 'flooring', 'drywall', 'tile',
    'concrete', 'fence', 'pool', 'security', 'other',
]

VALID_STATUSES = ['active', 'paused', 'blacklisted', 'pending_review']


# ============================================================
# Models
# ============================================================

class ServiceProviderCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    company_name: Optional[str] = None
    email: str
    phone: str = Field(min_length=10, max_length=20)
    services: List[str] = Field(min_items=1, max_items=10)
    service_areas: Optional[List[str]] = None  # ZIPs e.g. ["79029", "79045"]
    hourly_rate: Optional[float] = None
    project_rate_min: Optional[float] = None
    project_rate_max: Optional[float] = None
    has_insurance: bool = False
    insurance_provider: Optional[str] = None
    license_number: Optional[str] = None
    years_experience: Optional[int] = Field(default=None, ge=0, le=80)
    languages: List[str] = Field(default_factory=lambda: ['en'])
    bio: Optional[str] = None
    references_text: Optional[str] = None
    website: Optional[str] = None
    work_photos: Optional[List[str]] = None  # base64 or URLs
    language_pref: str = Field(default='es', pattern='^(es|en)$')
    source: Optional[str] = 'web'

    @validator('email')
    def _email(cls, v):
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError('Invalid email')
        return v.lower().strip()

    @validator('phone')
    def _phone(cls, v):
        digits = re.sub(r'\D', '', v)
        if len(digits) < 10:
            raise ValueError('Phone must have at least 10 digits')
        return digits

    @validator('services', each_item=True)
    def _service(cls, v):
        if v not in VALID_SERVICES:
            raise ValueError(f'Invalid service: {v}')
        return v


class ServiceProviderUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    is_featured: Optional[bool] = None
    services: Optional[List[str]] = None
    name: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    hourly_rate: Optional[float] = None


class ProviderSettings(BaseModel):
    email_enabled: bool = True
    sms_enabled: bool = True
    notify_admin_email: Optional[str] = None
    welcome_email_subject_es: Optional[str] = None
    welcome_email_subject_en: Optional[str] = None
    welcome_email_body_es: Optional[str] = None
    welcome_email_body_en: Optional[str] = None
    welcome_sms_es: Optional[str] = None
    welcome_sms_en: Optional[str] = None


class DispatchRequest(BaseModel):
    provider_id: str
    subject: Optional[str] = None
    message: str = Field(min_length=5)
    via_email: bool = True
    via_sms: bool = True
    job_id: Optional[str] = None


class RatingPayload(BaseModel):
    rating: float = Field(ge=0, le=5)
    comment: Optional[str] = None
    job_id: Optional[str] = None


# ============================================================
# Defaults
# ============================================================

DEFAULT_PROVIDER_SETTINGS = {
    "email_enabled": True,
    "sms_enabled": True,
    "notify_admin_email": "yoandyross@gmail.com",
    "welcome_email_subject_es": "Bienvenido a la red de proveedores de Ross House Rentals 🛠️",
    "welcome_email_subject_en": "Welcome to the Ross House Rentals provider network 🛠️",
    "welcome_email_body_es": """Hola {name},

¡Gracias por unirte a nuestra red de proveedores de servicios en Dumas, TX! 🛠️

Tu registro fue recibido con los siguientes servicios:
{services}

Te contactaremos por email o SMS cuando tengamos trabajos de mantenimiento que coincidan con tus servicios y zona.

Mientras tanto:
• Sitio web: https://www.rosshouserentals.com
• Teléfono: (806) 934-2018

¡Esperamos trabajar contigo pronto!

— Equipo Ross House Rentals""",
    "welcome_email_body_en": """Hi {name},

Thanks for joining our service provider network in Dumas, TX! 🛠️

Your registration was received with the following services:
{services}

We'll contact you by email or SMS when we have maintenance work matching your services and area.

In the meantime:
• Website: https://www.rosshouserentals.com
• Phone: (806) 934-2018

We look forward to working with you soon!

— Ross House Rentals Team""",
    "welcome_sms_es": "Ross House: ¡Gracias {name}! Estás en nuestra red de proveedores. Te llamaremos cuando te necesitemos.",
    "welcome_sms_en": "Ross House: Thanks {name}! You're in our provider network. We'll call when we need you.",
}


async def _get_settings(db) -> Dict[str, Any]:
    doc = await db.provider_settings.find_one({"_id": "config"})
    if not doc:
        await db.provider_settings.insert_one({"_id": "config", **DEFAULT_PROVIDER_SETTINGS, "updated_at": datetime.utcnow()})
        return DEFAULT_PROVIDER_SETTINGS
    return {**DEFAULT_PROVIDER_SETTINGS, **{k: v for k, v in doc.items() if k != '_id'}}


SERVICE_LABELS_ES = {
    'plumber': 'Plomero', 'electrician': 'Electricista', 'hvac': 'HVAC / Aire',
    'mason': 'Albañil', 'painter': 'Pintor', 'gardener': 'Jardinero',
    'cleaner': 'Limpieza', 'locksmith': 'Cerrajero', 'roofer': 'Techos',
    'appliance_repair': 'Electrodomésticos', 'pest_control': 'Control de plagas',
    'handyman': 'Mantenimiento general', 'flooring': 'Pisos', 'drywall': 'Tablaroca',
    'tile': 'Azulejos', 'concrete': 'Concreto', 'fence': 'Cercas',
    'pool': 'Piscinas', 'security': 'Seguridad', 'other': 'Otro',
}
SERVICE_LABELS_EN = {
    'plumber': 'Plumber', 'electrician': 'Electrician', 'hvac': 'HVAC',
    'mason': 'Mason', 'painter': 'Painter', 'gardener': 'Gardener',
    'cleaner': 'Cleaning', 'locksmith': 'Locksmith', 'roofer': 'Roofer',
    'appliance_repair': 'Appliance repair', 'pest_control': 'Pest control',
    'handyman': 'Handyman', 'flooring': 'Flooring', 'drywall': 'Drywall',
    'tile': 'Tile', 'concrete': 'Concrete', 'fence': 'Fencing',
    'pool': 'Pools', 'security': 'Security', 'other': 'Other',
}


def _format_template(tpl: str, provider: Dict[str, Any]) -> str:
    lang = provider.get('language_pref', 'es')
    labels = SERVICE_LABELS_ES if lang == 'es' else SERVICE_LABELS_EN
    services_str = ', '.join([labels.get(s, s) for s in (provider.get('services') or [])])
    return tpl.format(
        name=provider.get('name', ''),
        services=services_str or '-',
        company=provider.get('company_name', '') or '',
        phone=provider.get('phone', ''),
        email=provider.get('email', ''),
    )


# ============================================================
# Email / SMS helpers (reused pattern)
# ============================================================

async def _send_email(to_email: str, subject: str, body: str) -> bool:
    try:
        api_key = os.environ.get('SENDGRID_API_KEY')
        from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
        if not api_key:
            return False
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        html_body = body.replace('\n', '<br>')
        msg = Mail(
            from_email=from_email, to_emails=to_email, subject=subject,
            plain_text_content=body,
            html_content=f"<div style='font-family:Helvetica,Arial,sans-serif;line-height:1.6;color:#1a1a1a;'>{html_body}</div>",
        )
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        return 200 <= resp.status_code < 300
    except Exception as e:
        logger.exception(f"[providers] email send failed: {e}")
        return False


async def _send_sms(to_phone: str, body: str) -> bool:
    try:
        sid = os.environ.get('TWILIO_ACCOUNT_SID')
        token = os.environ.get('TWILIO_AUTH_TOKEN')
        from_phone = os.environ.get('TWILIO_PHONE_NUMBER') or os.environ.get('TWILIO_FROM_NUMBER')
        if not (sid and token and from_phone):
            return False
        from twilio.rest import Client
        if not to_phone.startswith('+'):
            to_phone = '+1' + re.sub(r'\D', '', to_phone)
        Client(sid, token).messages.create(body=body[:1500], from_=from_phone, to=to_phone)
        return True
    except Exception as e:
        logger.exception(f"[providers] sms send failed: {e}")
        return False


# ============================================================
# PUBLIC endpoints
# ============================================================

@router.post('/public/service-providers')
async def public_register_provider(request: Request, payload: ServiceProviderCreate):
    db = get_db()
    existing = await db.service_providers.find_one({"email": payload.email})
    settings = await _get_settings(db)

    data = payload.dict()
    data['created_at'] = datetime.utcnow()
    data['updated_at'] = datetime.utcnow()
    data['status'] = 'pending_review'
    data['rating'] = 0.0
    data['total_jobs'] = 0
    data['completed_jobs'] = 0
    data['ratings_history'] = []
    data['dispatch_history'] = []
    data['admin_notes'] = ''
    data['is_featured'] = False
    data['ip_address'] = request.client.host if request.client else ''
    data['user_agent'] = request.headers.get('user-agent', '')[:500]

    if existing:
        provider_id = existing['_id']
        await db.service_providers.update_one(
            {"_id": provider_id},
            {"$set": {**data,
                      "created_at": existing.get('created_at', datetime.utcnow()),
                      "status": existing.get('status', 'pending_review'),
                      "rating": existing.get('rating', 0),
                      "total_jobs": existing.get('total_jobs', 0),
                      "admin_notes": existing.get('admin_notes', '')}}
        )
        data['_id'] = provider_id
        is_new = False
    else:
        data['_id'] = str(uuid.uuid4())
        await db.service_providers.insert_one(data)
        is_new = True

    # Welcome notifications
    lang = data.get('language_pref', 'es')
    try:
        if settings.get('email_enabled'):
            subj_key = f'welcome_email_subject_{lang}'
            body_key = f'welcome_email_body_{lang}'
            subj = settings.get(subj_key) or DEFAULT_PROVIDER_SETTINGS[subj_key]
            body = settings.get(body_key) or DEFAULT_PROVIDER_SETTINGS[body_key]
            await _send_email(data['email'], _format_template(subj, data), _format_template(body, data))
        if settings.get('sms_enabled'):
            sms_key = f'welcome_sms_{lang}'
            sms_tpl = settings.get(sms_key) or DEFAULT_PROVIDER_SETTINGS[sms_key]
            await _send_sms(data['phone'], _format_template(sms_tpl, data))
        # Admin alert
        admin_email = settings.get('notify_admin_email')
        if is_new and admin_email:
            labels = SERVICE_LABELS_ES
            services_str = ', '.join([labels.get(s, s) for s in (data.get('services') or [])])
            subj_admin = f"🛠️ Nuevo proveedor: {data['name']} — {services_str}"
            body_admin = f"""Nuevo proveedor registrado en Ross House Rentals:

Nombre: {data['name']}
Empresa: {data.get('company_name') or '-'}
Email: {data['email']}
Teléfono: {data['phone']}
Servicios: {services_str}
Zonas: {', '.join(data.get('service_areas') or []) or '-'}
Tarifa por hora: ${data.get('hourly_rate') or '-'}
Años de experiencia: {data.get('years_experience') or '-'}
Seguro: {'Sí' if data.get('has_insurance') else 'No'}
Licencia: {data.get('license_number') or '-'}
Idiomas: {', '.join(data.get('languages') or [])}
Web: {data.get('website') or '-'}

Bio:
{data.get('bio') or '-'}

Referencias:
{data.get('references_text') or '-'}

Ver/aprobar en admin:
https://www.rosshouserentals.com/admin/proveedores
"""
            await _send_email(admin_email, subj_admin, body_admin)
    except Exception as e:
        logger.exception(f"[providers] notification failed: {e}")

    return {"success": True, "is_new": is_new, "id": data['_id'], "message": "Provider registered"}


@router.get('/public/service-providers/check')
async def public_check_provider(email: str):
    db = get_db()
    doc = await db.service_providers.find_one({"email": email.lower().strip()})
    return {"exists": bool(doc), "status": doc.get('status') if doc else None}


@router.get('/public/service-providers/services')
async def public_get_services():
    """Returns the canonical service list for the registration form."""
    return {
        "success": True,
        "services": [{"id": k, "es": SERVICE_LABELS_ES[k], "en": SERVICE_LABELS_EN[k]} for k in VALID_SERVICES],
    }


# ============================================================
# ADMIN endpoints
# ============================================================

@router.get('/admin/service-providers/stats')
async def admin_provider_stats(request: Request):
    await auth_admin(request)
    db = get_db()
    by_status: Dict[str, int] = {s: 0 for s in VALID_STATUSES}
    async for row in db.service_providers.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]):
        by_status[row['_id']] = row['count']
    by_service: Dict[str, int] = {}
    async for row in db.service_providers.aggregate([
        {"$unwind": "$services"},
        {"$group": {"_id": "$services", "count": {"$sum": 1}}},
    ]):
        by_service[row['_id']] = row['count']
    total = sum(by_status.values())
    return {"success": True, "total": total, "by_status": by_status, "by_service": by_service}


@router.get('/admin/service-providers')
async def admin_list_providers(
    request: Request,
    status: Optional[str] = None,
    service: Optional[str] = None,
    area: Optional[str] = None,
    search: Optional[str] = None,
    min_rating: Optional[float] = None,
    limit: int = Query(default=200, le=1000),
    skip: int = 0,
):
    await auth_admin(request)
    db = get_db()
    q: Dict[str, Any] = {}
    if status and status != 'all':
        q['status'] = status
    if service:
        q['services'] = service
    if area:
        q['service_areas'] = area
    if min_rating is not None:
        q['rating'] = {'$gte': min_rating}
    if search:
        rgx = re.compile(re.escape(search), re.IGNORECASE)
        q['$or'] = [
            {'name': {'$regex': rgx}}, {'email': {'$regex': rgx}},
            {'phone': {'$regex': rgx}}, {'company_name': {'$regex': rgx}},
            {'bio': {'$regex': rgx}}, {'admin_notes': {'$regex': rgx}},
        ]
    total = await db.service_providers.count_documents(q)
    cursor = db.service_providers.find(q).sort([("is_featured", -1), ("rating", -1), ("created_at", -1)]).skip(skip).limit(limit)
    items = [serialize(d) async for d in cursor]
    return {"success": True, "total": total, "providers": items}


@router.get('/admin/service-providers/export/csv')
async def admin_export_csv(request: Request):
    await auth_admin(request)
    db = get_db()
    rows = [['Nombre', 'Empresa', 'Email', 'Teléfono', 'Servicios', 'Zonas', 'Tarifa/hr', 'Experiencia', 'Seguro', 'Licencia', 'Idiomas', 'Rating', 'Estado', 'Notas', 'Creado']]
    async for p in db.service_providers.find({}).sort([('created_at', -1)]):
        rows.append([
            p.get('name', ''),
            p.get('company_name') or '',
            p.get('email', ''),
            p.get('phone', ''),
            ', '.join([SERVICE_LABELS_ES.get(s, s) for s in (p.get('services') or [])]),
            ', '.join(p.get('service_areas') or []),
            f"${(p.get('hourly_rate') or 0):,.0f}",
            str(p.get('years_experience') or ''),
            'Sí' if p.get('has_insurance') else 'No',
            p.get('license_number') or '',
            ', '.join(p.get('languages') or []),
            f"{p.get('rating', 0):.1f}",
            p.get('status', ''),
            (p.get('admin_notes') or '').replace('\n', ' '),
            p.get('created_at').isoformat() if p.get('created_at') else '',
        ])
    csv = '\n'.join([','.join([f'"{c}"' for c in r]) for r in rows])
    return Response(content=csv, media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="service-providers-{datetime.utcnow():%Y%m%d}.csv"'
    })


@router.get('/admin/service-providers/{provider_id}')
async def admin_get_provider(request: Request, provider_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.service_providers.find_one({"_id": provider_id})
    if not doc:
        raise HTTPException(404, "Provider not found")
    return {"success": True, "provider": serialize(doc)}


@router.patch('/admin/service-providers/{provider_id}')
async def admin_update_provider(request: Request, provider_id: str, payload: ServiceProviderUpdate):
    await auth_admin(request)
    db = get_db()
    update = {"updated_at": datetime.utcnow()}
    if payload.status:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status. Use: {VALID_STATUSES}")
        update['status'] = payload.status
    for f in ['admin_notes', 'rating', 'is_featured', 'services', 'name', 'company_name', 'email', 'phone', 'hourly_rate']:
        v = getattr(payload, f)
        if v is not None:
            update[f] = v
    res = await db.service_providers.update_one({"_id": provider_id}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Provider not found")
    return {"success": True, "provider": serialize(await db.service_providers.find_one({"_id": provider_id}))}


@router.delete('/admin/service-providers/{provider_id}')
async def admin_delete_provider(request: Request, provider_id: str):
    await auth_admin(request)
    db = get_db()
    res = await db.service_providers.delete_one({"_id": provider_id})
    if res.deleted_count == 0:
        raise HTTPException(404, "Provider not found")
    return {"success": True}


@router.post('/admin/service-providers/dispatch')
async def admin_dispatch_job(request: Request, payload: DispatchRequest):
    """Send a maintenance job to a provider (email + SMS)."""
    await auth_admin(request)
    db = get_db()
    p = await db.service_providers.find_one({"_id": payload.provider_id})
    if not p:
        raise HTTPException(404, "Provider not found")
    settings = await _get_settings(db)
    subj = payload.subject or "Ross House Rentals — Solicitud de trabajo"
    email_sent = sms_sent = False
    if payload.via_email and settings.get('email_enabled'):
        email_sent = await _send_email(p['email'], subj, payload.message)
    if payload.via_sms and settings.get('sms_enabled'):
        sms_sent = await _send_sms(p['phone'], payload.message)
    entry = {
        "type": "dispatch",
        "sent_at": datetime.utcnow(),
        "subject": subj,
        "message": payload.message,
        "job_id": payload.job_id,
        "email": email_sent,
        "sms": sms_sent,
    }
    await db.service_providers.update_one(
        {"_id": payload.provider_id},
        {"$push": {"dispatch_history": entry}, "$inc": {"total_jobs": 1}, "$set": {"updated_at": datetime.utcnow()}}
    )
    return {"success": True, "email_sent": email_sent, "sms_sent": sms_sent}


@router.post('/admin/service-providers/{provider_id}/rate')
async def admin_rate_provider(request: Request, provider_id: str, payload: RatingPayload):
    """Add a rating after a completed job. Recomputes the average."""
    await auth_admin(request)
    db = get_db()
    p = await db.service_providers.find_one({"_id": provider_id})
    if not p:
        raise HTTPException(404, "Provider not found")
    history = list(p.get('ratings_history') or [])
    history.append({
        "rating": payload.rating, "comment": payload.comment or "",
        "job_id": payload.job_id, "rated_at": datetime.utcnow(),
    })
    avg = sum(r['rating'] for r in history) / len(history)
    await db.service_providers.update_one(
        {"_id": provider_id},
        {"$set": {"ratings_history": history, "rating": round(avg, 2), "updated_at": datetime.utcnow()},
         "$inc": {"completed_jobs": 1}}
    )
    return {"success": True, "average_rating": round(avg, 2), "total_ratings": len(history)}


# Maps maintenance categories from tenant_router → service_provider services
MAINT_CATEGORY_TO_SERVICE = {
    'plumbing': ['plumber', 'handyman'],
    'plomeria': ['plumber', 'handyman'],
    'electrical': ['electrician', 'handyman'],
    'electrica': ['electrician', 'handyman'],
    'electricidad': ['electrician', 'handyman'],
    'hvac': ['hvac'],
    'aire_acondicionado': ['hvac'],
    'aire': ['hvac'],
    'appliance': ['appliance_repair', 'handyman'],
    'electrodomesticos': ['appliance_repair', 'handyman'],
    'general': ['handyman'],
    'structural': ['mason', 'handyman'],
    'estructural': ['mason', 'handyman'],
    'painting': ['painter'],
    'pintura': ['painter'],
    'roof': ['roofer'],
    'techo': ['roofer'],
    'pest': ['pest_control'],
    'plagas': ['pest_control'],
    'lock': ['locksmith'],
    'cerradura': ['locksmith'],
    'flooring': ['flooring', 'tile'],
    'piso': ['flooring', 'tile'],
    'fence': ['fence', 'handyman'],
    'cerca': ['fence', 'handyman'],
    'garden': ['gardener'],
    'jardin': ['gardener'],
    'cleaning': ['cleaner'],
    'limpieza': ['cleaner'],
    'pool': ['pool'],
    'piscina': ['pool'],
}


def _maintenance_to_services(category: str) -> List[str]:
    if not category:
        return ['handyman']
    key = str(category).strip().lower()
    return MAINT_CATEGORY_TO_SERVICE.get(key, ['handyman'])


@router.get('/admin/service-providers/match-for-maintenance/{request_id}')
async def admin_match_for_maintenance(request: Request, request_id: str):
    """Suggest active providers that match a maintenance request's category."""
    await auth_admin(request)
    db = get_db()
    # Look up the maintenance request
    from bson import ObjectId
    try:
        oid = ObjectId(request_id)
    except Exception:
        oid = None
    mreq = None
    if oid:
        mreq = await db.maintenance_requests.find_one({"_id": oid})
    if not mreq:
        mreq = await db.maintenance_requests.find_one({"_id": request_id})
    if not mreq:
        raise HTTPException(404, "Maintenance request not found")

    category = mreq.get('category') or mreq.get('title') or 'general'
    matching_services = _maintenance_to_services(category)

    # Find active providers offering any of the matching services
    q = {"status": "active", "services": {"$in": matching_services}}
    providers = []
    async for p in db.service_providers.find(q).sort([("is_featured", -1), ("rating", -1)]).limit(50):
        providers.append(serialize(p))

    return {
        "success": True,
        "maintenance_request": {
            "id": str(mreq.get('_id')),
            "title": mreq.get('title', ''),
            "category": category,
            "priority": mreq.get('priority', ''),
            "description": mreq.get('description', ''),
            "property_address": mreq.get('property_address', ''),
            "tenant_name": mreq.get('tenant_name', ''),
            "tenant_phone": mreq.get('tenant_phone', ''),
        },
        "matching_services": matching_services,
        "matched_providers": providers,
        "count": len(providers),
    }


@router.post('/admin/service-providers/dispatch-maintenance')
async def admin_dispatch_maintenance(request: Request):
    """Dispatch a maintenance request to a provider with auto-composed message."""
    await auth_admin(request)
    data = await request.json()
    provider_id = data.get('provider_id')
    request_id = data.get('request_id')
    extra_note = data.get('extra_note', '')
    via_email = data.get('via_email', True)
    via_sms = data.get('via_sms', True)

    if not (provider_id and request_id):
        raise HTTPException(400, "provider_id and request_id required")

    db = get_db()
    p = await db.service_providers.find_one({"_id": provider_id})
    if not p:
        raise HTTPException(404, "Provider not found")

    # Look up maintenance request (try ObjectId then string)
    from bson import ObjectId
    mreq = None
    try:
        mreq = await db.maintenance_requests.find_one({"_id": ObjectId(request_id)})
    except Exception:
        pass
    if not mreq:
        mreq = await db.maintenance_requests.find_one({"_id": request_id})
    if not mreq:
        raise HTTPException(404, "Maintenance request not found")

    settings = await _get_settings(db)
    lang = p.get('language_pref', 'es')

    addr = mreq.get('property_address') or '—'
    title = mreq.get('title') or 'Mantenimiento'
    description = mreq.get('description') or ''
    priority = mreq.get('priority') or 'medium'
    tenant = mreq.get('tenant_name') or ''
    phone = mreq.get('tenant_phone') or ''

    if lang == 'es':
        subject = f"🛠️ Trabajo: {title} — {addr}"
        body = f"""Hola {p.get('name')},

Tenemos un trabajo de mantenimiento que coincide con tus servicios:

📋 Detalle: {title}
🏠 Dirección: {addr}
⚡ Prioridad: {priority}
👤 Inquilino: {tenant}
📞 Contacto: {phone}

Descripción:
{description}

{extra_note}

Por favor responde a este correo o llámanos al (806) 934-2018 si puedes tomar el trabajo.

— Ross House Rentals"""
        sms_body = f"Ross House: Trabajo disponible en {addr} — {title} ({priority}). Inquilino: {tenant} {phone}. Responde si puedes tomarlo. (806) 934-2018"
    else:
        subject = f"🛠️ Job: {title} — {addr}"
        body = f"""Hi {p.get('name')},

We have a maintenance job matching your services:

📋 Job: {title}
🏠 Address: {addr}
⚡ Priority: {priority}
👤 Tenant: {tenant}
📞 Contact: {phone}

Description:
{description}

{extra_note}

Please reply to this email or call (806) 934-2018 if you can take the job.

— Ross House Rentals"""
        sms_body = f"Ross House: Job available at {addr} — {title} ({priority}). Tenant: {tenant} {phone}. Reply if you can take it. (806) 934-2018"

    email_sent = sms_sent = False
    if via_email and settings.get('email_enabled'):
        email_sent = await _send_email(p['email'], subject, body)
    if via_sms and settings.get('sms_enabled'):
        sms_sent = await _send_sms(p['phone'], sms_body)

    # Log on provider record
    entry = {
        "type": "maintenance_dispatch",
        "sent_at": datetime.utcnow(),
        "subject": subject,
        "message": body,
        "job_id": str(mreq.get('_id')),
        "email": email_sent,
        "sms": sms_sent,
    }
    await db.service_providers.update_one(
        {"_id": provider_id},
        {"$push": {"dispatch_history": entry}, "$inc": {"total_jobs": 1}, "$set": {"updated_at": datetime.utcnow()}}
    )

    # Tag the maintenance ticket with assigned provider
    await db.maintenance_requests.update_one(
        {"_id": mreq['_id']},
        {"$set": {
            "assigned_provider_id": provider_id,
            "assigned_provider_name": p.get('name'),
            "assigned_provider_phone": p.get('phone'),
            "assigned_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }}
    )

    return {"success": True, "email_sent": email_sent, "sms_sent": sms_sent, "provider": serialize(p)}


# ============================================================
# Settings
# ============================================================

@router.get('/admin/provider-settings')
async def get_provider_settings(request: Request):
    await auth_admin(request)
    db = get_db()
    return {"success": True, "settings": await _get_settings(db)}


@router.put('/admin/provider-settings')
async def update_provider_settings(request: Request, payload: ProviderSettings):
    await auth_admin(request)
    db = get_db()
    update = {k: v for k, v in payload.dict().items() if v is not None}
    update['updated_at'] = datetime.utcnow()
    await db.provider_settings.update_one({"_id": "config"}, {"$set": update}, upsert=True)
    return {"success": True, "settings": await _get_settings(db)}
