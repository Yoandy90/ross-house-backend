"""Home delivery and explicitly enrolled couriers. No public driver registration.
Assignments share the order CAS; couriers never acquire admin/payment privileges.
"""
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field
from rental import resident_store as s

router = APIRouter()
INACTIVE = {'deleted', 'inactive', 'disabled', 'suspended'}

async def home_for(user):
    from rental.tenant_integrity import resolve_authenticated_tenant, find_active_contract_for_tenant
    tenant = await resolve_authenticated_tenant(user)
    contract = await find_active_contract_for_tenant(tenant) if tenant else None
    if not contract:
        return None
    pid = str(contract.get('property_id') or '')
    if not ObjectId.is_valid(pid):
        return None
    prop = await s.get_db().properties.find_one({'_id': ObjectId(pid)})
    if not prop or not isinstance(prop.get('address'), str) or len(prop['address'].strip()) < 5:
        return None
    parts = [prop['address'].strip()]
    unit_id = str(contract.get('unit_id') or '')
    if unit_id:
        if not ObjectId.is_valid(unit_id):
            return None
        unit = await s.get_db().property_units.find_one({'_id': ObjectId(unit_id)})
        if not unit or str(unit.get('property_id')) != pid or not unit.get('unit_name'):
            return None
        parts.append(str(unit['unit_name']))
    parts.extend(str(prop[k]).strip() for k in ('city', 'state') if prop.get(k))
    address = ', '.join(parts)
    if len(address) > 250:
        return None
    postal = str(prop.get('zip_code') or prop.get('zip') or prop.get('zipcode') or '')
    return {'address': address, 'zip': postal[:5], 'property_id': pid, 'unit_id': unit_id,
            'contract_id': str(contract['_id'])}

async def home_checkout(state, body, user):
    if not state['settings'].get('home_delivery_only', False):
        return body, None
    if body.fulfillment != 'delivery':
        raise HTTPException(400, 'store_pickup_unavailable')
    home = await home_for(user)
    if not home:
        raise HTTPException(409, 'store_home_unavailable')
    if body.address != home['address'] or body.zip != home['zip']:
        raise HTTPException(409, 'store_home_changed')
    return body, home

class Enroll(s.Strict):
    email: str = Field(min_length=3, max_length=254)
    active: bool = True

class Assignment(s.Strict):
    driver_id: str = Field(min_length=1, max_length=100)
    expected_driver_id: str = Field(default='', max_length=100)
    eta: datetime

class DriverAction(s.Strict):
    action: str = Field(pattern='^(depart|handoff|issue)$')
    note: str = Field(default='', max_length=300)

async def active_account(uid):
    if not ObjectId.is_valid(uid):
        return None
    user = await s.get_db().app_users.find_one({'_id': ObjectId(uid)})
    if not user or user.get('status') in INACTIVE or user.get('active') is False or user.get('is_active') is False:
        return None
    return user

async def courier(request):
    user = await s.auth_marketplace(request)
    uid = s.identity(user)
    if not await active_account(uid):
        raise HTTPException(403, 'store_driver_access_required')
    return uid


def allowed_driver(state, uid):
    if not state.get('drivers', {}).get(uid, {}).get('active'):
        raise HTTPException(403, 'store_driver_access_required')


def driver_order(o):
    # Whitelist: no tenant IDs, costs, rental records, receipt, or payment references.
    keys = ('id', 'customer_name', 'address', 'zip', 'slot', 'status', 'created_at',
            'items', 'total_cents', 'payment_status', 'delivery_instructions', 'tracking')
    return {k: v for k, v in s.order_display(o).items() if k in keys}


@router.put('/admin/store/drivers')
async def enroll_driver(body: Enroll, request: Request):
    actor = s.identity(await s.auth_admin(request))
    import re
    users = await s.get_db().app_users.find({'email': {'$regex': '^' + re.escape(body.email.strip()) + '$', '$options': 'i'}}).limit(2).to_list(2)
    if len(users) != 1 or not await active_account(str(users[0]['_id'])):
        raise HTTPException(400, 'store_driver_account_unavailable')
    user = users[0]; uid = str(user['_id'])
    def operation(state):
        drivers = state.setdefault('drivers', {})
        if uid not in drivers and len(drivers) >= 100:
            raise HTTPException(409, 'store_capacity_review_required')
        if not body.active and any(o.get('driver_id') == uid and o['status'] not in ('delivered', 'cancelled') for o in state['orders'].values()):
            raise HTTPException(409, 'store_driver_has_deliveries')
        drivers[uid] = {'id': uid, 'name': str(user.get('name') or 'Repartidor')[:100], 'email': user['email'], 'active': body.active}
        s.audit(state, actor, 'driver_access_updated', {'driver_id': uid, 'active': body.active})
        return {'ok': True}
    return await s.mutate(operation)


