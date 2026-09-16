"""Resident-owned inventory pilot. One versioned aggregate makes inventory/order
changes atomic on both standalone MongoDB and replica sets, without cross-collection
partial writes. Hard size/capacity limits fail closed; no rental ledger writes.
"""
import os
import base64
import binascii
import re
import copy
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from typing import Literal
from bson import BSON
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from rental.catalog_images import normalize_product_photo, MAX_INPUT_BYTES
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError
from rental.shared import get_db, auth_admin, auth_marketplace

router = APIRouter(tags=['Resident store'])
KEY = 'resident-store-v1'
MAX_BYTES = 6_000_000

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class TaxClass(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    name: str = Field(min_length=1, max_length=80)
    rate_bps: int = Field(strict=True, ge=0, le=10000)

class Product(Strict):
    sku: str = Field(default='', max_length=80, pattern=r'^[a-zA-Z0-9._-]*$')
    barcode: str = Field(default='', max_length=80, pattern=r'^[a-zA-Z0-9._-]*$')
    family_id: str = Field(default='', max_length=60, pattern=r'^[a-zA-Z0-9_-]*$')
    brand: str = Field(default='', max_length=80)
    size: str = Field(default='', max_length=40)
    color: str = Field(default='', max_length=40)
    sale_unit: Literal['unit','pack','kg','g','lb','oz','l','ml','m','ft'] = 'unit'
    pack_size: str = Field(default='1', max_length=16)
    tax_class: str = Field(default='', max_length=60)
    reorder_level: int = Field(default=5, strict=True, ge=0, le=100000)

    name: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    description_en: str = Field(default='', max_length=500)
    category: str = Field(min_length=1, max_length=50)
    image_url: str = Field(default='', max_length=1000)
    image_urls: list[str] = Field(default_factory=list, max_length=8)
    price_cents: int = Field(strict=True, gt=0, le=1000000)
    cost_cents: int = Field(strict=True, ge=0, le=1000000)
    tax_bps: int = Field(strict=True, ge=0, le=10000)
    stock: int = Field(strict=True, ge=0, le=100000)
    active: bool = False

    @field_validator('image_urls')
    @classmethod
    def valid_gallery(cls, values):
        if any(not v.startswith('https://') or len(v) > 1000 for v in values):
            raise ValueError('Gallery images must use HTTPS')
        return list(dict.fromkeys(values))

    @model_validator(mode='after')
    def primary_photo(self):
        if self.image_urls:
            self.image_url = self.image_urls[0]
        elif self.image_url:
            self.image_urls = [self.image_url]
        return self

    @field_validator('image_url')
    @classmethod
    def https_only(cls, v):
        if v and not v.startswith('https://'):
            raise ValueError('HTTPS required')
        return v

class Settings(Strict):
    enabled: bool = False
    online_payments: bool = True
    email_notifications: bool = True
    home_delivery_only: bool = True
    tax_classes: list[TaxClass] = Field(default_factory=list, max_length=30)

    @field_validator('tax_classes')
    @classmethod
    def unique_tax_classes(cls, values):
        if len({v.id for v in values}) != len(values):
            raise ValueError('Duplicate tax class')
        return values
    pickup_address: str = Field(default='', max_length=250)
    delivery_zips: list[str] = Field(default_factory=list, max_length=30)
    slots: list[str] = Field(default_factory=list, max_length=20)
    delivery_fee_cents: int = Field(default=0, strict=True, ge=0, le=10000)
    delivery_tax_bps: int = Field(default=0, strict=True, ge=0, le=10000)
    minimum_cents: int = Field(default=0, strict=True, ge=0, le=100000)

    @field_validator('slots')
    @classmethod
    def valid_slots(cls, values):
        if any(not x.strip() or len(x)>100 for x in values) or len(set(values))!=len(values):
            raise ValueError('Invalid slots')
        return values

    @field_validator('delivery_zips')
    @classmethod
    def valid_zips(cls, values):
        if any(len(x)!=5 or not x.isascii() or not x.isdigit() for x in values):
            raise ValueError('Invalid ZIP')
        return list(dict.fromkeys(values))

class Item(Strict):
    product_id: str = Field(min_length=1, max_length=60)
    quantity: int = Field(strict=True, ge=1, le=99)

class Checkout(Strict):
    payment_method_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{24}$')
    payment_authorized: bool | None = None
    payment_authorization_version: str | None = Field(default=None, max_length=50)
    language: Literal['es','en'] | None = None
    items: list[Item] = Field(min_length=1, max_length=40)
    delivery_instructions: str = Field(default='', max_length=300)
    fulfillment: Literal['delivery','pickup']
    address: str = Field(default='', max_length=250)
    zip: str = Field(default='', max_length=5)
    slot: str = Field(min_length=1, max_length=100)

class PlaceOrder(Checkout):
    idempotency_key: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,100}$')
    quote_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class SaveProduct(Strict):
    revision: int
    stock_reason: str = Field(default='', max_length=200)
    product: Product

