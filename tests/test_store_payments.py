"""Isolated store payment flows; no Helcim/Mongo/customer network calls."""
import asyncio
from unittest.mock import AsyncMock
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
import pytest
import pytest_asyncio
from test_resident_store import shop, setup, order, checkout
from rental import resident_store as s, store_payments as p

MID = '000000000000000000000001'
CFG = {'api_token': 'fake', 'environment': 'sandbox', 'fingerprint': 'fake-fingerprint'}

@pytest_asyncio.fixture
async def online(shop, monkeypatch):
    db, app = shop
    await setup(db, stock=10)
    await db.helcim_saved_methods.insert_one({'_id': ObjectId(MID), 'tenant_id':'resident-1', 'type':'card', 'card_token':'fake-token', 'brand':'Visa','last4':'1234'})
    monkeypatch.setattr(p, 'configuration', AsyncMock(return_value=CFG))
    provider = AsyncMock(return_value={'transactionId':123, 'amount':4.32,'currency':'USD','status':'APPROVED'})
    monkeypatch.setattr(p, 'provider_request', provider)
    return db, app, provider

def payment(**kw):
    return {'payment_method_id':MID,'payment_authorized':True,'payment_authorization_version':p.AUTH_VERSION,**kw}

@pytest.mark.asyncio
async def test_card_paid_receipt_and_duplicate_replay(online):
    db,app,provider=online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json()
        b=(await order(c,**payment())).json()
        assert a['id']==b['id'] and a['payment_status']=='paid' and a['receipt']['number']==b['receipt']['number']
        assert provider.await_count==1
        assert provider.call_args.kwargs['key']==(await s.read_state())['orders'][a['id']]['payment_attempt']['id']
        assert 'payment_attempt' not in a and 'fake-token' not in str(a) and 'credential_fingerprint' not in str(a)
        assert (await s.read_state())['products']['water']['stock']==9
        for col in ('rental_payments','autopay_config','tenant_payments'):
            assert await db[col].count_documents({})==0

@pytest.mark.asyncio
async def test_concurrent_checkouts_send_once(online):
    db,app,provider=online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        body=checkout(**payment());q=(await c.post('/store/quote',json=body)).json()
        body.update(quote_hash=q['quote_hash'],idempotency_key='concurrent-purchase-123')
        rs=await asyncio.gather(*[c.post('/store/orders',json=body) for _ in range(8)])
        assert all(r.status_code==200 for r in rs)
        assert len({r.json()['id'] for r in rs})==1 and provider.await_count==1

@pytest.mark.asyncio
async def test_timeout_blocks_recharge_manual_payment_and_cancellation(online):
    db,app,provider=online;provider.side_effect=TimeoutError()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json();oid=a['id']
        assert a['payment_status']=='review_required' and not a.get('receipt')
        assert (await order(c,**payment())).json()['id']==oid
        assert provider.await_count==1
        for path,body in [(f'/store/orders/{oid}/cancel',None),(f'/admin/store/orders/{oid}/payment',{'reference':'cash-123'}),(f'/admin/store/orders/{oid}/status',{'status':'preparing'})]:
            assert (await c.post(path,json=body)).status_code==409

@pytest.mark.asyncio
async def test_ach_pending_then_cleared(online):
    db,app,provider=online
    await db.helcim_saved_methods.update_one({'_id':ObjectId(MID)},{'$set':{'type':'ach','customer_id':7,'bank_account_id':8}})
    provider.return_value={'id':321,'amount':4.32,'currency':'USD','statusAuth':'APPROVED','statusClearing':'OPENED'}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json();oid=a['id']
        assert a['payment_status']=='ach_pending' and not a.get('receipt')
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'preparing'})).status_code==409
        provider.return_value={**provider.return_value,'statusClearing':'CLEARED'}
        a=(await c.post(f'/store/orders/{oid}/payment-status')).json()
        assert a['payment_status']=='paid' and a['receipt']['number']
        assert provider.call_args.args[1:]==('GET','/ach/transactions/321')

