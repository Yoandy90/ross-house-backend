from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
import pytest
import pytest_asyncio
from test_resident_store import shop, setup, order
from rental import resident_store as s, store_live_tracking as live


@pytest_asyncio.fixture
async def delivery(shop, monkeypatch):
    db, app = shop
    await setup(db, stock=20)
    uid = str(ObjectId())
    await db.app_users.insert_one({'_id': ObjectId(uid), 'name': 'QA Driver', 'email': 'qa@example.invalid'})
    monkeypatch.setattr(live, 'authenticated_claims', lambda _: {'exp': (live.utcnow() + timedelta(hours=4)).timestamp()})
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        oid = (await order(c)).json()['id']
        await db.resident_store.update_one({'_id': s.KEY}, {'$set': {
            f'drivers.{uid}': {'active': True}, f'orders.{oid}.driver_id': uid,
            f'orders.{oid}.status': 'out_for_delivery'}})
        monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'id': uid}))
        yield db, c, oid, uid


async def start(c, oid):
    r = await c.post(f'/store/driver/orders/{oid}/tracking/start', json={'destination': {'latitude': 35.8, 'longitude': -101.97}})
    assert r.status_code == 200, r.text
    return r.json()['session_id']


def fix(sid, **kw):
    return {'session_id': sid, 'latitude': 35.85, 'longitude': -101.96, 'accuracy': 8,
            'heading': 90, 'captured_at': live.utcnow().isoformat(), **kw}


async def as_customer(c, oid, monkeypatch, user='resident-1'):
    monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'id': user}))
    return await c.get(f'/store/orders/{oid}/tracking/live')


@pytest.mark.asyncio
async def test_live_position_owner_only_no_private_data_in_regular_orders(delivery, monkeypatch):
    db, c, oid, uid = delivery
    sid = await start(c, oid)
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(sid))).status_code == 200
    listed = await c.get('/store/driver/orders')
    assert '_live' not in listed.text and sid not in listed.text and 'latitude' not in listed.text
    assert (await as_customer(c, oid, monkeypatch, 'other-tenant')).status_code == 404
    assert (await as_customer(c, oid, monkeypatch, uid)).status_code == 404
    result = await as_customer(c, oid, monkeypatch)
    assert result.headers['cache-control'] == 'private, no-store'
    assert result.json()['state'] == 'live'
    assert result.json()['position']['latitude'] == 35.85
    assert sid not in result.text and uid not in result.text
    state = await s.read_state()
    assert '_live' not in s.order_public(state['orders'][oid])
    assert 'latitude' not in str(state['audit'])
    assert (await c.get(f'/admin/store/orders/{oid}/tracking/live')).status_code == 200


@pytest.mark.asyncio
async def test_stale_then_expired_without_replaying_or_overwriting_newer_sample(delivery, monkeypatch):
    db, c, oid, uid = delivery
    sid = await start(c, oid)
    newer = fix(sid)
    await c.post(f'/store/driver/orders/{oid}/tracking/position', json=newer)
    older = fix(sid, latitude=10, captured_at=(live.utcnow() - timedelta(seconds=10)).isoformat())
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/position', json=older)).status_code == 200
    assert (await db.store_live_locations.find_one({'_id': sid}))['latitude'] == 35.85
    await db.store_live_locations.update_one({'_id': sid}, {'$set': {'captured_at': live.utcnow() - timedelta(seconds=45)}})
    assert (await as_customer(c, oid, monkeypatch)).json()['state'] == 'stale'
    await db.store_live_locations.update_one({'_id': sid}, {'$set': {'captured_at': live.utcnow() - timedelta(seconds=301)}})
    assert (await as_customer(c, oid, monkeypatch)).json()['position'] is None
    index = await db.store_live_locations.index_information()
    assert any(i.get('expireAfterSeconds') == 0 for i in index.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('override', [
    {'latitude': 91}, {'longitude': -181}, {'accuracy': 101}, {'accuracy': -1},
    {'captured_at': '2020-01-01T00:00:00Z'}, {'captured_at': '2099-01-01T00:00:00Z'},
    {'captured_at': '2026-09-19T00:00:00'}, {'heading': 360},
    {'user_id': 'another-driver'}, {'latitude': 'NaN'},
])
async def test_rejects_bad_location_and_identity_injection(delivery, override):
    db, c, oid, uid = delivery
    sid = await start(c, oid)
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(sid, **override))).status_code == 422


