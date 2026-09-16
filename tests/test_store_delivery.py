import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
import pytest
from test_resident_store import shop, setup, order, checkout
from rental import resident_store as s, store_delivery as d, tenant_integrity as ti

@pytest.mark.asyncio
async def test_home_delivery_rejects_pickup_tampering_and_preserves_retry(shop, monkeypatch):
    db, app = shop; await setup(db)
    await db.resident_store.update_one({'_id': s.KEY}, {'$set': {'settings.home_delivery_only': True}})
    home = {'address': '123 Test Street', 'zip': '79029', 'property_id': 'property-1', 'unit_id': '', 'contract_id': 'lease-1'}
    resolver = AsyncMock(return_value=home); monkeypatch.setattr(d, 'home_for', resolver)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        assert (await c.post('/store/quote', json=checkout(fulfillment='pickup'))).status_code == 400
        assert (await c.post('/store/quote', json=checkout(address='999 Another Home'))).status_code == 409
        payload = checkout(delivery_instructions='Front door')
        q = (await c.post('/store/quote', json=payload)).json()
        payload.update(quote_hash=q['quote_hash'], idempotency_key='same-home-order-123')
        result = await c.post('/store/orders', json=payload); assert result.status_code == 200
        assert 'residence' not in result.json()
        resolver.return_value = None
        assert (await c.post('/store/orders', json=payload)).json()['id'] == result.json()['id']
        assert (await c.post('/store/quote', json=checkout())).json()['detail'] == 'store_home_unavailable'
        assert (await c.get('/store/catalog')).json()['residence'] is None

@pytest.mark.asyncio
async def test_residence_requires_canonical_unit_link(shop, monkeypatch):
    db, _ = shop
    pid, uid = ObjectId(), ObjectId()
    monkeypatch.setattr(ti, 'resolve_authenticated_tenant', AsyncMock(return_value={'_id': ObjectId()}))
    contract = {'_id': ObjectId(), 'property_id': str(pid), 'unit_id': str(uid)}
    monkeypatch.setattr(ti, 'find_active_contract_for_tenant', AsyncMock(return_value=contract))
    await db.properties.insert_one({'_id': pid, 'address': '123 Example Street', 'city': 'Dumas', 'state': 'TX', 'zip_code': '79029'})
    await db.property_units.insert_one({'_id': uid, 'property_id': 'wrong', 'unit_name': 'Apt 2'})
    assert await d.home_for({'id': 'resident'}) is None
    await db.property_units.update_one({'_id': uid}, {'$set': {'property_id': str(pid)}})
    home = await d.home_for({'id': 'resident'})
    assert home['address'] == '123 Example Street, Apt 2, Dumas, TX' and home['zip'] == '79029'

@pytest.mark.asyncio
async def test_driver_assignment_privacy_handoff_payment_and_revocation(shop, monkeypatch):
    db, app = shop; await setup(db)
    driver, other = ObjectId(), ObjectId()
    await db.app_users.insert_many([{'_id': driver, 'email': 'driver@test.invalid', 'name': 'Driver One', 'role': 'maintenance'}, {'_id': other, 'email': 'other@test.invalid', 'name': 'Other'}])
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        oid = (await order(c)).json()['id']
        assert (await c.put('/admin/store/drivers', json={'email': 'driver@test.invalid'})).status_code == 200
        eta = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        assert (await c.post(f'/admin/store/orders/{oid}/assignment', json={'driver_id': str(driver), 'eta': eta})).status_code == 200
        assert (await c.post(f'/admin/store/orders/{oid}/assignment', json={'driver_id': str(driver), 'eta': eta})).status_code == 409
        assert (await c.put('/admin/store/drivers', json={'email': 'driver@test.invalid', 'active': False})).status_code == 409
        for status in ('preparing', 'ready'):
            assert (await c.post(f'/admin/store/orders/{oid}/status', json={'status': status})).status_code == 200
        monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'id': str(other), 'role': 'maintenance'}))
        assert (await c.get('/store/driver/orders')).status_code == 403
        monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'id': str(driver), 'role': 'maintenance'}))
        response = await c.get('/store/driver/orders'); assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-store'
        assert not {'user_id','cost_cents','fingerprint','residence','payment_reference'} & set(response.json()['orders'][0])
        assert (await c.get('/store/catalog')).status_code == 403
        assert (await c.post(f'/store/driver/orders/{oid}/action', json={'action':'handoff'})).status_code == 409
        assert (await c.post(f'/store/driver/orders/{oid}/action', json={'action':'depart'})).status_code == 200
        assert (await c.post(f'/store/driver/orders/{oid}/action', json={'action':'issue','note':'Gate closed'})).status_code == 200
        results = await asyncio.gather(*[c.post(f'/store/driver/orders/{oid}/action', json={'action':'handoff'}) for _ in range(3)])
        assert all(r.status_code == 200 for r in results)
        state = await s.read_state(); assert state['orders'][oid]['payment_status'] == 'unpaid'
        assert sum(a['action']=='delivery_handoff' for a in state['audit']) == 1
        assert (await c.post(f'/admin/store/orders/{oid}/status', json={'status':'delivered'})).status_code == 409
        paid = await c.post(f'/admin/store/orders/{oid}/payment', json={'reference':'Cash verified 01'})
        assert paid.status_code == 200 and paid.json()['status'] == 'delivered'
        assert (await c.get('/store/driver/orders')).json()['orders'] == []
        await db.app_users.update_one({'_id': driver}, {'$set': {'status': 'suspended'}})
        assert (await c.get('/store/driver/orders')).status_code == 403
        assert (await c.get('/store/driver/access')).json()['enabled'] is False
        assert (await s.read_state())['products']['water']['stock'] == 2