class SaveSettings(Strict):
    revision: int
    settings: Settings

class Transition(Strict):
    status: Literal['preparing','ready','out_for_delivery','delivered','cancelled']

class Payment(Strict):
    reference: str = Field(min_length=3, max_length=150)


def now():
    return datetime.now(timezone.utc).isoformat()

def identity(user):
    uid = user.get('_id') or user.get('id')
    if not uid:
        raise HTTPException(401, 'store_auth_required')
    return str(uid)

async def resident(request):
    user = await auth_marketplace(request)
    if user.get('role') not in ('tenant','admin'):
        raise HTTPException(403, 'store_residents_only')
    return user

async def read_state():
    db = get_db()
    state = await db.resident_store.find_one({'_id':KEY})
    if state:
        return state
    state = {'_id':KEY, 'revision':0, 'settings':Settings().model_dump(), 'products':{}, 'orders':{}, 'audit':[]}
    try:
        await db.resident_store.insert_one(copy.deepcopy(state))
    except DuplicateKeyError:
        return await db.resident_store.find_one({'_id':KEY})
    return state

async def mutate(operation):
    for _ in range(12):
        state = await read_state()
        revision = state['revision']
        result = operation(state)
        state['revision'] += 1
        if len(BSON.encode(state)) > MAX_BYTES:
            raise HTTPException(409, 'store_capacity_review_required')
        saved = await get_db().resident_store.replace_one({'_id':KEY,'revision':revision},state)
        if saved.modified_count:
            # The audit entry is the durable outbox. Failure to publish a notice
            # must never turn an accepted purchase into an apparent failure.
            from rental.store_notifications import sync_inbox_safely
            await sync_inbox_safely(get_db())
            return result
    raise HTTPException(409, 'store_busy_retry')

def audit(state, actor, action, target, store_notice=None):
    entry = {'at':now(),'actor':actor,'action':action,'target':target}
    if store_notice:
        entry['store_notice'] = store_notice
    state['audit'].append(entry)
    # Fail closed instead of silently discarding audit history.
    if len(state['audit']) > 15000:
        raise HTTPException(409,'store_capacity_review_required')

def product_public(pid, p):
    from rental.store_inventory import sku_for
    return {'id':pid, **{k:v for k,v in p.items() if k!='cost_cents'}, 'sku':sku_for(pid,p)}

def order_public(order):
    result = order_display({k:v for k,v in order.items() if k not in ('fingerprint','idempotency_key','cost_cents','payment_actor','driver_id','residence','payment_attempt','refund_attempt')})
    from rental.store_payments import public_payment
    result['online_payment'] = public_payment(order)
    if result.get('tracking'):
        result['tracking'].pop('issue', None)
        result['tracking'].pop('issue_at', None)
    return result

def order_display(order):
    # Presentation only: legacy purchase snapshots and accounting stay intact.
    result = copy.deepcopy(order)
    if result.get('address') == 'DEMO · Recepción de staging (sin entregas reales)':
        result['address'] = 'Recepción · Ross House Rentals'
    if result.get('slot') == 'DEMO · Solo simulación':
        result['slot'] = 'Coordinar horario de recogida'
    for item in result.get('items', []):
        for field in ('name', 'name_en'):
            if item.get(field):
                item[field] = re.sub(r'^DEMO\s*[·:—-]\s*', '', item[field], flags=re.I)
    return result