@pytest.mark.asyncio
async def test_start_requires_active_assigned_courier_and_departed_order(delivery, monkeypatch):
    db, c, oid, uid = delivery
    for status in ['received', 'preparing', 'ready', 'delivered', 'cancelled']:
        await db.resident_store.update_one({'_id': s.KEY}, {'$set': {f'orders.{oid}.status': status}})
        assert (await c.post(f'/store/driver/orders/{oid}/tracking/start', json={})).status_code == 409
    monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'id': str(ObjectId())}))
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/start', json={})).status_code == 403


@pytest.mark.asyncio
async def test_switching_delivery_revokes_previous_customer(delivery, monkeypatch):
    db, c, oid, uid = delivery
    sid = await start(c, oid)
    await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(sid))
    state = await s.read_state()
    other = dict(state['orders'][oid], id='order-2', user_id='resident-2'); other.pop('_live')
    await db.resident_store.update_one({'_id': s.KEY}, {'$set': {'orders.order-2': other}})
    second = await start(c, 'order-2')
    assert second != sid
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(sid))).status_code == 409
    assert (await as_customer(c, oid, monkeypatch)).json()['position'] is None
    assert await db.store_live_locations.find_one({'_id': sid}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('end', ['stop', 'handoff', 'reassignment', 'suspended', 'session_revoked', 'expired'])
async def test_stop_completion_revocation_and_racing_ping_never_expose_location(delivery, monkeypatch, end):
    db, c, oid, uid = delivery
    if end == 'session_revoked':
        monkeypatch.setattr(live, 'authenticated_claims', lambda _: {'sid': 'a'*32, 'exp': (live.utcnow()+timedelta(hours=1)).timestamp()})
        await db.auth_sessions.insert_one({'sid': 'a'*32, 'user_id': uid, 'expires_at': live.utcnow()+timedelta(hours=1)})
    sid = await start(c, oid)
    await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(sid))
    if end == 'stop':
        assert (await c.post(f'/store/driver/orders/{oid}/tracking/stop', json={'session_id': sid})).status_code == 200
    elif end == 'handoff':
        assert (await c.post(f'/store/driver/orders/{oid}/action', json={'action': 'handoff'})).status_code == 200
        assert (await s.read_state())['orders'][oid]['payment_status'] == 'unpaid'
    elif end == 'reassignment':
        await db.resident_store.update_one({'_id': s.KEY}, {'$set': {f'orders.{oid}.driver_id': 'someone-else'}})
    elif end == 'suspended':
        await db.app_users.update_one({'_id': ObjectId(uid)}, {'$set': {'status': 'suspended'}})
    elif end == 'session_revoked':
        await db.auth_sessions.update_one({'sid': 'a'*32}, {'$set': {'revoked_at': live.utcnow()}})
    elif end == 'expired':
        await db.resident_store.update_one({'_id': s.KEY}, {'$set': {f'orders.{oid}._live.expires_at': '2020-01-01T00:00:00Z'}})
    # Model a GPS write that was already in flight when stop committed.
    await db.store_live_locations.update_one({'_id': sid}, {'$set': {**fix(sid), 'expires_at': live.utcnow()+timedelta(minutes=5)}}, upsert=True)
    result = await as_customer(c, oid, monkeypatch)
    assert result.json()['state'] == 'inactive' and result.json()['position'] is None


@pytest.mark.asyncio
async def test_late_stop_cannot_cancel_new_session(delivery):
    db, c, oid, uid = delivery
    old = await start(c, oid)
    current = await start(c, oid)
    await c.post(f'/store/driver/orders/{oid}/tracking/stop', json={'session_id': old})
    assert (await s.read_state())['orders'][oid]['_live']['id'] == current
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(current))).status_code == 200


@pytest.mark.asyncio
async def test_driver_live_snapshot_requires_same_assigned_driver(delivery, monkeypatch):
    db, c, oid, uid = delivery
    sid = await start(c, oid)
    assert (await c.post(f'/store/driver/orders/{oid}/tracking/position', json=fix(sid))).status_code == 200
    result = await c.get(f'/store/driver/orders/{oid}/tracking/live')
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'private, no-store'
    assert result.json()['state'] == 'live'
    assert result.json()['position']['latitude'] == 35.85
    other = str(ObjectId())
    await db.app_users.insert_one({'_id': ObjectId(other), 'name': 'Other Driver', 'email': 'other@example.invalid'})
    await db.resident_store.update_one({'_id': s.KEY}, {'$set': {f'drivers.{other}': {'active': True}}})
    monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'id': other}))
    assert (await c.get(f'/store/driver/orders/{oid}/tracking/live')).status_code == 404
