import asyncio
import base64
import subprocess
from datetime import datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from rental import contracts_router, tenant_invoices_router
from rental.tenant_payment_history import serialize_payment_activity
from rental_pdf_service import generate_rental_receipt_pdf


class Request:
    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data


def invoice(**changes):
    return {
        '_id': ObjectId(), 'tenant_id': 'test-tenant', 'period': '2026-09',
        'period_month': 'September', 'period_year': 2026,
        'amount': 1200, 'late_fee': 50, 'total_due': 1250,
        'paid': False, 'status': 'pending', 'payment_method': 'cash',
        'charge_attempt': {'id': 'attempt-1', 'status': 'processing',
                           'source': 'helcim_checkout', 'amount': 1200},
        **changes,
    }


def prepare(monkeypatch):
    db = AsyncMongoMockClient()['manual_receipt_test']
    async def auth(_): return {'email': 'admin@example.com', '_id': 'admin'}
    monkeypatch.setattr(contracts_router, 'get_db', lambda: db)
    monkeypatch.setattr(contracts_router, 'auth_admin', auth)
    return db


def test_manual_confirmation_records_full_amount_once_and_keeps_provider_evidence(monkeypatch):
    async def scenario():
        db = prepare(monkeypatch)
        doc = invoice()
        await db.rental_payments.insert_one(doc)
        req = Request({'status': 'completed', 'payment_date': '2026-09-15'})
        await contracts_router.update_rental_payment(str(doc['_id']), req)
        paid = await db.rental_payments.find_one({'_id': doc['_id']})
        assert paid['total_paid'] == 1250
        assert paid['paid'] is True
        assert paid['confirmation_source'] == 'admin_manual'
        assert paid['confirmed_by'] == 'admin@example.com'
        assert paid['payment_date_precision'] == 'date'
        assert paid['charge_attempt'] == doc['charge_attempt']
        assert paid['provider_reconciliation_required'] is True
        assert len(paid['manual_confirmation_history']) == 1
        await contracts_router.update_rental_payment(str(doc['_id']), req)
        repeated = await db.rental_payments.find_one({'_id': doc['_id']})
        assert len(repeated['manual_confirmation_history']) == 1
        assert repeated['confirmed_at'] == paid['confirmed_at']
        activity = serialize_payment_activity(repeated)
        assert activity['amount'] == activity['total_paid'] == 1250
    asyncio.run(scenario())


@pytest.mark.parametrize('amount', [1200, 1300, -1, 'NaN', 'Infinity', '1250.001', None])
def test_invalid_or_incomplete_received_amount_cannot_be_marked_paid(monkeypatch, amount):
    async def scenario():
        db = prepare(monkeypatch)
        doc = invoice()
        await db.rental_payments.insert_one(doc)
        with pytest.raises(HTTPException) as exc:
            await contracts_router.update_rental_payment(str(doc['_id']), Request({
                'status': 'completed', 'total_paid': amount,
            }))
        assert exc.value.status_code in (400, 409)
        assert await db.rental_payments.find_one({'_id': doc['_id']}) == doc
    asyncio.run(scenario())


def test_existing_manual_paid_record_can_be_repaired_with_explicit_total(monkeypatch):
    async def scenario():
        db = prepare(monkeypatch)
        doc = invoice(status='completed', paid=True, receipt_number='REC-2026-0001')
        await db.rental_payments.insert_one(doc)
        await contracts_router.update_rental_payment(str(doc['_id']), Request({
            'status': 'completed', 'total_paid': 1250,
        }))
        paid = await db.rental_payments.find_one({'_id': doc['_id']})
        assert paid['receipt_number'] == 'REC-2026-0001'
        assert paid['total_paid'] == 1250
        assert paid['manual_confirmation_history'][0]['previous_total_paid'] is None
    asyncio.run(scenario())


