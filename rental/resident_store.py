"""Resident-owned inventory pilot. One versioned aggregate makes inventory/order
changes atomic on both standalone MongoDB and replica sets, without cross-collection
partial writes. Hard size/capacity limits fail closed; no rental ledger writes.
"""
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
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError
from rental.shared import get_db, auth_admin, auth_marketplace

router = APIRouter(tags=['Resident store'])
KEY = 'resident-store-v1'
MAX_BYTES = 6_000_000

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Product(Strict):
    name: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    description_en: str = Field(default='', max_length=500)
    category: str = Field(min_length=1, max_length=50)
    image_url: str = Field(default='', max_length=1000)
    price_cents: int = Field(strict=True, gt=0, le=1000000)
    cost_cents: int = Field(strict=True, ge=0, le=1000000)
    tax_bps: int = Field(strict=True, ge=0, le=10000)
    stock: int = Field(strict=True, ge=0, le=100000)
    active: bool = False

    @field_validator('image_url')
    @classmethod
    def https_only(cls, v):
        if v and not v.startswith('https://'):
            raise ValueError('HTTPS required')
        return v

class Settings(Strict):
    enabled: bool = False
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
    items: list[Item] = Field(min_length=1, max_length=40)
    fulfillment: Literal['delivery','pickup']
    address: str = Field(default='', max_length=250)
    zip: str = Field(default='', max_length=5)
    slot: str = Field(min_length=1, max_length=100)

class PlaceOrder(Checkout):
    idempotency_key: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,100}$')
    quote_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

class SaveProduct(Strict):
    revision: int
    product: Product

class SaveSettings(Strict):
    revision: int
    settings: Settings

class Transition(Strict):
    status: Literal['preparing','ready','delivered','cancelled']

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
            return result
    raise HTTPException(409, 'store_busy_retry')

def audit(state, actor, action, target):
    state['audit'].append({'at':now(),'actor':actor,'action':action,'target':target})
    # Fail closed instead of silently discarding audit history.
    if len(state['audit']) > 15000:
        raise HTTPException(409,'store_capacity_review_required')

def product_public(pid, p):
    return {'id':pid, **{k:v for k,v in p.items() if k!='cost_cents'}}

def order_public(order):
    return {k:v for k,v in order.items() if k not in ('fingerprint','idempotency_key','cost_cents','payment_actor')}

def quote(state, body):
    cfg = state['settings']
    if not cfg['enabled']:
        raise HTTPException(409,'store_closed')
    if body.slot not in cfg['slots']:
        raise HTTPException(409,'store_slot_unavailable')
    if body.fulfillment == 'delivery':
        if body.zip not in cfg['delivery_zips'] or len(body.address.strip()) < 8:
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
        lines.append({'product_id':pid,'name':p['name'],'name_en':p['name_en'],'quantity':qty,'price_cents':p['price_cents'],'subtotal_cents':subtotal,'tax_cents':(subtotal*p['tax_bps']+5000)//10000})
    subtotal=sum(x['subtotal_cents'] for x in lines)
    if subtotal<cfg['minimum_cents']:
        raise HTTPException(400,'store_minimum_not_met')
    fee=cfg['delivery_fee_cents'] if body.fulfillment=='delivery' else 0
    tax=sum(x['tax_cents'] for x in lines)+(fee*cfg['delivery_tax_bps']+5000)//10000
    result={'items':lines,'subtotal_cents':subtotal,'tax_cents':tax,'delivery_fee_cents':fee,'total_cents':subtotal+tax+fee,'fulfillment':body.fulfillment,'address':body.address if body.fulfillment=='delivery' else cfg['pickup_address'],'zip':body.zip if body.fulfillment=='delivery' else '', 'slot':body.slot,'payment_method':'pay_on_receipt'}
    result['quote_hash']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result

@router.get('/store/catalog')
async def catalog(request:Request):
    user=await resident(request)
    s=await read_state()
    return {'settings':s['settings'],'products':[product_public(k,v) for k,v in s['products'].items() if v['active']], 'default_address':user.get('address','') if isinstance(user.get('address',''),str) else ''}

@router.post('/store/quote')
async def checkout_quote(body:Checkout,request:Request):
    await resident(request)
    return quote(await read_state(),body)

@router.post('/store/orders')
async def place_order(body:PlaceOrder,request:Request):
    user=await resident(request);uid=identity(user)
    fingerprint=hashlib.sha256(json.dumps(body.model_dump(exclude={'idempotency_key'}),sort_keys=True).encode()).hexdigest()
    oid=str(uuid4())
    def operation(s):
        for existing in s['orders'].values():
            if existing['user_id']==uid and existing['idempotency_key']==body.idempotency_key:
                if existing['fingerprint']!=fingerprint:
                    raise HTTPException(409,'store_idempotency_conflict')
                return order_public(existing)
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
        s['orders'][oid]=order;audit(s,uid,'order_created',oid)
        return order_public(order)
    return await mutate(operation)

@router.get('/store/orders')
async def my_orders(request:Request):
    uid=identity(await resident(request));s=await read_state()
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
    paid=[o for o in s['orders'].values() if o['payment_status']=='paid']
    return {'revision':s['revision'],'settings':s['settings'],'products':[{'id':k,**v} for k,v in s['products'].items()],'orders':sorted(list(s['orders'].values()),key=lambda o:o['created_at'],reverse=True),'summary':{'collected_cents':sum(o['total_cents'] for o in paid),'merchandise_margin_cents':sum(o['subtotal_cents']-o['cost_cents'] for o in paid),'tax_cents':sum(o['tax_cents'] for o in paid),'pending_orders':sum(o['status'] not in ('cancelled','delivered') for o in s['orders'].values())}}

@router.put('/admin/store/settings')
async def save_settings(body:SaveSettings,request:Request):
    uid=identity(await auth_admin(request))
    def operation(s):
        if s['revision']!=body.revision:
            raise HTTPException(409,'store_revision_changed')
        cfg=body.settings.model_dump()
        if cfg['enabled'] and (not cfg['slots'] or not (cfg['pickup_address'] or cfg['delivery_zips'])):
            raise HTTPException(400,'store_setup_incomplete')
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
        s['products'][pid]=body.product.model_dump()
        audit(s,uid,'product_updated',{'id':pid,'before':old,'after':s['products'][pid].copy()})
        return {'ok':True}
    return await mutate(operation)

def change_status(s,o,target,uid,resident_action=False):
    if o['status']==target:
        return order_public(o)
    allowed={'received':{'preparing','cancelled'},'preparing':{'ready','cancelled'},'ready':{'delivered','cancelled'}}
    if target not in allowed.get(o['status'],set()) or (resident_action and o['status']!='received'):
        raise HTTPException(409,'store_transition_invalid')
    if target=='cancelled':
        if o['payment_status']=='paid':
            raise HTTPException(409,'store_refund_required')
        for line in o['items']:
            s['products'][line['product_id']]['stock']+=line['quantity']
    if target=='delivered' and o['payment_status']!='paid':
        raise HTTPException(409,'store_payment_required')
    o['status']=target;o['updated_at']=now();audit(s,uid,'order_'+target,o['id'])
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
        if o['status'] not in ('received','preparing','ready'):
            raise HTTPException(409,'store_transition_invalid')
        o.update(payment_status='paid',payment_reference=body.reference,payment_actor=uid,paid_at=now(),updated_at=now())
        audit(s,uid,'payment_recorded',oid)
        return order_public(o)
    return await mutate(operation)


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
    return {'path': '/api/public/store-images/' + image_id, 'width': 1200,
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
