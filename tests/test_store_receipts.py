import asyncio
import base64
import io
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from pypdf import PdfReader

from test_resident_store import shop, setup, order
from rental import resident_store as s, store_receipts as receipts


@pytest.mark.asyncio
async def test_payment_issues_one_number_persists_pdf_and_notifies_once(shop):
    db, app = shop
    await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        oid = (await order(c)).json()['id']
        results = await asyncio.gather(*[c.post(f'/admin/store/orders/{oid}/payment', json={'reference': 'Cash <123>'}) for _ in range(4)])
        assert all(r.status_code == 200 for r in results)
        assert len({r.json()['receipt']['number'] for r in results}) == 1
        assert await db.store_receipt_files.count_documents({'order_id': oid}) == 2
        notices = await db.rental_notifications.count_documents({'data.status': 'paid', 'user_id': 'resident-1'})
        assert notices == 1
        r = await c.get(f'/store/orders/{oid}/receipt?language=es')
        assert r.status_code == 200 and r.headers['cache-control'] == 'private, no-store'
        data = base64.b64decode(r.json()['pdf_base64'])
        text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
        assert 'Recibo de compra' in text and 'PAGADO' in text and '$4.32' in text and 'Cash <123>' in text
        assert 'payment_actor' not in text and 'cost_cents' not in text
        again = await c.get(f'/admin/store/orders/{oid}/receipt?language=es')
        assert again.json()['pdf_base64'] == r.json()['pdf_base64']
        en = await c.get(f'/store/orders/{oid}/receipt?language=en')
        assert 'Purchase receipt' in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(base64.b64decode(en.json()['pdf_base64']))).pages)
        assert await db.rental_payments.count_documents({}) == 0


@pytest.mark.asyncio
async def test_unpaid_cancelled_and_other_account_cannot_download(shop, monkeypatch):
    db, app = shop
    await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        oid = (await order(c)).json()['id']
        assert (await c.get(f'/store/orders/{oid}/receipt')).status_code == 409
        await c.post(f'/store/orders/{oid}/cancel')
        assert (await c.get(f'/store/orders/{oid}/receipt')).status_code == 409
        monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'_id': 'other', 'role': 'tenant'}))
        assert (await c.get(f'/store/orders/{oid}/receipt')).status_code == 404
        for role in ['guest', 'maintenance', 'contractor']:
            monkeypatch.setattr(s, 'auth_marketplace', AsyncMock(return_value={'_id': 'resident-1', 'role': role}))
            assert (await c.get(f'/store/orders/{oid}/receipt')).status_code == 403
        assert await db.store_receipt_files.count_documents({}) == 0


@pytest.mark.asyncio
async def test_failed_render_keeps_payment_and_download_recovers(shop, monkeypatch):
    db, app = shop
    await setup(db)
    real = receipts.receipt_payload
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        oid = (await order(c)).json()['id']
        monkeypatch.setattr(receipts, 'receipt_payload', AsyncMock(side_effect=RuntimeError('storage unavailable')))
        result = await c.post(f'/admin/store/orders/{oid}/payment', json={'reference': 'cash-123'})
        assert result.status_code == 200 and result.json()['payment_status'] == 'paid'
        number = result.json()['receipt']['number']
        monkeypatch.setattr(receipts, 'receipt_payload', real)
        downloaded = await c.get(f'/store/orders/{oid}/receipt')
        assert downloaded.status_code == 200 and downloaded.json()['receipt_number'] == number


@pytest.mark.asyncio
async def test_legacy_paid_receipt_gets_number_without_repayment_or_notice(shop):
    db, app = shop
    state = await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        oid = (await order(c)).json()['id']
        await db.resident_store.update_one({'_id': s.KEY}, {'$set': {
            f'orders.{oid}.payment_status': 'paid', f'orders.{oid}.paid_at': s.now(), f'orders.{oid}.payment_reference': 'old-cash'}})
        result = await c.get(f'/store/orders/{oid}/receipt')
        assert result.status_code == 200
        assert await db.rental_notifications.count_documents({'data.status': 'paid'}) == 0
        second = await c.get(f'/store/orders/{oid}/receipt')
        assert result.json()['receipt_number'] == second.json()['receipt_number']