@pytest.mark.asyncio
async def test_decline_can_cancel_and_restock_once(online):
    db,app,provider=online;provider.return_value['status']='DECLINED'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json();oid=a['id'];assert a['payment_status']=='failed'
        for _ in range(2):assert (await c.post(f'/store/orders/{oid}/cancel')).status_code==200
        assert (await s.read_state())['products']['water']['stock']==10

@pytest.mark.asyncio
@pytest.mark.parametrize('response',[
 {'transactionId':123,'amount':1,'currency':'USD','status':'APPROVED'},
 {'transactionId':123,'amount':4.32,'currency':'CAD','status':'APPROVED'},
 {'amount':4.32,'currency':'USD','status':'APPROVED'},
])
async def test_bad_provider_financials_never_paid(online,response):
    db,app,provider=online;provider.return_value=response
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json();assert a['payment_status']=='review_required' and not a.get('receipt')

@pytest.mark.asyncio
async def test_owner_consent_and_changed_quote(online,monkeypatch):
    db,app,provider=online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.post('/store/quote',json=checkout(**payment(payment_authorized=False)))).status_code==400
        await db.helcim_saved_methods.update_one({'_id':ObjectId(MID)},{'$set':{'tenant_id':'other'}})
        assert (await c.post('/store/quote',json=checkout(**payment()))).status_code==409
        assert not provider.called

@pytest.mark.asyncio
async def test_refund_single_claim_and_no_automatic_restock(online):
    db,app,provider=online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json();oid=a['id']
        provider.return_value={'transactionId':124,'amount':4.32,'currency':'USD','status':'APPROVED'}
        rs=await asyncio.gather(*[c.post(f'/admin/store/orders/{oid}/refund',json={'reason':'Customer requested cancellation'}) for _ in range(4)])
        assert all(r.status_code==200 for r in rs) and provider.await_count==2
        a=(await s.read_state())['orders'][oid]
        assert a['payment_status']=='refunded' and a['refunded_cents']==432
        assert (await s.read_state())['products']['water']['stock']==9
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'cancelled'})).status_code==200
        assert (await s.read_state())['products']['water']['stock']==10

@pytest.mark.asyncio
async def test_refund_timeout_does_not_resend_or_complete_delivery(online):
    db,app,provider=online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c,**payment())).json()['id'];provider.side_effect=TimeoutError()
        for _ in range(2):await c.post(f'/admin/store/orders/{oid}/refund',json={'reason':'Customer requested'})
        assert provider.await_count==2
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'preparing'})).status_code==409

@pytest.mark.asyncio
async def test_result_recovers_after_confirmation_write_fails(online,monkeypatch):
    db,app,provider=online;original=s.mutate
    calls=0
    async def fail_once(fn):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('database temporarily unavailable')
        return await original(fn)
    monkeypatch.setattr(s,'mutate',fail_once)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        a=(await order(c,**payment())).json();assert a['payment_status']=='processing'
        a=(await c.post('/store/orders/'+a['id']+'/payment-status')).json()
        assert a['payment_status']=='paid' and a['receipt']['number'] and provider.await_count==1

@pytest.mark.asyncio
async def test_status_endpoint_owner_and_environment_guard(online,monkeypatch):
    db,app,provider=online
    provider.return_value={'transactionId':123,'amount':4.32,'currency':'USD','status':'PENDING'}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c,**payment())).json()['id']
        monkeypatch.setattr(p,'configuration',AsyncMock(return_value={**CFG,'fingerprint':'changed'}))
        assert (await c.post(f'/store/orders/{oid}/payment-status')).status_code==409
        monkeypatch.setattr(s,'auth_marketplace',AsyncMock(return_value={'_id':'other','role':'tenant'}))
        assert (await c.post(f'/store/orders/{oid}/payment-status')).status_code==404

@pytest.mark.asyncio
async def test_staging_rejects_production_credentials(monkeypatch):
    from rental import payment_processors_core as core
    monkeypatch.setenv('ENVIRONMENT','staging')
    monkeypatch.setattr(core,'get_active_processor',AsyncMock(return_value=('helcim',{'api_token':'fake','environment':'production'})))
    with pytest.raises(Exception) as exc:await p.configuration()
    assert exc.value.detail=='store_payment_sandbox_required'

