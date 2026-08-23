#!/usr/bin/env python3
"""Etapa 2 — authorized single-record update:
EXP-2026-0020 ($149.24, 2026-08-22, drywall @ 812 NE 2nd) → accounting_treatment=CAPITAL_IMPROVEMENT.
Owner-confirmed: this expense is part of the 812 NE 2nd remodel.
Guarded: verifies expense_number + amount + date + property_id before writing. Idempotent."""
import os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient

EXPECTED = {'expense_number': 'EXP-2026-0020', 'amount': 149.24,
            'expense_date': '2026-08-22', 'property_id': '69e40ae6268db576b07cafd0'}


async def main():
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    doc = await db.property_expenses.find_one({'expense_number': EXPECTED['expense_number']})
    assert doc, 'expense not found'
    for k, v in EXPECTED.items():
        assert doc.get(k) == v, f'MISMATCH {k}: {doc.get(k)!r} != {v!r} — ABORT'
    print(f"VERIFIED candidate: {doc['expense_number']} ${doc['amount']} {doc['expense_date']} property_id={doc['property_id']} (BEFORE treatment={doc.get('accounting_treatment')})")
    r = await db.property_expenses.update_one(
        {'_id': doc['_id'], 'expense_number': EXPECTED['expense_number']},
        {'$set': {'accounting_treatment': 'CAPITAL_IMPROVEMENT'}})
    after = await db.property_expenses.find_one({'_id': doc['_id']})
    print(f"AFTER: treatment={after['accounting_treatment']} (modified={r.modified_count})")
    cli.close()

asyncio.run(main())
