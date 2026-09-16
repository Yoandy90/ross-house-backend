"""All addresses/providers are isolated fakes. Never send an external email."""
import asyncio
import base64
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport
from test_resident_store import shop, setup, order
from rental import resident_store as s, store_email as mail, store_notifications as notices


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setenv('DISABLE_BACKGROUND_JOBS', 'false')
    monkeypatch.setattr(mail, '_transactional_sendgrid_config', AsyncMock(return_value=('fake-key', 'sender@example.invalid')))
    sender = AsyncMock(return_value=202)
    monkeypatch.setattr(mail, 'send_message', sender)
    return sender


async def create(shop, language='en'):
    db, app = shop
    await setup(db)
    await db.app_users.insert_one({'_id': 'resident-1', 'role': 'tenant', 'email': 'owner@example.invalid', 'language': 'es'})
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        result = await order(c, language=language)
        assert result.status_code == 200
        return result.json()


@pytest.mark.asyncio
async def test_confirmation_language_and_paid_pdf_are_sent_once_after_commit(shop, provider):
    db, app = shop
    purchase = await create(shop)
    assert not provider.called
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        assert (await order(c, language='en')).json()['id'] == purchase['id']
        await asyncio.gather(mail.drain(db), mail.drain(db))
        assert provider.await_count == 1
        content = provider.call_args.args[3]
        assert provider.call_args.args[2] == 'owner@example.invalid'
        assert 'We received your order' in content['subject']
        assert 'Total due upon receipt' in content['text']
        assert not any(a['type'] == 'application/pdf' for a in content['attachments'])
        for _ in range(2):
            assert (await c.post('/admin/store/orders/' + purchase['id'] + '/payment', json={'reference': 'cash-123'})).status_code == 200
        await asyncio.gather(mail.drain(db), mail.drain(db))
        assert provider.await_count == 2
        content = provider.call_args.args[3]
        assert 'Your purchase receipt' in content['subject']
        pdf = next(a for a in content['attachments'] if a['type'] == 'application/pdf')
        assert pdf['filename'].endswith('-en.pdf') and base64.b64decode(pdf['content']).startswith(b'%PDF')
        assert await db.store_email_deliveries.count_documents({'status': 'accepted'}) == 2
        assert await db.rental_payments.count_documents({}) == 0


@pytest.mark.asyncio
async def test_staging_and_kill_switch_never_send_or_replay(shop, monkeypatch, provider):
    monkeypatch.setenv('ENVIRONMENT', 'staging')
    db, _ = shop
    await create(shop)
    await mail.drain(db)
    assert await db.store_email_deliveries.count_documents({'status': 'suppressed_environment'}) == 1
    monkeypatch.setenv('ENVIRONMENT', 'production')
    await mail.drain(db)
    assert not provider.called


@pytest.mark.asyncio
async def test_pending_send_rechecks_kill_switch(shop, monkeypatch, provider):
    db, _ = shop
    await create(shop)
    monkeypatch.setenv('DISABLE_BACKGROUND_JOBS', 'true')
    await mail.drain(db)
    assert not provider.called
    assert await db.store_email_deliveries.count_documents({'status': 'suppressed_environment'}) == 1


@pytest.mark.asyncio
async def test_unknown_provider_result_never_automatically_resends(shop, provider):
    db, _ = shop
    await create(shop)
    provider.side_effect = TimeoutError()
    await mail.drain(db)
    await mail.drain(db)
    assert provider.await_count == 1
    assert await db.store_email_deliveries.count_documents({'status': 'uncertain'}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change,status', [
    ({'settings.email_notifications': False}, 'disabled'),
    ({'orders.PLACEHOLDER.status': 'cancelled'}, 'superseded'),
    ({'orders.PLACEHOLDER.user_id': 'someone-else'}, 'ineligible'),
])
async def test_disabled_cancelled_or_wrong_owner_is_not_sent(shop, provider, change, status):
    db, _ = shop
    purchase = await create(shop)
    await db.resident_store.update_one({'_id': s.KEY}, {'$set': {k.replace('PLACEHOLDER',purchase['id']):v for k,v in change.items()}})
    await mail.drain(db)
    assert not provider.called
    assert await db.store_email_deliveries.count_documents({'status': status}) == 1


@pytest.mark.asyncio
async def test_old_events_do_not_generate_retrospective_mail(shop):
    db, _ = shop
    await mail.enqueue(db, {'_id':'old', 'status_code':'received', 'order_id':'old', 'user_id':'resident-1'})
    assert await db.store_email_deliveries.count_documents({}) == 0


@pytest.mark.asyncio
async def test_missing_configuration_remains_retryable_without_provider_calls(shop, monkeypatch, provider):
    db, _ = shop
    await create(shop)
    monkeypatch.setattr(mail, '_transactional_sendgrid_config', AsyncMock(return_value=('', 'sender@example.invalid')))
    await mail.drain(db)
    assert not provider.called
    pending = await db.store_email_deliveries.find_one({'status':'pending'})
    assert pending['last_error'] == 'configuration_missing' and pending['retry_at']


@pytest.mark.asyncio
async def test_html_escapes_product_customer_and_address_and_uses_local_photos(shop):
    purchase = await create(shop, language='es')
    purchase['customer_name'] = '<script>alert(1)</script>'
    purchase['address'] = '<b>House</b>'
    purchase['items'][0]['name'] = '<img src=x onerror=alert(1)>'
    content = mail.build_message(purchase, 'received', 'es', {'water': b'photo'})
    assert '<script>' not in content['html'] and '&lt;script&gt;' in content['html']
    assert '&lt;img' in content['html'] and 'src="https://' not in content['html']
    assert 'Total al recibir' in content['html']
    assert any(a['type'] == 'image/jpeg' for a in content['attachments'])
    assert 'Tu pago está registrado' not in content['text']


@pytest.mark.asyncio
async def test_receipt_render_failure_defers_email_without_reversing_payment(shop, monkeypatch, provider):
    db, app = shop
    purchase = await create(shop)
    await mail.drain(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        await c.post('/admin/store/orders/' + purchase['id'] + '/payment', json={'reference':'cash-123'})
    monkeypatch.setattr(mail, 'receipt_payload', AsyncMock(side_effect=RuntimeError('temporary storage failure')))
    await mail.drain(db)
    assert provider.await_count == 1
    pending = await db.store_email_deliveries.find_one({'kind':'paid'})
    assert pending['status'] == 'pending' and pending['last_error'] == 'document_unavailable'
    assert (await s.read_state())['orders'][purchase['id']]['payment_status'] == 'paid'