def test_concurrent_settlement_is_not_overwritten(monkeypatch):
    async def scenario():
        db = prepare(monkeypatch)
        collection = db.rental_payments
        monkeypatch.setattr(contracts_router, 'get_db', lambda: SimpleNamespace(rental_payments=collection))
        doc = invoice()
        await collection.insert_one(doc)
        original = collection.update_one
        async def competing_settlement(query, mutation):
            await original({'_id': doc['_id']}, {'$set': {
                'status': 'completed', 'total_paid': 1250, 'payment_method': 'card',
            }})
            return await original(query, mutation)
        monkeypatch.setattr(collection, 'update_one', competing_settlement)
        with pytest.raises(HTTPException) as exc:
            await contracts_router.update_rental_payment(str(doc['_id']), Request({'status': 'completed'}))
        assert exc.value.status_code == 409
        settled = await collection.find_one({'_id': doc['_id']})
        assert settled['payment_method'] == 'card'
        assert 'manual_confirmation_history' not in settled
    asyncio.run(scenario())


def test_manual_creation_records_source_and_date_precision(monkeypatch):
    async def scenario():
        db = prepare(monkeypatch)
        response = await contracts_router.register_rental_payment(Request({
            'amount': 1200, 'late_fee': 50, 'payment_method': 'cash',
            'status': 'completed', 'payment_date': '2026-09-15',
        }))
        doc = await db.rental_payments.find_one({'_id': ObjectId(response['id'])})
        assert doc['total_paid'] == 1250
        assert doc['confirmation_source'] == 'admin_manual'
        assert doc['payment_date_precision'] == 'date'
    asyncio.run(scenario())


def test_invoice_history_uses_received_amount_not_due_or_old_attempt(monkeypatch):
    async def scenario():
        db = prepare(monkeypatch)
        await db.rental_payments.insert_one(invoice(status='completed', paid=True, total_paid=1250))
        async def auth(_): return {'_id': 'test-tenant'}
        async def ids(_): return ['test-tenant']
        monkeypatch.setattr(tenant_invoices_router, 'get_db', lambda: db)
        monkeypatch.setattr(tenant_invoices_router, 'auth_marketplace', auth)
        monkeypatch.setattr(tenant_invoices_router, '_resolve_tenant_ids_for_user', ids)
        result = await tenant_invoices_router.tenant_invoices_history(None)
        assert result['summary']['total_paid'] == 1250
        assert result['items'][0]['amount'] == 1250
        # Legacy inconsistent records must not claim collection of an extra $50.
        await db.rental_payments.update_many({}, {'$set': {'total_paid': 1200}})
        result = await tenant_invoices_router.tenant_invoices_history(None)
        assert result['summary']['total_paid'] == 1200
    asyncio.run(scenario())


def pdf_text(tmp_path, payment):
    encoded = generate_rental_receipt_pdf(payment, contract={'contract_number': 'TEST'}, tenant={'name': 'Test Tenant'})
    path = tmp_path / 'receipt.pdf'
    path.write_bytes(base64.b64decode(encoded))
    return subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)


def test_pdf_total_manual_confirmation_and_date_only(tmp_path):
    text = pdf_text(tmp_path, invoice(
        status='completed', paid=True, total_paid=1250, confirmation_source='admin_manual',
        receipt_number='REC-TEST', payment_date=datetime(2026, 9, 15), payment_date_precision='date',
    ))
    assert text.count('$1,250.00') == 2
    assert '$1,200.00' in text and '$50.00' in text
    assert 'manualmente' in text and 'Efectivo' in text
    assert '15 Sep 2026' in text and '12:00 AM' not in text


def test_pdf_timestamp_preserves_timezone(tmp_path):
    text = pdf_text(tmp_path, invoice(
        status='completed', total_paid=1250,
        payment_date='2026-09-15T06:26:14.160Z', payment_date_precision='datetime',
    ))
    assert '01:26 AM CDT' in text


@pytest.mark.parametrize('changes', [
    {'status': 'pending'}, {'status': 'completed', 'total_paid': 1200},
    {'status': 'completed'}, {'status': 'completed', 'total_paid': 1250, 'record_type': 'checkout_attempt'},
])
def test_pdf_refuses_unconfirmed_or_inconsistent_receipt(changes):
    with pytest.raises(HTTPException) as exc:
        generate_rental_receipt_pdf(invoice(**changes))
    assert exc.value.status_code == 409
