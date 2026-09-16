from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from bson import ObjectId
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient
from rental import property_inquiries as q, property_inquiry_notices as n

@pytest.fixture
def state(monkeypatch):
    db=AsyncMongoMockClient()['inquiries'];uid=ObjectId();pid=ObjectId()
    monkeypatch.setattr(q,'get_db',lambda:db)
    monkeypatch.setattr(q,'auth_marketplace',AsyncMock(return_value={'_id':str(uid)}))
    monkeypatch.setattr(q,'auth_admin',AsyncMock(return_value={'_id':'admin'}))
    app=FastAPI();app.include_router(q.router)
    app.post('/public/property-inquiry')(q.create_inquiry)
    app.get('/admin/property-inquiries')(q.admin_list)
    return db,uid,pid,app

async def setup(state):
    db,uid,pid,app=state
    await db.properties.insert_one({'_id':pid,'address':'Test home','status':'available'})
    await db.app_users.insert_one({'_id':uid,'role':'tenant','email':'owner@example.invalid'})
    return {'property_id':str(pid),'name':'Test','email':'test@example.invalid','inquiry_type':'visit','request_id':'request-123456'}

@pytest.mark.asyncio
async def test_create_retry_is_one_inquiry_and_one_notice_per_audience(state):
    body=await setup(state);db,uid,pid,app=state
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test',headers={'Authorization':'Bearer test'}) as c:
        first=await c.post('/public/property-inquiry',json=body);assert first.status_code==200,first.text
        second=await c.post('/public/property-inquiry',json=body);assert second.json()['inquiry_id']==first.json()['inquiry_id']
        assert await db.property_inquiries.count_documents({})==1
        assert await db.rental_notifications.count_documents({})==2
        assert await db.property_inquiry_deliveries.count_documents({'status':'suppressed_environment'})==2
        changed=await c.post('/public/property-inquiry',json={**body,'message':'changed'});assert changed.status_code==409

@pytest.mark.asyncio
async def test_owner_scope_does_not_link_by_email(state,monkeypatch):
    body=await setup(state);db,uid,pid,app=state
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
        assert (await c.post('/public/property-inquiry',json=body)).status_code==200
        assert (await c.get('/marketplace/property-inquiries')).json()['inquiries']==[]
        assert (await c.post('/public/property-inquiry',headers={'Authorization':'Bearer test'},json={**body,'request_id':'authenticated'})).status_code==200
        assert len((await c.get('/marketplace/property-inquiries')).json()['inquiries'])==1
        monkeypatch.setattr(q,'auth_marketplace',AsyncMock(return_value={'_id':str(ObjectId())}))
        assert (await c.get('/marketplace/property-inquiries')).json()['inquiries']==[]

@pytest.mark.asyncio
async def test_validation_rejects_bad_email_missing_property_and_past_visit(state):
    body=await setup(state);db,uid,pid,app=state
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
        for bad in [{'email':'bad'},{'name':' '},{'property_id':str(ObjectId())},{'preferred_slot':{'date':'2020-01-01','time':'12:00'}},{'preferred_slot':{'date':'2030-99-99','time':'25:00'}}]:
            assert (await c.post('/public/property-inquiry',json={**body,**bad})).status_code in (400,404)
    assert await db.property_inquiries.count_documents({})==0

@pytest.mark.asyncio
async def test_confirm_visit_conflict_and_resolve(state):
    body=await setup(state);db,uid,pid,app=state
    future=(q.now()+timedelta(days=3)).strftime('%Y-%m-%d')
    slot={'date':future,'time':'14:30'}
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test',headers={'Authorization':'Bearer test'}) as c:
        created=(await c.post('/public/property-inquiry',json={**body,'preferred_slot':slot})).json()
        assert created['inquiry']['confirmed_visit'] is None
        url='/admin/property-inquiries/'+created['inquiry_id']
        bad=await c.patch(url,json={'version':1,'status':'visit_confirmed'});assert bad.status_code==400
        updated=await c.patch(url,json={'version':1,'status':'visit_confirmed','confirmed_slot':slot,'customer_note':'See you there'})
        assert updated.status_code==200,updated.text
        assert updated.json()['inquiry']['confirmed_visit']['timezone']=='America/Chicago'
        assert (await c.patch(url,json={'version':1,'status':'resolved'})).status_code==409
        mine=(await c.get('/marketplace/property-inquiries')).json()['inquiries'][0]
        assert mine['customer_note']=='See you there' and mine['status']=='visit_confirmed'
        assert (await c.patch(url,json={'version':2,'status':'resolved'})).status_code==200
        assert (await c.patch(url,json={'version':3,'status':'new'})).status_code==409

