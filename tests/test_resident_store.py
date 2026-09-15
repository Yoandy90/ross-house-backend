import asyncio
import copy
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient
from rental import resident_store as s

@pytest.fixture
def shop(monkeypatch):
    db=AsyncMongoMockClient()['store_test']
    monkeypatch.setattr(s,'get_db',lambda:db)
    monkeypatch.setattr(s,'auth_marketplace',AsyncMock(return_value={'_id':'resident-1','role':'tenant','name':'Resident'}))
    monkeypatch.setattr(s,'auth_admin',AsyncMock(return_value={'_id':'admin-1'}))
    app=FastAPI();app.include_router(s.router)
    return db, app

async def setup(db,stock=3):
    state=await s.read_state()
    state['settings'].update(enabled=True,slots=['Tuesday 5–7'],delivery_zips=['79029'],pickup_address='Office, Dumas',delivery_fee_cents=200,delivery_tax_bps=825)
    state['products']['water']={'name':'Agua','name_en':'Water','description':'','description_en':'','category':'Bebidas','image_url':'','price_cents':199,'cost_cents':100,'tax_bps':825,'stock':stock,'active':True}
    await db.resident_store.replace_one({'_id':s.KEY},state)
    return state

def checkout(**overrides):
    return {'items':[{'product_id':'water','quantity':1}],'fulfillment':'delivery','address':'123 Test Street','zip':'79029','slot':'Tuesday 5–7',**overrides}

async def order(client,key='request-key-123456',**overrides):
    body=checkout(**overrides)
    q=await client.post('/store/quote',json=body)
    assert q.status_code==200,q.text
    return await client.post('/store/orders',json={**body,'quote_hash':q.json()['quote_hash'],'idempotency_key':key})

@pytest.mark.asyncio
async def test_store_starts_closed_and_hides_cost(shop):
    db,app=shop
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        r=await c.get('/store/catalog');assert not r.json()['settings']['enabled']
        assert (await c.post('/store/quote',json=checkout())).status_code==409
        await setup(db)
        assert 'cost_cents' not in (await c.get('/store/catalog')).json()['products'][0]

@pytest.mark.asyncio
async def test_quote_server_price_and_tax_rounding(shop):
    db,app=shop;await setup(db)
    q=s.quote(await s.read_state(),s.Checkout(**checkout()))
    assert q['subtotal_cents']==199 and q['tax_cents']==33 and q['total_cents']==432
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.post('/store/quote',json=checkout(total_cents=1))).status_code==422

@pytest.mark.asyncio
async def test_duplicate_retry_single_reservation(shop):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        body=checkout();q=(await c.post('/store/quote',json=body)).json()
        payload={**body,'quote_hash':q['quote_hash'],'idempotency_key':'same-request-key-123'}
        results=await asyncio.gather(*[c.post('/store/orders',json=payload) for _ in range(8)])
        assert all(r.status_code==200 for r in results)
        assert len({r.json()['id'] for r in results})==1
        state=await s.read_state();assert state['products']['water']['stock']==2 and len(state['orders'])==1

@pytest.mark.asyncio
async def test_last_unit_no_oversell(shop):
    db,app=shop;await setup(db,stock=1)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        body=checkout();q=(await c.post('/store/quote',json=body)).json()
        results=await asyncio.gather(*[c.post('/store/orders',json={**body,'quote_hash':q['quote_hash'],'idempotency_key':f'concurrent-order-{i}'}) for i in range(6)])
        assert sum(r.status_code==200 for r in results)==1
        assert (await s.read_state())['products']['water']['stock']==0

@pytest.mark.asyncio
async def test_cas_conflict_retries_without_double_stock(shop,monkeypatch):
    db,app=shop;await setup(db)
    collection=db.resident_store;original=collection.replace_one;attempts=[]
    async def conflict_once(query,doc,*args,**kwargs):
        attempts.append(1)
        if len(attempts)==1:
            await collection.update_one({'_id':s.KEY},{'$inc':{'revision':1}})
        return await original(query,doc,*args,**kwargs)
    monkeypatch.setattr(collection,'replace_one',conflict_once)
    from types import SimpleNamespace
    monkeypatch.setattr(s,'get_db',lambda:SimpleNamespace(resident_store=collection))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        r=await order(c);assert r.status_code==200
    assert len(attempts)==2 and (await s.read_state())['products']['water']['stock']==2

@pytest.mark.asyncio
async def test_changed_price_requires_new_review(shop):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        body=checkout();q=(await c.post('/store/quote',json=body)).json()
        await db.resident_store.update_one({'_id':s.KEY},{'$set':{'products.water.price_cents':200}})
        r=await c.post('/store/orders',json={**body,'quote_hash':q['quote_hash'],'idempotency_key':'stale-request-key-123'})
        assert r.status_code==409 and r.json()['detail']=='store_quote_changed'
        assert not (await s.read_state())['orders']

