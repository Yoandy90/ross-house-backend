#!/usr/bin/env python3
"""ETAPA 3 — READ-ONLY dry-run: expense_scope + accounting_treatment proposals
for ALL property_expenses, plus Portfolio PREVIEW for 121 Oak & 812 NE 2nd.
NO writes."""
import os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from rental.normalization import propose_treatment
from rental.portfolio_data import (effective_scope, summarize_expenses,
                                   adjusted_cost_basis, noi_inputs, collected_income_t12)

OFFICE_MARKERS = ('305 bruce', 'oficina', 'office')


def propose_scope(e):
    if e.get('property_id'):
        return 'PROPERTY', 'HIGH', 'has property_id (canonical link)'
    snap = (e.get('property_address') or '').lower()
    desc = (e.get('description') or '').lower()
    cat = (e.get('category') or '').lower()
    if any(m in snap for m in OFFICE_MARKERS) or any(m in desc for m in OFFICE_MARKERS) or cat == 'office':
        return 'BUSINESS', 'HIGH', 'office/305 Bruce Ave marker in snapshot/description/category'
    return 'BUSINESS?', 'LOW', 'no property_id and no office evidence — AMBIGUOUS, needs human review'


async def main():
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    props = {str(p['_id']): p.get('address', '') async for p in db.properties.find({}, {'address': 1})}
    expenses = [e async for e in db.property_expenses.find({}).sort('expense_date', 1)]
    counts = {}
    print('=== EXPENSE DRY-RUN (read-only, nothing written) ===')
    for e in expenses:
        scope, conf, reason = propose_scope(e)
        cur_tr = e.get('accounting_treatment')
        prop_tr = cur_tr or propose_treatment(e.get('category'))
        tr_conf = 'HIGH' if (cur_tr or prop_tr) else 'LOW'
        counts[scope] = counts.get(scope, 0) + 1
        addr = props.get(str(e.get('property_id') or ''), '-')
        print(f"  {e['expense_number']:<14} {e.get('expense_date','')} ${float(e.get('amount',0)):>8,.2f} "
              f"cat={e.get('category',''):<12} prop={addr.strip() or '-':<14} "
              f"scope[{e.get('expense_scope','<absent>')}→{scope}/{conf}] "
              f"treatment[{cur_tr or '<absent>'}→{prop_tr or 'NEEDS_HUMAN'}/{tr_conf}] :: {reason} "
              f":: {str(e.get('description',''))[:40]!r}")
    print('SCOPE SUMMARY:', counts)

    print('\n=== PORTFOLIO PREVIEW (read-only; visible formulas UNCHANGED) ===')
    async for inv in db.investments.find({'property_id': {'$nin': ['', None]}}):
        pid = inv['property_id']
        summary = summarize_expenses(expenses, pid)
        purchase = inv.get('purchase_price')
        acq = summary['acquisition_costs'] if summary['acquisition_costs'] > 0 else None
        closing = inv.get('closing_costs')  # absent on legacy docs => UNKNOWN
        acb, unknowns = adjusted_cost_basis(purchase, closing if closing is not None else acq, summary['capital_improvements'])
        income = await collected_income_t12(db, pid)
        noi = noi_inputs(income, summary)
        prop_addr = props.get(str(pid), '?').strip()
        fmt = lambda v: 'UNKNOWN / NOT RECORDED' if v is None else f'${v:,.2f}'
        print(f'\n  ── {prop_addr} (investment {inv["_id"]}) ──')
        print(f'  Purchase Price:              {fmt(purchase)}')
        print(f'  Acquisition Costs:           {fmt(closing if closing is not None else acq)}')
        print(f'  Capital Improvements:        ${summary["capital_improvements"]:,.2f}')
        print(f'  Adjusted Cost Basis PREVIEW: {fmt(acb)}  {"(" + "; ".join(unknowns) + ")" if unknowns else ""}')
        print(f'  Operating Expenses (all-time): ${summary["operating_expenses"]:,.2f}')
        print(f'  Unclassified PROPERTY exp:   ${summary["unclassified"]:,.2f}')
        print(f'  Business Expenses EXCLUDED:  ${summary["business_expenses_excluded"]:,.2f}')
        print(f'  Property Income T12 (collected): ${income:,.2f}')
        print(f'  NOI T12 PREVIEW (income − operating): {fmt(noi["noi_t12_preview"])}  [PREVIEW ONLY — visible NOI unchanged]')
        cev = inv.get('current_estimated_value')
        print(f'  Cap Rate PREVIEW: ' + (f'{(noi["noi_t12_preview"]/cev*100):.2f}%' if (cev and noi['noi_t12_preview'] is not None) else 'UNKNOWN (current_estimated_value NOT RECORDED)'))
    cli.close()

asyncio.run(main())