@router.post('/admin/store/orders/{oid}/assignment')
async def assign(oid: str, body: Assignment, request: Request):
    actor = s.identity(await s.auth_admin(request))
    if body.eta.tzinfo is None or body.eta <= datetime.now(timezone.utc):
        raise HTTPException(400, 'store_eta_invalid')
    if not await active_account(body.driver_id):
        raise HTTPException(400, 'store_driver_account_unavailable')
    def operation(state):
        allowed_driver(state, body.driver_id)
        order = state['orders'].get(oid)
        if not order:
            raise HTTPException(404, 'store_order_not_found')
        if order['fulfillment'] != 'delivery' or order['status'] in ('delivered', 'cancelled') or order.get('tracking', {}).get('handoff_at'):
            raise HTTPException(409, 'store_transition_invalid')
        if order.get('payment_review_required') or order.get('refund_attempt') or (order.get('payment_attempt') and order['payment_status'] != 'paid'):
            raise HTTPException(409, 'store_payment_required')
        if order.get('driver_id', '') != body.expected_driver_id:
            raise HTTPException(409, 'store_assignment_changed')
        order['driver_id'] = body.driver_id
        tracking = order.setdefault('tracking', {})
        tracking.update(driver_name=state['drivers'][body.driver_id]['name'].split(' ')[0], eta=body.eta.astimezone(timezone.utc).isoformat(), assigned_at=s.now())
        order['updated_at'] = s.now()
        s.audit(state, actor, 'driver_assigned', {'order_id': oid, 'driver_id': body.driver_id, 'eta': tracking['eta']})
        return s.order_public(order)
    return await s.mutate(operation)


@router.get('/store/driver/access')
async def driver_access(request: Request):
    user = await s.auth_marketplace(request); uid = s.identity(user)
    state = await s.read_state()
    return {'enabled': bool(state.get('drivers', {}).get(uid, {}).get('active') and await active_account(uid))}


@router.get('/store/driver/orders')
async def deliveries(request: Request, response: Response):
    uid = await courier(request); state = await s.read_state(); allowed_driver(state, uid)
    response.headers['Cache-Control'] = 'private, no-store'
    orders = [driver_order(o) for o in state['orders'].values() if o.get('driver_id') == uid and o['status'] not in ('delivered', 'cancelled')]
    return {'orders': sorted(orders, key=lambda o: o.get('tracking', {}).get('eta', o['created_at']))}


@router.post('/store/driver/orders/{oid}/action')
async def delivery_action(oid: str, body: DriverAction, request: Request):
    uid = await courier(request)
    def operation(state):
        allowed_driver(state, uid)
        order = state['orders'].get(oid)
        if not order or order.get('driver_id') != uid:
            raise HTTPException(404, 'store_order_not_found')
        tracking = order.setdefault('tracking', {})
        if body.action == 'handoff' and tracking.get('handoff_at'):
            return driver_order(order)
        if order['status'] in ('cancelled', 'delivered'):
            raise HTTPException(409, 'store_transition_invalid')
        if body.action in ('depart', 'handoff') and (order.get('payment_review_required') or order.get('refund_attempt') or (order.get('payment_attempt') and order['payment_status'] != 'paid')):
            raise HTTPException(409, 'store_payment_required')
        if body.action == 'depart':
            if order['status'] not in ('ready', 'out_for_delivery'):
                raise HTTPException(409, 'store_transition_invalid')
            s.change_status(state, order, 'out_for_delivery', uid)
        elif body.action == 'handoff':
            if order['status'] != 'out_for_delivery':
                raise HTTPException(409, 'store_transition_invalid')
            tracking['handoff_at'] = s.now()
            s.audit(state, uid, 'delivery_handoff', oid, {'order_id': oid, 'user_id': order['user_id'], 'status': 'handoff'})
            if order['payment_status'] == 'paid':
                s.change_status(state, order, 'delivered', uid)
        else:
            if not body.note.strip():
                raise HTTPException(400, 'store_delivery_note_required')
            tracking['issue'] = body.note
            tracking['issue_at'] = s.now()
            s.audit(state, uid, 'delivery_issue', {'order_id': oid, 'note': body.note})
        order['updated_at'] = s.now()
        return driver_order(order)
    return await s.mutate(operation)
