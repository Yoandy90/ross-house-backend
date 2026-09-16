"""Property inquiries: authenticated ownership, requested/confirmed visits and audit."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from pymongo.errors import DuplicateKeyError
from rental.shared import get_db, auth_admin, auth_marketplace, serialize

router = APIRouter()
ZONE = 'America/Chicago'

def now():
    return datetime.now(timezone.utc)

class Slot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date: str
    time: str

class Create(BaseModel):
    model_config = ConfigDict(extra='forbid')
    property_id: str
    property_type: Literal['ross_house', 'marketplace'] = 'ross_house'
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    phone: str = Field(default='', max_length=40)
    message: str = Field(default='', max_length=3000)
    inquiry_type: Literal['contact', 'apply', 'visit'] = 'contact'
    language: Literal['es', 'en'] = 'es'
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=8, max_length=80, pattern=r'^[A-Za-z0-9-]+$')
    preferred_slot: Slot | None = None

class Update(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0)
    status: Literal['new', 'following_up', 'visit_confirmed', 'resolved', 'cancelled']
    confirmed_slot: Slot | None = None
    customer_note: str = Field(default='', max_length=1000)


def slot_value(slot):
    if not slot:
        return None
    try:
        value = datetime.strptime(slot.date + ' ' + slot.time, '%Y-%m-%d %H:%M')
        local = value.replace(tzinfo=ZoneInfo(ZONE))
        utc = local.astimezone(timezone.utc)
        # Reject nonexistent and ambiguous local times during DST transitions.
        if utc.astimezone(ZoneInfo(ZONE)).replace(tzinfo=None) != value or local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError()
        if not now() < utc < now() + timedelta(days=366):
            raise ValueError()
        return {'at': utc.isoformat(), 'date': slot.date, 'time': slot.time, 'timezone': ZONE}
    except ValueError:
        raise HTTPException(400, 'Selecciona una fecha y hora futura válida / Select a valid future date and time')


def public(doc):
    result = serialize(dict(doc))
    for key in ('request_hash', 'events', 'user_id', 'notice_version'):
        result.pop(key, None)
    result.setdefault('version', 0)
    return result

async def sync_notice(db, doc):
    from rental.property_inquiry_notices import materialize
    # A durable embedded event remains available to the worker if this fails.
    try:
        for event in doc.get('events', []):
            await materialize(db, doc, event)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Inquiry inbox pending recovery')

async def create_inquiry(request: Request, data: Create):
    db = get_db()
    user = await auth_marketplace(request) if request.headers.get('Authorization') else None
    uid = str(user['_id']) if user else ''
    name, email = data.name.strip(), data.email.strip()
    if not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise HTTPException(400, 'Nombre y correo válidos requeridos / Valid name and email required')
    # Never attach historical/anonymous inquiries to accounts by submitted email.
    oid = ObjectId(hashlib.sha256((uid + ':' + data.request_id).encode()).hexdigest()[:24])
    fingerprint = hashlib.sha256(json.dumps(data.model_dump(exclude={'request_id'}), sort_keys=True).encode()).hexdigest()
    existing = await db.property_inquiries.find_one({'_id': oid})
    if existing:
        if existing.get('request_hash') != fingerprint:
            raise HTTPException(409, 'Solicitud modificada / Request changed')
        await sync_notice(db, existing)
        return {'success': True, 'inquiry_id': str(oid), 'inquiry': public(existing)}
    if not ObjectId.is_valid(data.property_id):
        raise HTTPException(404, 'Propiedad no encontrada / Property not found')
    col = db.properties if data.property_type == 'ross_house' else db.marketplace_listings
    prop = await col.find_one({'_id': ObjectId(data.property_id), 'status': {'$ne': 'deleted'}, 'is_deleted': {'$ne': True}})
    if not prop or (data.property_type == 'marketplace' and prop.get('status') != 'approved'):
        raise HTTPException(404, 'Propiedad no encontrada / Property not found')
    if data.preferred_slot and data.inquiry_type != 'visit':
        raise HTTPException(400, 'La fecha solo corresponde a visitas / Dates apply only to visits')
    preferred = slot_value(data.preferred_slot)
    event = {'version': 1, 'status': 'new', 'at': now()}
    doc = {**data.model_dump(exclude={'request_id', 'preferred_slot'}), '_id': oid,
           'name': name, 'email': email, 'phone': data.phone.strip(), 'message': data.message.strip(),
           'user_id': uid, 'property_title': prop.get('address') or prop.get('title') or data.property_id,
           'property_city': prop.get('city', ''), 'preferred_visit': preferred,
           'confirmed_visit': None, 'status': 'new', 'version': 1, 'customer_note': '',
           'created_at': event['at'], 'updated_at': event['at'], 'events': [event], 'request_hash': fingerprint}
    try:
        await db.property_inquiries.insert_one(doc)
    except DuplicateKeyError:
        existing = await db.property_inquiries.find_one({'_id': oid})
        if not existing or existing.get('request_hash') != fingerprint:
            raise HTTPException(409, 'Solicitud modificada / Request changed')
        doc = existing
    await sync_notice(db, doc)
    return {'success': True, 'inquiry_id': str(oid), 'inquiry': public(doc)}

@router.get('/marketplace/property-inquiries')
async def mine(request: Request):
    user = await auth_marketplace(request)
    docs = await get_db().property_inquiries.find({'user_id': str(user['_id'])}).sort('created_at', -1).limit(100).to_list(100)
    for doc in docs:
        await sync_notice(get_db(), doc)
    return {'success': True, 'inquiries': [public(d) for d in docs]}

async def admin_list(request: Request):
    await auth_admin(request)
    docs = await get_db().property_inquiries.find().sort('created_at', -1).limit(100).to_list(100)
    for doc in docs:
        if not doc.get('property_title') and ObjectId.is_valid(doc.get('property_id', '')):
            col = get_db().marketplace_listings if doc.get('property_type') == 'marketplace' else get_db().properties
            prop = await col.find_one({'_id': ObjectId(doc['property_id'])}) or {}
            doc['property_title'] = prop.get('address') or prop.get('title') or doc['property_id']
    return {'success': True, 'inquiries': [public(d) for d in docs], 'count': len(docs)}

@router.patch('/admin/property-inquiries/{inquiry_id}')
async def update(inquiry_id: str, data: Update, request: Request):
    actor = await auth_admin(request)
    if not ObjectId.is_valid(inquiry_id):
        raise HTTPException(404, 'Solicitud no encontrada / Request not found')
    db = get_db()
    doc = await db.property_inquiries.find_one({'_id': ObjectId(inquiry_id)})
    if not doc:
        raise HTTPException(404, 'Solicitud no encontrada / Request not found')
    if doc.get('version', 0) != data.version:
        raise HTTPException(409, 'Actualiza la lista antes de guardar / Refresh before saving')
    if doc.get('status') in ('resolved', 'cancelled'):
        raise HTTPException(409, 'Solicitud cerrada / Request closed')
    confirmed = doc.get('confirmed_visit') if data.status == 'resolved' else None
    if data.status == 'visit_confirmed':
        if doc.get('inquiry_type') != 'visit' or not data.confirmed_slot:
            raise HTTPException(400, 'Indica fecha y hora de la visita / Set the visit date and time')
        confirmed = slot_value(data.confirmed_slot)
    elif data.confirmed_slot:
        raise HTTPException(400, 'Confirma la visita para guardar una cita / Confirm the visit to save an appointment')
    version = data.version + 1
    event = {'version': version, 'status': data.status, 'at': now(), 'actor_id': str(actor.get('_id') or actor.get('id') or 'admin')}
    values = {'status': data.status, 'version': version, 'confirmed_visit': confirmed,
              'customer_note': data.customer_note.strip(), 'updated_at': event['at']}
    predicate = {'_id': doc['_id'], 'version': data.version} if 'version' in doc else {'_id': doc['_id'], 'version': {'$exists': False}}
    result = await db.property_inquiries.update_one(predicate, {'$set': values, '$push': {'events': event}})
    if not result.modified_count:
        raise HTTPException(409, 'Solicitud actualizada por otro administrador / Request changed by another administrator')
    doc.update(values); doc['events'] = [event]
    await sync_notice(db, doc)
    return {'success': True, 'inquiry': public(doc)}