@pytest.mark.asyncio
async def test_cancel_twice_restock_once(shop):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c)).json()['id']
        for _ in range(2):assert (await c.post(f'/store/orders/{oid}/cancel')).status_code==200
        assert (await s.read_state())['products']['water']['stock']==3

@pytest.mark.asyncio
async def test_ownership_and_role_authorization(shop,monkeypatch):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c)).json()['id']
        monkeypatch.setattr(s,'auth_marketplace',AsyncMock(return_value={'_id':'other','role':'tenant'}))
        assert (await c.get('/store/orders')).json()['orders']==[]
        assert (await c.post(f'/store/orders/{oid}/cancel')).status_code==404
        for role in ('guest','buyer','maintenance','landlord'):
            monkeypatch.setattr(s,'auth_marketplace',AsyncMock(return_value={'_id':'other','role':role}))
            assert (await c.get('/store/catalog')).status_code==403

@pytest.mark.asyncio
async def test_real_auth_rejects_missing_credentials(shop,monkeypatch):
    from rental.shared import auth_admin,auth_marketplace
    db,app=shop;monkeypatch.setattr(s,'auth_admin',auth_admin);monkeypatch.setattr(s,'auth_marketplace',auth_marketplace)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.get('/store/catalog')).status_code==401
        assert (await c.get('/admin/store')).status_code==401

@pytest.mark.asyncio
async def test_pay_delivery_and_accounting_isolation(shop):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c)).json()['id']
        for status in ('preparing','ready'):
            assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':status})).status_code==200
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'delivered'})).status_code==409
        for _ in range(2):assert (await c.post(f'/admin/store/orders/{oid}/payment',json={'reference':'Cash receipt 001'})).status_code==200
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'cancelled'})).status_code==409
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'delivered'})).status_code==200
        assert (await c.get('/admin/store')).json()['summary']['collected_cents']==432
        state=await s.read_state();assert sum(a['action']=='payment_recorded' for a in state['audit'])==1
        for name in ('payments','tenant_payments','rental_payments','invoices'):
            assert await db[name].count_documents({})==0

@pytest.mark.asyncio
async def test_admin_stale_stock_edit_cannot_undo_reservation(shop):
    db,app=shop;original=await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        await order(c)
        r=await c.put('/admin/store/products/water',json={'revision':original['revision'],'product':original['products']['water']})
        assert r.status_code==409 and (await s.read_state())['products']['water']['stock']==2

@pytest.mark.asyncio
async def test_capacity_fails_without_order_or_stock_mutation(shop,monkeypatch):
    db,app=shop;await setup(db);monkeypatch.setattr(s,'MAX_BYTES',100)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        assert (await order(c)).status_code==409
    state=await s.read_state();assert not state['orders'] and state['products']['water']['stock']==3

@pytest.mark.asyncio
@pytest.mark.parametrize('overrides,status',[
    ({'zip':'00000'},400),({'slot':'invalid'},409),({'address':''},400),
    ({'items':[{'product_id':'water','quantity':0}]},422),
    ({'items':[{'product_id':'water','quantity':True}]},422),
    ({'items':[{'product_id':'water','quantity':2},{'product_id':'water','quantity':2}]},409),
    ({'items':[{'product_id':'missing','quantity':1}]},409),
])
async def test_invalid_checkout(shop,overrides,status):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.post('/store/quote',json=checkout(**overrides))).status_code==status

@pytest.mark.asyncio
async def test_pickup_and_minimum(shop):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        q=(await c.post('/store/quote',json=checkout(fulfillment='pickup',address='arbitrary'))).json()
        assert q['address']=='Office, Dumas' and q['delivery_fee_cents']==0 and q['tax_cents']==16
        await db.resident_store.update_one({'_id':s.KEY},{'$set':{'settings.minimum_cents':1000}})
        assert (await c.post('/store/quote',json=checkout())).status_code==400

@pytest.mark.asyncio
async def test_retry_after_store_closed_still_returns_original_order(shop):
    db,app=shop;await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        body=checkout();q=(await c.post('/store/quote',json=body)).json();payload={**body,'quote_hash':q['quote_hash'],'idempotency_key':'closed-retry-key-123'}
        original=await c.post('/store/orders',json=payload)
        await db.resident_store.update_one({'_id':s.KEY},{'$set':{'settings.enabled':False}})
        retry=await c.post('/store/orders',json=payload)
        assert retry.status_code==200 and retry.json()['id']==original.json()['id']
        conflict=await c.post('/store/orders',json={**payload,'address':'Changed address'})
        assert conflict.status_code==409
