import asyncio
import base64
import io
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from pypdf import PdfReader

from rental import contracts_router as routes
from rental.invoice_document import invoice_reference, parse_invoice_reference


def setup(monkeypatch):
    db = AsyncMongoMockClient()['invoice_documents']
    async def auth(_): return {'email': 'office@example.com'}
    monkeypatch.setattr(routes, 'get_db', lambda: db)
    monkeypatch.setattr(routes, 'auth_admin', auth)
    return db


def test_scan_finds_exact_invoice_without_changing_it(monkeypatch):
    async def run():
        db = setup(monkeypatch)
        row = {'_id': ObjectId(), 'amount':1200, 'late_fee':50, 'total_due':1250,
               'status':'pending', 'period':'2026-09', 'tenant_name':'Test'}
        await db.rental_payments.insert_one(row)
        await db.rental_payments.insert_one({'amount':1200,'status':'pending'})
        ref = invoice_reference(row)
        request = SimpleNamespace(query_params={'search':ref})
        result = await routes.list_rental_payments(request)
        assert result['total'] == 1
        assert str(result['payments'][0]['_id']) == str(row['_id'])
        document = await routes.admin_rental_payment_document(str(row['_id']), request)
        pdf = PdfReader(io.BytesIO(base64.b64decode(document['pdf_base64'])))
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text()
        assert 'PENDIENTE' in text and 'No acredita un pago recibido' in text
        assert text.count('$1,250.00') == 2
        assert ref in text
        assert await db.rental_payments.find_one({'_id':row['_id']}) == row
    asyncio.run(run())


def test_document_requires_admin_before_database_access(monkeypatch):
    async def deny(_): raise HTTPException(403, 'Admin only')
    monkeypatch.setattr(routes, 'auth_admin', deny)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.admin_rental_payment_document(str(ObjectId()), None))
    assert exc.value.status_code == 403


@pytest.mark.parametrize('change', [
    {'record_type':'checkout_attempt'}, {'invoice_id':'another-invoice'},
    {'total_due':1300}, {'total_paid':100}, {'status':'failed'},
])
def test_document_does_not_misrepresent_attempts_or_amounts(monkeypatch, change):
    async def run():
        db = setup(monkeypatch)
        row = {'_id':ObjectId(),'amount':1200,'late_fee':50,'total_due':1250,'status':'pending',**change}
        await db.rental_payments.insert_one(row)
        with pytest.raises(HTTPException):
            await routes.admin_rental_payment_document(str(row['_id']),None)
    asyncio.run(run())


def test_reference_contains_only_id_and_rejects_arbitrary_input():
    identity = str(ObjectId())
    ref = invoice_reference({'_id':identity,'tenant_email':'secret@example.com'})
    assert ref == 'RHR:' + identity
    assert parse_invoice_reference(ref) == identity
    for value in ['https://example.com', 'RHR:.*', 'RHR:'+identity+'/pay']:
        assert parse_invoice_reference(value) is None


@pytest.mark.parametrize('change', [
    {'paid':True,'status':'completed'},
    {'charge_attempt':{'status':'processing'}},
    {'charge_attempt':{'status':'unknown'}},
])
def test_office_confirmation_blocks_paid_or_unresolved_invoice(monkeypatch, change):
    async def run():
        db=setup(monkeypatch)
        row={'_id':ObjectId(),'status':'pending','amount':1200,'late_fee':0,**change}
        await db.rental_payments.insert_one(row)
        class Request:
            async def json(self):
                return {'confirm_unpaid':True,'status':'completed','total_paid':1200,'payment_method':'cash','payment_date':'2026-09-15'}
        with pytest.raises(HTTPException) as exc:
            await routes.update_rental_payment(str(row['_id']),Request())
        assert exc.value.status_code == 409
        assert await db.rental_payments.find_one({'_id':row['_id']}) == row
    asyncio.run(run())


def test_admin_receipt_resolves_contract_and_tenant_metadata(monkeypatch):
    async def run():
        db=setup(monkeypatch)
        contract_id, tenant_id, payment_id = ObjectId(), ObjectId(), ObjectId()
        await db.rental_contracts.insert_one({'_id':contract_id,'contract_number':'CT-TEST-123','property_address':'Test Address'})
        await db.tenants.insert_one({'_id':tenant_id,'name':'Test Tenant'})
        await db.rental_payments.insert_one({'_id':payment_id,'contract_id':str(contract_id),'tenant_id':str(tenant_id),'amount':1200,'late_fee':50,'total_paid':1250,'status':'completed','payment_date':'2026-09-15'})
        result=await routes.admin_rental_payment_document(str(payment_id),None)
        text=PdfReader(io.BytesIO(base64.b64decode(result['pdf_base64']))).pages[0].extract_text()
        assert 'CT-TEST-123' in text and 'Test Tenant' in text and 'Test Address' in text
    asyncio.run(run())
