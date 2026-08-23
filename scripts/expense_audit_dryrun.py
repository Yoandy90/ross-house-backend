#!/usr/bin/env python3
"""READ-ONLY audit of property_expenses: linkage + proposed accounting_treatment.
NO writes. Output classes: LINKED_EXISTING / SAFE_TO_LINK / NEEDS_MANUAL_REVIEW / GENERAL_CONFIRMED.
"""
import os, sys, asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient
from rental.normalization import classify_expense, propose_treatment


async def main():
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    properties = [p async for p in db.properties.find({}, {'address': 1})]
    expenses = [e async for e in db.property_expenses.find(
        {}, {'expense_number': 1, 'property_id': 1, 'property_address': 1,
             'category': 1, 'description': 1, 'amount': 1, 'expense_date': 1,
             'accounting_treatment': 1}).sort('expense_date', 1)]
    counts = {}
    print(f'property_expenses: {len(expenses)} | properties: {len(properties)}')
    for e in expenses:
        status, pid, reason = classify_expense(e, properties)
        treatment = e.get('accounting_treatment') or propose_treatment(e.get('category'))
        counts[status] = counts.get(status, 0) + 1
        print(f"  {status:<20} {e.get('expense_number','?'):<14} ${e.get('amount',0):>9,.2f} "
              f"{e.get('expense_date','')} cat={e.get('category','')} "
              f"treatment={'?' if treatment is None else treatment} "
              f"prop={pid or '-'} :: {reason} :: {str(e.get('description',''))[:45]!r}")
    print('SUMMARY:', counts)
    print('NOTE: read-only audit. Nothing written. "treatment=?" requires human classification.')
    cli.close()

asyncio.run(main())