@pytest.mark.asyncio
async def test_stale_quote_does_not_reserve_or_charge(online):
    db, app, provider = online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        body = checkout(**payment())
        body['quote_hash'] = (await c.post('/store/quote',json=body)).json()['quote_hash']
        body['idempotency_key'] = 'stale-payment-quote-123'
        await db.resident_store.update_one({'_id':s.KEY},{'$set':{'products.water.price_cents':999}})
        assert (await c.post('/store/orders',json=body)).status_code == 409
        assert provider.await_count == 0
        assert (await s.read_state())['products']['water']['stock'] == 10

@pytest.mark.asyncio
async def test_ach_refund_requires_distinct_cleared_transaction(online):
    db, app, provider = online
    await db.helcim_saved_methods.update_one({'_id':ObjectId(MID)},{'$set':{'type':'ach','customer_id':7,'bank_account_id':8}})
    original={'id':321,'amount':4.32,'currency':'USD','statusAuth':'APPROVED','statusClearing':'CLEARED'}
    provider.return_value=original
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c,**payment())).json()['id']
        assert (await c.post(f'/store/orders/{oid}/payment-status')).json()['payment_status']=='paid'
        provider.return_value={**original,'id':322,'statusClearing':'OPENED'}
        r=(await c.post(f'/admin/store/orders/{oid}/refund',json={'reason':'Customer requested refund'})).json()
        assert r['online_payment']['refund_status']=='refund_pending'
        assert r['payment_status']=='paid'
        provider.return_value={**original,'id':322}
        r=(await c.post(f'/store/orders/{oid}/payment-status')).json()
        assert r['payment_status']=='refunded'
        a=(await s.read_state())['orders'][oid]['refund_attempt']
        assert p.outcome(original,a,refresh=True,refund=True)=='review_required'
        assert await db.rental_notifications.count_documents({'data.status':'refunded'})==1

@pytest.mark.asyncio
async def test_late_pending_does_not_undo_paid_and_return_blocks_fulfillment(online):
    db,app,_=online
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c,**payment())).json()['id']
        a=(await s.read_state())['orders'][oid]['payment_attempt']
        before=(await s.read_state())['orders'][oid]['receipt']
        await p.apply_result(oid,a['id'],{'id':123},'ach_pending')
        assert (await s.read_state())['orders'][oid]['payment_status']=='paid'
        for _ in range(2): await p.apply_result(oid,a['id'],{'id':123},'failed')
        assert (await c.post(f'/admin/store/orders/{oid}/status',json={'status':'preparing'})).status_code==409
        state=await s.read_state()
        assert state['orders'][oid]['receipt']==before
        assert sum(e['action']=='payment_return_review' for e in state['audit'])==1

@pytest.mark.asyncio
async def test_reconciliation_respects_environment_and_clears_known_ach(online,monkeypatch):
    from rental import background_job_policy as policy
    db,app,provider=online
    await db.helcim_saved_methods.update_one({'_id':ObjectId(MID)},{'$set':{'type':'ach','customer_id':7,'bank_account_id':8}})
    provider.return_value={'id':321,'amount':4.32,'currency':'USD','statusAuth':'APPROVED','statusClearing':'OPENED'}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c,**payment())).json()['id']
    monkeypatch.setattr(policy,'should_disable_background_jobs',lambda:True)
    await p.reconcile_pending(db)
    assert provider.await_count==1
    monkeypatch.setattr(policy,'should_disable_background_jobs',lambda:False)
    provider.return_value['statusClearing']='CLEARED'
    await p.reconcile_pending(db)
    assert provider.await_count==2
    assert (await s.read_state())['orders'][oid]['payment_status']=='paid'

@pytest.mark.asyncio
async def test_late_success_after_failed_cancel_requires_review_not_paid(online):
    db,app,provider=online
    provider.return_value['status']='DECLINED'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        oid=(await order(c,**payment())).json()['id']
        assert (await c.post(f'/store/orders/{oid}/cancel')).status_code==200
        a=(await s.read_state())['orders'][oid]['payment_attempt']
        result=await p.apply_result(oid,a['id'],{'id':123},'paid')
        assert result['payment_status']=='failed' and result['payment_review_required']
        assert not result.get('receipt') and result['status']=='cancelled'
        assert (await s.read_state())['products']['water']['stock']==10
