"""Order-scoped live location. Latest point only, short TTL, no route history.

The aggregate owns consent/assignment. Location writes are separate from stock
and money; every read rechecks the aggregate, so racing writes cannot resurrect
a stopped, reassigned, or completed delivery.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, AwareDatetime
from pymongo.errors import DuplicateKeyError
from rental import resident_store as s, store_delivery as d
from rental.store_routing import road_route

router = APIRouter()
FRESH_SECONDS = 30
RETAIN_SECONDS = 300
SESSION_HOURS = 4


def authenticated_claims(request):
    # Called only after courier() authenticated the same bearer token.
    from rental.shared import TENANT_JWT_SECRET
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    try:
        return jwt.decode(token, TENANT_JWT_SECRET, algorithms=['HS256'], options={'require': ['exp']})
    except jwt.InvalidTokenError:
        raise HTTPException(401, 'session_invalid')


async def session_alive(live, uid):
    sid = live.get('sid')
    if not sid:
        return date(live.get('auth_expires_at')) > utcnow()
    session = await s.get_db().auth_sessions.find_one({'sid': sid})
    return bool(session and str(session.get('user_id')) == uid
                and not session.get('revoked_at')
                and date(session.get('expires_at')) > utcnow())


def utcnow():
    return datetime.now(timezone.utc)


def date(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


class Point(s.Strict):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Start(s.Strict):
    destination: Point | None = None


class Session(s.Strict):
    session_id: str = Field(min_length=36, max_length=36)


class Position(Session, Point):
    captured_at: AwareDatetime
    accuracy: float = Field(ge=0, le=100, allow_inf_nan=False)
    heading: float | None = Field(default=None, ge=0, lt=360, allow_inf_nan=False)


def active(order):
    return bool(order and order.get('fulfillment') == 'delivery'
                and order.get('status') == 'out_for_delivery'
                and not order.get('tracking', {}).get('handoff_at'))


def assigned(state, oid, uid):
    d.allowed_driver(state, uid)
    order = state['orders'].get(oid)
    if not order or order.get('driver_id') != uid:
        raise HTTPException(404, 'store_order_not_found')
    return order


def valid_session(order, uid, session_id=None):
    live = order.get('_live', {})
    return bool(active(order) and live.get('driver_id') == uid
                and date(live.get('expires_at')) > utcnow()
                and (session_id is None or live.get('id') == session_id))


async def purge(ids):
    if ids:
        await s.get_db().store_live_locations.delete_many({'_id': {'$in': ids}})


@router.post('/store/driver/orders/{oid}/tracking/start')
async def start(oid: str, body: Start, request: Request, response: Response):
    uid = await d.courier(request)
    claims = authenticated_claims(request)
    # No GPS coordinates in aggregate/audit. TTL removes the latest point even
    # if the phone dies; session expiration bounds abandoned consent.
    await s.get_db().store_live_locations.create_index('expires_at', expireAfterSeconds=0)
    session_id = str(uuid4())
    def operation(state):
        order = assigned(state, oid, uid)
        if not active(order):
            raise HTTPException(409, 'store_tracking_requires_departure')
        removed = []
        for other in state['orders'].values():
            if other.get('_live', {}).get('driver_id') == uid:
                removed.append(other.pop('_live')['id'])
        live = {'id': session_id, 'driver_id': uid, 'sid': claims.get('sid'),
                'auth_expires_at': datetime.fromtimestamp(claims['exp'], timezone.utc).isoformat(),
                'expires_at': (utcnow() + timedelta(hours=SESSION_HOURS)).isoformat()}
        order['_live'] = live
        s.audit(state, uid, 'delivery_tracking_started', oid)
        return {'session_id': session_id, 'expires_at': live['expires_at'], '_removed': removed}
    result = await s.mutate(operation)
    await purge(result.pop('_removed'))
    if body.destination:
        await s.get_db().store_live_locations.update_one({'_id': session_id}, {'$set': {
            'destination': body.destination.model_dump(),
            'expires_at': utcnow() + timedelta(seconds=RETAIN_SECONDS)}}, upsert=True)
    response.headers['Cache-Control'] = 'private, no-store'
    return result


@router.post('/store/driver/orders/{oid}/tracking/position')
async def position(oid: str, body: Position, request: Request, response: Response):
    uid = await d.courier(request)
    state = await s.read_state()
    order = assigned(state, oid, uid)
    if not valid_session(order, uid, body.session_id):
        raise HTTPException(409, 'store_tracking_stopped')
    now = utcnow()
    age = (now - body.captured_at).total_seconds()
    if age < -10 or age > FRESH_SECONDS:
        raise HTTPException(422, 'store_location_too_old')
    collection = s.get_db().store_live_locations
    # Do not let delayed/out-of-order samples overwrite newer GPS fixes.
    try:
        await collection.update_one({'_id': body.session_id, '$and': [
            {'$or': [{'captured_at': {'$lt': body.captured_at}}, {'captured_at': {'$exists': False}}]},
            {'$or': [{'received_at': {'$lte': now - timedelta(seconds=3)}}, {'received_at': {'$exists': False}}]},
        ]},
            {'$set': {'latitude': body.latitude, 'longitude': body.longitude,
                      'accuracy': body.accuracy, 'heading': body.heading,
                      'captured_at': body.captured_at, 'received_at': now,
                      'expires_at': now + timedelta(seconds=RETAIN_SECONDS)}}, upsert=True)
    except DuplicateKeyError:
        pass  # Equal/older sample; idempotent acknowledgement, no overwrite.
    response.headers['Cache-Control'] = 'private, no-store'
    return {'ok': True}


@router.post('/store/driver/orders/{oid}/tracking/stop')
async def stop(oid: str, body: Session, request: Request):
    uid = await d.courier(request)
    def operation(state):
        order = assigned(state, oid, uid)
        removed = []
        if order.get('_live', {}).get('id') == body.session_id:
            order.pop('_live', None)
            removed.append(body.session_id)
            s.audit(state, uid, 'delivery_tracking_stopped', oid)
        return {'ok': True, '_removed': removed}
    result = await s.mutate(operation)
    await purge(result.pop('_removed'))
    return result


async def snapshot(order, state):
    uid = order.get('driver_id', '')
    result = {'order_id': order['id'], 'order_status': order['status'],
              'handoff_at': order.get('tracking', {}).get('handoff_at'),
              'eta': order.get('tracking', {}).get('eta'), 'state': 'inactive',
              'position': None, 'destination': None, 'server_time': utcnow().isoformat()}
    if (not valid_session(order, uid) or not state.get('drivers', {}).get(uid, {}).get('active')
            or not await d.active_account(uid)
            or not await session_alive(order.get('_live', {}), uid)):
        return result
    doc = await s.get_db().store_live_locations.find_one({'_id': order['_live']['id']})
    result['state'] = 'waiting'
    if not doc or date(doc.get('expires_at')) <= utcnow():
        return result
    result['destination'] = doc.get('destination')
    if not doc.get('captured_at'):
        return result
    age = max(0, (utcnow() - date(doc['captured_at'])).total_seconds())
    if age >= RETAIN_SECONDS:
        return result
    result['state'] = 'live' if age <= FRESH_SECONDS else 'stale'
    result['position'] = {k: doc[k] for k in ('latitude', 'longitude', 'accuracy', 'heading')}
    result['position'].update(captured_at=date(doc['captured_at']).isoformat(), age_seconds=age)
    # Road geometry is ephemeral and best-effort. Never persist route history or
    # make live GPS visibility depend on the external routing provider.
    if result['destination']:
        result['route'] = await road_route(result['position'], result['destination'])
    return result


@router.get('/store/driver/orders/{oid}/tracking/live')
async def driver_tracking(oid: str, request: Request, response: Response):
    uid = await d.courier(request)
    state = await s.read_state()
    order = assigned(state, oid, uid)
    response.headers['Cache-Control'] = 'private, no-store'
    return await snapshot(order, state)


@router.get('/store/orders/{oid}/tracking/live')
async def customer_tracking(oid: str, request: Request, response: Response):
    user = await s.auth_marketplace(request)
    if user.get('status') in d.INACTIVE or user.get('active') is False or user.get('is_active') is False:
        raise HTTPException(403, 'store_tracking_access_required')
    state = await s.read_state()
    order = state['orders'].get(oid)
    if not order or order.get('user_id') != s.identity(user):
        raise HTTPException(404, 'store_order_not_found')
    response.headers['Cache-Control'] = 'private, no-store'
    return await snapshot(order, state)


@router.get('/admin/store/orders/{oid}/tracking/live')
async def admin_tracking(oid: str, request: Request, response: Response):
    await s.auth_admin(request)
    state = await s.read_state()
    order = state['orders'].get(oid)
    if not order:
        raise HTTPException(404, 'store_order_not_found')
    response.headers['Cache-Control'] = 'private, no-store'
    return await snapshot(order, state)