@pytest.mark.asyncio
async def test_non_visit_cannot_be_scheduled(state):
    body=await setup(state);app=state[-1]
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
        d=(await c.post('/public/property-inquiry',json={**body,'inquiry_type':'contact'})).json()
        r=await c.patch('/admin/property-inquiries/'+d['inquiry_id'],json={'version':1,'status':'visit_confirmed','confirmed_slot':{'date':'2030-01-01','time':'10:00'}})
        assert r.status_code==400

@pytest.mark.asyncio
async def test_admin_enriches_legacy_property_and_public_omits_internal_fields(state):
    body=await setup(state);db,uid,pid,app=state
    await db.property_inquiries.insert_one({'property_id':str(pid),'name':'Legacy','status':'new'})
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
        d=(await c.get('/admin/property-inquiries')).json()['inquiries'][0]
        assert d['property_title']=='Test home' and d['version']==0
    assert 'events' not in q.public({'_id':ObjectId(),'events':[1],'request_hash':'secret','user_id':str(uid)})

@pytest.mark.asyncio
async def test_staging_never_calls_outbound_providers(state,monkeypatch):
    db,uid,pid,app=state;await setup(state)
    recipient=AsyncMock();monkeypatch.setattr(n,'push_recipient',recipient)
    await db.property_inquiry_deliveries.insert_one({'_id':'pending-test','status':'pending'})
    await n.deliver(db,{'_id':'pending-test'})
    recipient.assert_not_called()
    assert (await db.property_inquiry_deliveries.find_one({'_id':'pending-test'}))['status']=='suppressed_environment'

@pytest.mark.asyncio
async def test_denied_admin_does_not_mutate(state,monkeypatch):
    from fastapi import HTTPException
    body=await setup(state);db,uid,pid,app=state
    monkeypatch.setattr(q,'auth_admin',AsyncMock(side_effect=HTTPException(403,'Denied')))
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
        assert (await c.patch('/admin/property-inquiries/'+str(ObjectId()),json={'version':0,'status':'resolved'})).status_code==403
    assert await db.property_inquiries.count_documents({})==0

@pytest.mark.asyncio
async def test_push_claim_is_single_attempt_and_uses_customer_language(state,monkeypatch):
    import asyncio
    from rental import notification_center as center
    body=await setup(state);db,uid,pid,app=state
    await db.app_users.update_one({'_id':uid},{'$set':{'push_token':'ExponentPushToken[test]'}})
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test',headers={'Authorization':'Bearer test'}) as c:
        await c.post('/public/property-inquiry',json={**body,'language':'en'})
    item=await db.property_inquiry_deliveries.find_one({'channel':'push'})
    await db.property_inquiry_deliveries.update_one({'_id':item['_id']},{'$set':{'status':'pending'}})
    monkeypatch.setattr(n,'should_disable_background_jobs',lambda:False)
    sender=AsyncMock(return_value={'status':'accepted'});monkeypatch.setattr(center,'expo_send',sender)
    await asyncio.gather(n.deliver(db,item),n.deliver(db,item))
    sender.assert_awaited_once();assert sender.call_args.args[1]=='Request received'
    assert (await db.property_inquiry_deliveries.find_one({'_id':item['_id']}))['status']=='accepted'

@pytest.mark.asyncio
async def test_stale_delivery_is_not_sent(state,monkeypatch):
    body=await setup(state);db,uid,pid,app=state
    async with AsyncClient(transport=ASGITransport(app),base_url='http://test',headers={'Authorization':'Bearer test'}) as c:
        created=(await c.post('/public/property-inquiry',json=body)).json()
        await c.patch('/admin/property-inquiries/'+created['inquiry_id'],json={'version':1,'status':'following_up'})
    item=await db.property_inquiry_deliveries.find_one({'channel':'push','version':1})
    await db.property_inquiry_deliveries.update_one({'_id':item['_id']},{'$set':{'status':'pending'}})
    monkeypatch.setattr(n,'should_disable_background_jobs',lambda:False)
    await n.deliver(db,item)
    assert (await db.property_inquiry_deliveries.find_one({'_id':item['_id']}))['status']=='superseded'
