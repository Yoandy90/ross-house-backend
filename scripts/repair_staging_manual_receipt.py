"""Repair the one staging receipt whose manual $1,250 payment the user confirmed.

Dry run by default. No payment-provider calls, emails, or other invoices changed.
The original attempt and a before-image remain available for reconciliation.
"""
import argparse
import base64
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assert os.environ.get('ENVIRONMENT', '').lower() == 'staging', 'Staging only'
    assert os.environ.get('DB_NAME') == 'ross_house_staging', 'Wrong database'
    db = MongoClient(os.environ['MONGO_URL'])['ross_house_staging']
    doc = db.rental_payments.find_one({'_id': ObjectId('6aa81c0417c3490ae0ba9a68')})
    assert doc is not None, 'Receipt not found'
    expected = {
        'contract_id': '6aa513cd4c866f4ddd51ca7e', 'receipt_number': 'REC-2026-0001',
        'period': '2026-09', 'status': 'completed', 'paid': True,
        'amount': 1200.0, 'late_fee': 50.0, 'total_due': 1250.0, 'payment_method': 'cash',
    }
    assert all(doc.get(k) == v for k, v in expected.items()), 'Receipt changed; review required'
    already_fixed = doc.get('total_paid') == 1250.0 and doc.get('confirmation_source') == 'admin_manual'
    assert already_fixed or 'total_paid' not in doc, 'A received amount is already recorded; review required'
    print('RECEIPT_REPAIR', {'receipt': doc['receipt_number'], 'mode': 'apply' if args.apply else 'dry_run',
                             'already_fixed': already_fixed, 'confirmed_total': 1250.0})
    if not args.apply:
        return
    if not already_fixed:
        now = datetime.now(timezone.utc)
        before_fields = ('status', 'paid', 'amount', 'late_fee', 'total_due', 'total_paid',
                         'payment_method', 'payment_date', 'updated_at', 'confirmation_source',
                         'payment_date_precision', 'provider_reconciliation_required')
        before = {key: doc[key] for key in before_fields if key in doc}
        query = {'_id': doc['_id'], **expected, 'total_paid': {'$exists': False},
                 'updated_at': doc.get('updated_at'), 'charge_attempt': doc.get('charge_attempt')}
        result = db.rental_payments.update_one(query, {
            '$set': {'total_paid': 1250.0, 'confirmation_source': 'admin_manual',
                     'payment_date_precision': 'date', 'updated_at': now,
                     'provider_reconciliation_required': True},
            '$push': {'manual_confirmation_history': {
                'source': 'admin_manual_repair', 'recorded_at': now,
                'recorded_by': 'codex_staging_repair', 'amount': 1250.0,
                'reason': 'User confirmed manually marking this staging test invoice paid for $1,250.',
                'before': before,
            }},
        })
        assert result.modified_count == 1, 'Concurrent change; nothing repaired'
    fixed = db.rental_payments.find_one({'_id': doc['_id']})
    assert fixed['charge_attempt'] == doc['charge_attempt'], 'Provider evidence changed'
    assert fixed['total_paid'] == fixed['amount'] + fixed['late_fee'] == 1250.0
    from rental_pdf_service import generate_rental_receipt_pdf
    from rental.tenant_payment_history import serialize_payment_activity
    from pypdf import PdfReader
    contract = db.rental_contracts.find_one({'_id': ObjectId(fixed['contract_id'])})
    tenant = db.tenants.find_one({'_id': ObjectId(fixed['tenant_id'])})
    encoded = generate_rental_receipt_pdf(fixed, contract, tenant)
    pdf = PdfReader(io.BytesIO(base64.b64decode(encoded)))
    text = '\n'.join(page.extract_text() for page in pdf.pages)
    assert text.count('$1,250.00') == 2 and '$1,200.00' in text and '$50.00' in text
    assert 'manualmente' in text and '12:00 AM' not in text
    activity = serialize_payment_activity(fixed)
    assert activity['amount'] == activity['total_paid'] == 1250.0
    print('RECEIPT_VERIFIED', {'total_paid': 1250.0, 'manual_confirmation': True,
                               'pdf_pages': len(pdf.pages), 'provider_attempt_preserved': True,
                               'activity_amount': activity['amount']})


if __name__ == '__main__':
    main()
