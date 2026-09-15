from datetime import timedelta
from unittest.mock import AsyncMock
import asyncio
import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient
from rental import notification_center as c
from rental.notification_groups import Audience, directory, resolve

@pytest.fixture
def state(monkeypatch):
    db=AsyncMongoMockClient()['center']
    monkeypatch.setattr(c,'get_db',lambda:db)
    monkeypatch.setattr(c,'auth_admin',AsyncMock(return_value={'_id':'admin-test'}))
    sender=AsyncMock(return_value={'status':'accepted','ticket_id':'ticket-test'})
    monkeypatch.setattr(c,'expo_send',sender)
    app=FastAPI();app.include_router(c.router)
    return db,sender,app

async def tenant(db,city='Dumas',kind='apartment',active=True,**kwargs):
    uid,tid,pid=ObjectId(),ObjectId(),ObjectId()
    await db.app_users.insert_one({'_id':uid,'role':'tenant','name':str(uid),**kwargs})
    await db.tenants.insert_one({'_id':tid,'app_user_id':str(uid)})
    await db.properties.insert_one({'_id':pid,'city':city,'type':kind})
    await db.rental_contracts.insert_one({'tenant_id':str(tid),'property_id':str(pid),'status':'active' if active else 'ended'})
    return str(uid)

async def worker(db,kind):
    uid,pid=ObjectId(),ObjectId()
    await db.app_users.insert_one({'_id':uid,'role':'maintenance','service_provider_id':str(pid)})
    await db.service_providers.insert_one({'_id':pid,'status':'active','app_user_id':str(uid),'worker_type':kind})
    return str(uid)

@pytest.mark.asyncio
async def test_dynamic_city_type_and_active_contract(state):
    db,_,_=state
    yes=await tenant(db);await tenant(db,city='Amarillo');await tenant(db,kind='house');await tenant(db,active=False)
    assert [u['id'] for u in await resolve(db,Audience(cities=['dumas'],property_types=['apartment']))]==[yes]

@pytest.mark.asyncio
async def test_filters_do_not_mix_different_properties(state):
    db,_,_=state; uid=await tenant(db,kind='house');tid=await db.tenants.find_one({'app_user_id':uid})
    p=ObjectId();await db.properties.insert_one({'_id':p,'type':'apartment','city':'Amarillo'})
    await db.rental_contracts.insert_one({'tenant_id':str(tid['_id']),'property_id':str(p),'status':'active'})
    assert not await resolve(db,Audience(cities=['Dumas'],property_types=['apartment']))

@pytest.mark.asyncio
async def test_expired_contract_is_excluded(state):
    db,_,_=state;await tenant(db)
    await db.rental_contracts.update_many({}, {'$set':{'end_date':'2020-01-01'}})
    assert not await resolve(db,Audience())

@pytest.mark.asyncio
async def test_workers_use_provider_type_not_fictional_role(state):
    db,_,_=state;employee=await worker(db,'employee');await worker(db,'contractor')
    assert [u['id'] for u in await resolve(db,Audience(roles=['maintenance'],worker_types=['employee']))]==[employee]
    assert not await resolve(db,Audience(roles=['maintenance'],cities=['Dumas']))

@pytest.mark.asyncio
async def test_groups_dedupe_exclusions_and_dynamic_membership(state):
    db,_,_=state;a=await tenant(db);b=await tenant(db)
    await db.notification_groups.insert_many([{'_id':'a','audience':Audience().model_dump()},{'_id':'b','audience':Audience(mode='manual',user_ids=[a]).model_dump()}])
    spec=Audience(mode='groups',group_ids=['a','b'],exclude_ids=[b])
    assert [u['id'] for u in await resolve(db,spec)]==[a]
    await db.rental_contracts.update_many({}, {'$set':{'status':'ended'}})
    assert not await resolve(db,Audience(mode='groups',group_ids=['a']))

@pytest.mark.asyncio
async def test_no_email_join_no_cross_application_accounts(state):
    db,_,_=state
    await db.users.insert_one({'_id':'tax-account','role':'tenant','push_token':'ExpoPushToken[tax]'})
    await db.app_users.insert_one({'_id':ObjectId(),'role':'tenant','email':'same@example.test','password_hash':'secret'})
    await db.tenants.insert_one({'_id':ObjectId(),'email':'same@example.test'})
    assert not await resolve(db,Audience())
    assert 'password_hash' not in str(await directory(db))

@pytest.mark.asyncio
async def test_news_opt_out_and_deleted_account(state):
    db,_,_=state
    await tenant(db,notification_preferences={'news':False});await tenant(db,status='deleted')
    assert not await resolve(db,Audience(),category='news')
    assert len(await resolve(db,Audience()))==1

