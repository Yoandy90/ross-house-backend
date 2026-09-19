"""PDF exports must not turn reusable signature templates into signed evidence."""
import base64
import copy
import io
from datetime import datetime, timezone
from pathlib import Path
import ast

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader
from rental_pdf_service import generate_rental_contract_pdf, _format_signature_date


def signature(color):
    image = Image.new('RGB', (100, 30), 'white')
    ImageDraw.Draw(image).line((2, 20, 35, 3, 85, 25), fill=color, width=3)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()


TENANT = signature('blue')
ADMIN = signature('red')
BASE = dict(contract_number='CONT-QA-002', tenant_name='Test Tenant',
            property_address='999 Staging Test Ave', start_date='2027-09-01',
            end_date='2028-08-31', rent_amount=1200, deposit_amount=0,
            terms='PRUEBA STAGING - TEST ONLY', status='pending_signature',
            tenant_signature={'image_data': TENANT, 'signed_at': '2026-09-19T00:12:00Z'})


def render(contract, config=None):
    data = base64.b64decode(generate_rental_contract_pdf(contract, config))
    reader = PdfReader(io.BytesIO(data))
    return reader, '\n'.join(page.extract_text() for page in reader.pages)


def test_tenant_only_export_ignores_saved_admin_and_preserves_terms():
    contract = copy.deepcopy(BASE)
    config = {'saved_admin_signature': {'image_data': ADMIN, 'signed_at': '2026-09-13T12:00:00Z'}}
    original = copy.deepcopy((contract, config))
    reader, text = render(contract, config)
    assert len(reader.pages[-1].images) == 1
    assert '09/18/2026' in text
    assert '09/13/2026' not in text
    assert 'R.H.R.L.' not in text
    assert '2027-09-01' in text and '2028-08-31' in text
    assert 'PRUEBA STAGING' in text
    assert (contract, config) == original


@pytest.mark.parametrize('field,date_field', [('admin_signature','admin_signed_at'), ('landlord_signature','landlord_signed_at')])
def test_explicit_legacy_contract_signature_is_rendered(field,date_field):
    reader, text = render({**BASE, field: ADMIN, date_field: '2026-09-19T01:00:00Z'})
    assert len(reader.pages[-1].images) == 2
    assert 'R.H.R.L.' in text
    assert '09/18/2026' in text


def test_admin_without_date_does_not_borrow_tenant_date():
    reader, _ = render({**BASE, 'admin_signature': ADMIN, 'signed_at': '2026-09-19T00:12:00Z'})
    last = reader.pages[-1].extract_text()
    assert last.count('Date / Fecha: 09/18/2026') == 1
    assert 'Date / Fecha: _______________' in last


def test_unsigned_contract_stays_unsigned_even_with_template():
    reader, text = render({**BASE, 'tenant_signature': None}, {'saved_admin_signature': {'image_data': ADMIN}})
    assert len(reader.pages[-1].images) == 0
    assert 'R.H.R.L.' not in text


@pytest.mark.parametrize('value,expected', [
    ('2026-09-19T00:12:00Z','09/18/2026'),
    ('2026-09-18T19:12:00-05:00','09/18/2026'),
    (datetime(2026,9,19,0,12),'09/18/2026'),
    (datetime(2026,9,19,0,12,tzinfo=timezone.utc),'09/18/2026'),
    ('2026-01-02T05:30:00Z','01/01/2026'),
    ('2026-09-19','09/19/2026'), ('09/19/2026','09/19/2026'),
    (None,'____________'), ('invalid','____________'),
])
def test_signing_calendar_day(value,expected):
    assert _format_signature_date(value) == expected


def test_read_and_email_exports_never_load_default_signature():
    tree = ast.parse(Path('rental/contracts_router.py').read_text())
    targets = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
               and ('generate_rental_contract_pdf' in ast.unparse(n))]
    assert len(targets) >= 3
    for node in targets:
        assert 'admin_signatures.find_one' not in ast.unparse(node), node.name