def quote(state, body):
    cfg = state['settings']
    if not cfg['enabled']:
        raise HTTPException(409,'store_closed')
    if body.slot not in cfg['slots']:
        raise HTTPException(409,'store_slot_unavailable')
    if body.fulfillment == 'delivery':
        if not cfg.get('home_delivery_only', False) and (body.zip not in cfg['delivery_zips'] or len(body.address.strip()) < 8):
            raise HTTPException(400,'store_delivery_unavailable')
    elif not cfg['pickup_address']:
        raise HTTPException(400,'store_pickup_unavailable')
    quantities = {}
    for item in body.items:
        quantities[item.product_id] = quantities.get(item.product_id,0)+item.quantity
    lines=[]
    for pid, qty in sorted(quantities.items()):
        p=state['products'].get(pid)
        if not p or not p['active'] or qty>99 or p['stock']<qty:
            raise HTTPException(409,'store_stock_unavailable')
        subtotal=p['price_cents']*qty
        from rental.store_inventory import tax_rate, sku_for
        rate = tax_rate(state, p)
        lines.append({'product_id':pid,'image_url':p.get('image_url',''),'sku':sku_for(pid,p),'sale_unit':p.get('sale_unit','unit'),'pack_size':p.get('pack_size','1'),'size':p.get('size',''),'color':p.get('color',''),'tax_bps':rate,'name':p['name'],'name_en':p['name_en'],'quantity':qty,'price_cents':p['price_cents'],'subtotal_cents':subtotal,'tax_cents':(subtotal*rate+5000)//10000})
    subtotal=sum(x['subtotal_cents'] for x in lines)
    if subtotal<cfg['minimum_cents']:
        raise HTTPException(400,'store_minimum_not_met')
    fee=cfg['delivery_fee_cents'] if body.fulfillment=='delivery' else 0
    tax=sum(x['tax_cents'] for x in lines)+(fee*cfg['delivery_tax_bps']+5000)//10000
    result={'items':lines,'subtotal_cents':subtotal,'tax_cents':tax,'delivery_fee_cents':fee,'total_cents':subtotal+tax+fee,'fulfillment':body.fulfillment,'address':body.address if body.fulfillment=='delivery' else cfg['pickup_address'],'zip':body.zip if body.fulfillment=='delivery' else '', 'slot':body.slot,'payment_method':'pay_on_receipt','delivery_instructions':body.delivery_instructions}
    if body.payment_method_id:
        result['payment_method'] = 'helcim'
        result['selected_method_id'] = body.payment_method_id
    result['quote_hash']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result

@router.get('/store/catalog')
async def catalog(request:Request):
    user=await resident(request)
    s=await read_state()
    from rental.store_delivery import home_for
    home = await home_for(user) if s['settings'].get('home_delivery_only', False) else None
    return {'residence': home, 'default_zip': home['zip'] if home else '', 'settings':s['settings'],'products':[product_public(k,v) for k,v in s['products'].items() if v['active']], 'default_address':home['address'] if home else ''}

@router.post('/store/quote')
async def checkout_quote(body:Checkout,request:Request):
    user = await resident(request)
    state = await read_state()
    from rental.store_delivery import home_checkout
    body, _ = await home_checkout(state, body, user)
    from rental.store_payments import prepare
    await prepare(user, body, state)
    return quote(state,body)

@router.post('/store/orders')
async def place_order(body:PlaceOrder,request:Request):
    user=await resident(request);uid=identity(user)
    fingerprint=hashlib.sha256(json.dumps(body.model_dump(exclude={'idempotency_key'}, exclude_none=True),sort_keys=True).encode()).hexdigest()
    oid=str(uuid4())
    initial = await read_state()
    # Recover committed requests even if the lease or address has since changed.
    for existing in initial['orders'].values():
        if existing['user_id'] == uid and existing['idempotency_key'] == body.idempotency_key:
            if existing['fingerprint'] != fingerprint:
                raise HTTPException(409, 'store_idempotency_conflict')
            return order_public(existing)
    from rental.store_delivery import home_checkout
    body, home = await home_checkout(initial, body, user)
    from rental.store_payments import prepare, attach, charge
    prepared = await prepare(user, body, initial)
    def operation(s):
        for existing in s['orders'].values():
            if existing['user_id']==uid and existing['idempotency_key']==body.idempotency_key:
                if existing['fingerprint']!=fingerprint:
                    raise HTTPException(409,'store_idempotency_conflict')
                return {'order': existing, 'created': False}
        if s['settings'].get('home_delivery_only', False) != initial['settings'].get('home_delivery_only', False):
            raise HTTPException(409, 'store_quote_changed')
        if prepared and not s['settings'].get('online_payments', True):
            raise HTTPException(409, 'store_payment_disabled')
        q=quote(s,body)
        if q['quote_hash']!=body.quote_hash:
            raise HTTPException(409,'store_quote_changed')
        if len(s['orders'])>=2000:
            raise HTTPException(409,'store_capacity_review_required')
        cost=0
        for line in q['items']:
            p=s['products'][line['product_id']]
            p['stock']-=line['quantity'];cost+=p['cost_cents']*line['quantity']
        order={**q,'id':oid,'user_id':uid,'customer_name':str(user.get('name',''))[:150],'created_at':now(),'updated_at':now(),'status':'received','payment_status':'unpaid','cost_cents':cost,'idempotency_key':body.idempotency_key,'fingerprint':fingerprint}
        if home: order['residence'] = home
        order['language'] = body.language or ('en' if str(user.get('language') or user.get('preferred_language') or '').startswith('en') else 'es')
        order['tracking'] = {'received_at': order['created_at']}
        attach(order, prepared)
        s['orders'][oid]=order
        from rental.store_inventory import movement
        for line in q['items']:
            movement(s, uid, line['product_id'], -line['quantity'], 'order_reserved', oid)
        audit(s,uid,'order_created',oid,{'order_id':oid,'user_id':uid,'status':'received','email_notice':True})
        return {'order': order, 'created': True}
    result = await mutate(operation)
    if result['created'] and prepared:
        return await charge(result['order'], prepared, request.client.host if request.client else '127.0.0.1')
    return order_public(result['order'])

@router.get('/store/orders')
async def my_orders(request:Request):
    uid=identity(await resident(request));s=await read_state()
    from rental.store_notifications import sync_inbox_safely
    await sync_inbox_safely(get_db())
    return {'orders':sorted([order_public(o) for o in s['orders'].values() if o['user_id']==uid],key=lambda o:o['created_at'],reverse=True)}

@router.post('/store/orders/{oid}/cancel')
async def cancel_order(oid:str,request:Request):
    uid=identity(await resident(request))
    def operation(s):
        o=s['orders'].get(oid)
        if not o or o['user_id']!=uid:
            raise HTTPException(404,'store_order_not_found')
        return change_status(s,o,'cancelled',uid,resident_action=True)
    return await mutate(operation)

@router.get('/admin/store')
async def admin_state(request:Request):
    await auth_admin(request);s=await read_state()
    from rental.store_notifications import sync_inbox_safely
    await sync_inbox_safely(get_db())
    paid=[o for o in s['orders'].values() if o['payment_status']=='paid']
    return {'inventory_movements':[a for a in s['audit'] if a['action']=='inventory_movement'],'drivers':list(s.get('drivers', {}).values()),'revision':s['revision'],'settings':s['settings'],'products':[{'id':k,**v} for k,v in s['products'].items()],'orders':sorted([order_display(o) for o in s['orders'].values()],key=lambda o:o['created_at'],reverse=True),'summary':{'collected_cents':sum(o['total_cents'] for o in paid),'merchandise_margin_cents':sum(o['subtotal_cents']-o['cost_cents'] for o in paid),'tax_cents':sum(o['tax_cents'] for o in paid),'pending_orders':sum(o['status'] not in ('cancelled','delivered') for o in s['orders'].values())}}

@router.put('/admin/store/settings')
async def save_settings(body:SaveSettings,request:Request):
    uid=identity(await auth_admin(request))
    def operation(s):
        if s['revision']!=body.revision:
            raise HTTPException(409,'store_revision_changed')
        cfg=body.settings.model_dump()
        if cfg['enabled'] and (not cfg['slots'] or not (cfg['home_delivery_only'] or cfg['pickup_address'] or cfg['delivery_zips'])):
            raise HTTPException(400,'store_setup_incomplete')
        tax_ids = {t['id'] for t in cfg['tax_classes']}
        if any(p.get('tax_class') and p['tax_class'] not in tax_ids for p in s['products'].values()):
            raise HTTPException(409, 'store_tax_class_in_use')
        s['settings']=cfg;audit(s,uid,'settings_updated',KEY)
        return {'ok':True}
    return await mutate(operation)

@router.put('/admin/store/products/{pid}')
async def save_product(pid:str,body:SaveProduct,request:Request):
    uid=identity(await auth_admin(request))
    if len(pid)>60 or not pid.replace('-','').isalnum():
        raise HTTPException(400,'store_product_invalid')
    def operation(s):
        if s['revision']!=body.revision:
            raise HTTPException(409,'store_revision_changed')
        if pid not in s['products'] and len(s['products'])>=300:
            raise HTTPException(409,'store_capacity_review_required')
        old=s['products'].get(pid,{})
        from rental.store_inventory import validate_product, movement
        if old and old['stock'] != body.product.stock and not body.stock_reason.strip():
            raise HTTPException(400, 'store_stock_reason_required')
        s['products'][pid]=validate_product(s, pid, body.product.model_dump())
        movement(s, uid, pid, s['products'][pid]['stock'] - old.get('stock',0), body.stock_reason or 'opening_stock')
        audit(s,uid,'product_updated',{'id':pid,'before':old,'after':s['products'][pid].copy()})
        return {'ok':True}
    return await mutate(operation)

def change_status(s,o,target,uid,resident_action=False):
    if o['status']==target:
        return order_public(o)
    if o.get('payment_review_required'):
        raise HTTPException(409, 'store_payment_review_required')
    if o.get('payment_attempt') and target != 'cancelled' and o['payment_status'] != 'paid':
        raise HTTPException(409, 'store_payment_required')
    if o.get('refund_attempt') and target != 'cancelled':
        raise HTTPException(409, 'store_refund_in_progress')
    allowed={'received':{'preparing','cancelled'},'preparing':{'ready','cancelled'},'ready':{'out_for_delivery','delivered','cancelled'},'out_for_delivery':{'delivered'}}
    if target not in allowed.get(o['status'],set()) or (resident_action and o['status']!='received'):
        raise HTTPException(409,'store_transition_invalid')
    if target == 'out_for_delivery' and not o.get('driver_id'):
        raise HTTPException(409, 'store_driver_required')
    if target=='cancelled':
        if o.get('payment_attempt') and o['payment_status'] not in ('failed','refunded'):
            raise HTTPException(409, 'store_payment_review_required')
        if o['payment_status']=='paid':
            raise HTTPException(409,'store_refund_required')
        for line in o['items']:
            s['products'][line['product_id']]['stock']+=line['quantity']
            from rental.store_inventory import movement
            movement(s, uid, line['product_id'], line['quantity'], 'order_cancelled', o['id'])
    if target=='delivered' and o['payment_status']!='paid':
        raise HTTPException(409,'store_payment_required')
    o['status']=target;o['updated_at']=now()
    o.setdefault('tracking', {})[target + '_at'] = o['updated_at']
    audit(s,uid,'order_'+target,o['id'],{'order_id':o['id'],'user_id':o['user_id'],'status':target})
    return order_public(o)

@router.post('/admin/store/orders/{oid}/status')
async def update_status(oid:str,body:Transition,request:Request):
    uid=identity(await auth_admin(request))
    def operation(s):
        o=s['orders'].get(oid)
        if not o:raise HTTPException(404,'store_order_not_found')
        return change_status(s,o,body.status,uid)
    return await mutate(operation)

@router.post('/admin/store/orders/{oid}/payment')
async def record_payment(oid:str,body:Payment,request:Request):
    uid=identity(await auth_admin(request))
    def operation(s):
        o=s['orders'].get(oid)
        if not o:raise HTTPException(404,'store_order_not_found')
        if o['payment_status']=='paid':return order_public(o)
        if o.get('payment_attempt'):
            raise HTTPException(409, 'store_online_payment_requires_verification')
        if o['status'] not in ('received','preparing','ready','out_for_delivery'):
            raise HTTPException(409,'store_transition_invalid')
        o.update(payment_status='paid',payment_reference=body.reference,payment_actor=uid,paid_at=now(),updated_at=now())
        from rental.store_receipts import issue_receipt
        issue_receipt(s,o)
        if o.get('tracking', {}).get('handoff_at') and o['status'] == 'out_for_delivery':
            change_status(s, o, 'delivered', uid)
        audit(s,uid,'payment_recorded',oid,{'order_id':oid,'user_id':o['user_id'],'status':'paid','email_notice':True})
        return order_public(o)
    result = await mutate(operation)
    # Durable receipt metadata commits with the payment. PDF storage is retried
    # on download if unavailable; it must not turn an accepted payment into 500.
    if result.get('receipt'):
        import asyncio
        import logging
        from rental.store_receipts import receipt_payload
        try:
            for language in ('es', 'en'):
                await asyncio.wait_for(receipt_payload(get_db(), result, language), timeout=3)
        except Exception as exc:
            logging.getLogger(__name__).warning('Store receipt rendering deferred (%s)', type(exc).__name__)
    return result


async def download_receipt(oid, request, response, admin=False, language='es'):
    user = await auth_admin(request) if admin else await resident(request)
    state = await read_state()
    order = state['orders'].get(oid)
    if not order or (not admin and order['user_id'] != identity(user)):
        raise HTTPException(404, 'store_order_not_found')
    if order['payment_status'] not in ('paid','refunded'):
        raise HTTPException(409, 'store_receipt_requires_payment')
    if not order.get('receipt'):
        # Give legacy paid purchases one durable number, without another charge
        # or a historical payment notification.
        def operation(s):
            from rental.store_receipts import issue_receipt
            o = s['orders'][oid]
            if not o.get('receipt'):
                issue_receipt(s, o)
                audit(s, identity(user), 'receipt_issued', oid)
            return order_public(o)
        order = await mutate(operation)
    from rental.store_receipts import receipt_payload
    response.headers['Cache-Control'] = 'private, no-store'
    return await receipt_payload(get_db(), order, language)


@router.get('/store/orders/{oid}/receipt')
async def customer_receipt(oid:str,request:Request,response:Response,language:Literal['es','en']='es'):
    return await download_receipt(oid, request, response, language=language)


@router.get('/admin/store/orders/{oid}/receipt')
async def admin_receipt(oid:str,request:Request,response:Response,language:Literal['es','en']='es'):
    return await download_receipt(oid, request, response, admin=True, language=language)


class ProductPhoto(Strict):
    data: str = Field(min_length=1, max_length=4_000_000)


@router.post('/admin/store/images')
async def upload_product_photo(body: ProductPhoto, request: Request):
    actor = identity(await auth_admin(request))
    try:
        raw = base64.b64decode(body.data, validate=True)
        normalized = await run_in_threadpool(normalize_product_photo, raw)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, str(exc)) from exc
    image_id = hashlib.sha256(normalized).hexdigest()
    await get_db().resident_store_images.update_one(
        {'_id': image_id},
        {'$setOnInsert': {'data': base64.b64encode(normalized).decode('ascii'), 'created_at': now(), 'actor': actor}},
        upsert=True,
    )
    path = '/api/public/store-images/' + image_id
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip()
    origin = ('https://' + domain) if domain else str(request.base_url).rstrip('/')
    return {'path': path, 'url': origin + path, 'width': 1200,
            'height': 1200, 'bytes': len(normalized), 'format': 'webp'}


@router.get('/public/store-images/{image_id}')
async def product_photo(image_id: str):
    if not re.fullmatch(r'[a-f0-9]{64}', image_id):
        raise HTTPException(404, 'store_image_not_found')
    photo = await get_db().resident_store_images.find_one({'_id': image_id})
    if not photo:
        raise HTTPException(404, 'store_image_not_found')
    return Response(base64.b64decode(photo['data']), media_type='image/webp', headers={
        'Cache-Control': 'public, max-age=31536000, immutable',
        'X-Content-Type-Options': 'nosniff',
    })

from rental.store_delivery import router as delivery_router
router.include_router(delivery_router)
from rental.store_payments import router as payments_router
router.include_router(payments_router)