async def campaign(db,uid,**kw):
    doc={'_id':'camp','audience':Audience(mode='manual',user_ids=[uid]).model_dump(),'message':c.Message(title='Aviso',body='Mensaje').model_dump(),'preview_recipients':[uid],'status':'queued','due_at':c.now(),**kw}
    await db.push_campaigns.insert_one(doc)
    return doc

@pytest.mark.asyncio
async def test_campaign_concurrent_delivery_once_and_inbox(state):
    db,sender,_=state;u=await tenant(db,push_token='ExpoPushToken[test]');await campaign(db,u)
    await asyncio.gather(c.deliver_campaign(db,'camp'),c.deliver_campaign(db,'camp'))
    assert sender.await_count==1
    assert await db.rental_notifications.count_documents({'user_id':u})==1
    assert (await db.push_deliveries.find_one({}))['status']=='accepted'

@pytest.mark.asyncio
async def test_no_token_still_has_inbox(state):
    db,sender,_=state;u=await tenant(db);await campaign(db,u);await c.deliver_campaign(db,'camp')
    assert not sender.called
    assert (await db.push_deliveries.find_one({}))['status']=='inbox_only'
    assert await db.rental_notifications.count_documents({})==1

@pytest.mark.asyncio
async def test_rejection_not_counted_as_accepted(state):
    db,sender,_=state;sender.return_value={'status':'failed','error':'DeviceNotRegistered'}
    u=await tenant(db,push_token='ExpoPushToken[test]');await campaign(db,u);await c.deliver_campaign(db,'camp')
    assert (await db.push_deliveries.find_one({}))['status']=='failed'

@pytest.mark.asyncio
async def test_ambiguous_send_not_retried(state):
    db,sender,_=state;sender.side_effect=TimeoutError()
    u=await tenant(db,push_token='ExpoPushToken[test]');await campaign(db,u)
    await c.deliver_campaign(db,'camp');await c.deliver_campaign(db,'camp')
    assert sender.await_count==1
    assert (await db.push_deliveries.find_one({}))['status']=='uncertain'

@pytest.mark.asyncio
async def test_scheduled_recalculates_but_immediate_does_not_expand(state):
    db,sender,_=state;u=await tenant(db);await campaign(db,u,audience=Audience().model_dump());await tenant(db)
    await c.deliver_campaign(db,'camp')
    assert await db.rental_notifications.count_documents({})==1
    await db.push_campaigns.delete_many({});await db.push_deliveries.delete_many({});await db.rental_notifications.delete_many({})
    await campaign(db,u,audience=Audience().model_dump(),dynamic_at_send=True)
    await c.deliver_campaign(db,'camp')
    assert await db.rental_notifications.count_documents({})==2

@pytest.mark.asyncio
async def test_news_rule_disabled_and_repeat_publication(state):
    db,_,_=state
    doc={'_id':ObjectId(),'slug':'test-news','published_to_blog':True,'subject_es':'Título','body_es':'<p>Texto</p>'}
    await db.email_templates.insert_one(doc)
    assert await c.queue_news(db,doc) is None
    await db.push_rules.insert_one({'_id':'news','enabled':True,'audience':Audience().model_dump()})
    await c.queue_news(db,doc);await c.queue_news(db,doc)
    assert await db.push_campaigns.count_documents({})==1
    assert (await db.push_campaigns.find_one({}))['message']['body']=='Texto'

@pytest.mark.asyncio
async def test_unpublished_news_and_unfilled_variables_rejected(state):
    db,_,_=state
    for m in [c.Message(title='{asunto}',body='Body'),c.Message(title='News',body='Body',destination='news',news_slug='unknown')]:
        with pytest.raises(HTTPException): await c.validate_message(db,m)

@pytest.mark.asyncio
async def test_admin_auth_enforced(state,monkeypatch):
    _,_,app=state
    monkeypatch.setattr(c,'auth_admin',AsyncMock(side_effect=HTTPException(403,'Forbidden')))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(c.BASE+'/directory')).status_code==403
        assert (await client.post(c.BASE+'/preview',json={'audience':Audience().model_dump(),'message':{'title':'A','body':'B'}})).status_code==403

@pytest.mark.asyncio
async def test_preview_bound_to_admin_and_repeat_submit(state,monkeypatch):
    db,sender,app=state;await tenant(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        p=await client.post(c.BASE+'/preview',json={'audience':Audience().model_dump(),'message':{'title':'A','body':'B'}})
        assert p.status_code==200,p.text
        body={'preview_id':p.json()['preview_id']}
        monkeypatch.setattr(c,'auth_admin',AsyncMock(return_value={'_id':'other-admin'}))
        assert (await client.post(c.BASE+'/campaigns',json=body)).status_code==409
        monkeypatch.setattr(c,'auth_admin',AsyncMock(return_value={'_id':'admin-test'}))
        assert (await client.post(c.BASE+'/campaigns',json=body)).status_code==200
        repeated = await client.post(c.BASE+'/campaigns',json=body)
        assert repeated.status_code==200
        assert repeated.json()['status']=='finished'
        assert await db.push_campaigns.count_documents({})==1
        assert await db.rental_notifications.count_documents({})==1

@pytest.mark.asyncio
async def test_real_publish_endpoint_enqueues_only_once(state,monkeypatch):
    db,sender,app=state
    import rental.drip_router as drip
    monkeypatch.setattr(drip,'get_db',lambda:db)
    monkeypatch.setattr(drip,'auth_admin',AsyncMock(return_value={'_id':'admin-test'}))
    app.include_router(drip.router)
    await tenant(db,push_token='ExpoPushToken[test]')
    await db.push_rules.insert_one({'_id':'news','enabled':True,'audience':Audience().model_dump()})
    pid=ObjectId();await db.email_templates.insert_one({'_id':pid,'slug':'fixture','subject_es':'Noticia','body_es':'Contenido','published_to_blog':False})
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        for _ in range(2):
            r=await client.patch('/admin/drip/templates/'+str(pid),json={'published_to_blog':True})
            assert r.status_code==200,r.text
    assert sender.await_count==1
    assert await db.rental_notifications.count_documents({})==1

@pytest.mark.asyncio
async def test_detail_is_scoped_to_current_user(state,monkeypatch):
    db,_,app=state;uid=await tenant(db);nid=ObjectId()
    await db.rental_notifications.insert_one({'_id':nid,'user_id':uid,'title':'Private','body':'Only this recipient'})
    monkeypatch.setattr(c,'auth_marketplace',AsyncMock(return_value={'_id':ObjectId(),'role':'tenant'}))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/marketplace/notification-detail/'+str(nid))).status_code==404
    monkeypatch.setattr(c,'auth_marketplace',AsyncMock(return_value={'_id':uid,'role':'tenant'}))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/marketplace/notification-detail/'+str(nid))).status_code==200

@pytest.mark.asyncio
async def test_schedule_replay_preserves_original_confirmation(state, monkeypatch):
    db, sender, app = state
    await tenant(db, push_token='ExpoPushToken[test]')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        p = await client.post(c.BASE+'/preview', json={'audience': Audience().model_dump(), 'message': {'title':'A','body':'B'}})
        due = c.now() + timedelta(minutes=2)
        body = {'preview_id': p.json()['preview_id'], 'scheduled_at': due.isoformat()}
        assert (await client.post(c.BASE+'/campaigns', json=body)).status_code == 200
        for replacement in [None, (due + timedelta(hours=1)).isoformat()]:
            result = await client.post(c.BASE+'/campaigns', json={**body, 'scheduled_at': replacement})
            assert result.status_code == 409
        saved = await db.push_campaigns.find_one({})
        assert saved['due_at'].replace(tzinfo=due.tzinfo) == due.replace(microsecond=due.microsecond // 1000 * 1000)
        assert saved['status'] == 'queued'
        assert await db.push_campaigns.count_documents({}) == 1
        assert not sender.called
        # Retrying the same confirmation after its due time still reports its stored state.
        monkeypatch.setattr(c, 'now', lambda: due + timedelta(seconds=1))
        repeated = await client.post(c.BASE+'/campaigns', json=body)
        assert repeated.status_code == 200
        assert repeated.json() == {'id': p.json()['preview_id'], 'status': 'queued', 'scheduled': True}

@pytest.mark.asyncio
async def test_cancelled_campaign_replay_does_not_claim_it_is_scheduled(state):
    db, sender, app = state
    await tenant(db, push_token='ExpoPushToken[test]')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        p = await client.post(c.BASE+'/preview', json={'audience': Audience().model_dump(), 'message': {'title':'A','body':'B'}})
        cid = p.json()['preview_id']
        body = {'preview_id': cid, 'scheduled_at': (c.now()+timedelta(hours=1)).isoformat()}
        await client.post(c.BASE+'/campaigns', json=body)
        assert (await client.post(c.BASE+'/campaigns/'+cid+'/cancel')).status_code == 200
        repeated = await client.post(c.BASE+'/campaigns', json=body)
        assert repeated.status_code == 200
        assert repeated.json()['status'] == 'cancelled'
        assert not sender.called
        assert await db.rental_notifications.count_documents({}) == 0
