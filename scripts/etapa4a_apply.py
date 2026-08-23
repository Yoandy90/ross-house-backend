#!/usr/bin/env python3
"""ETAPA 4A — apply HIGH-confidence classifications (authorized).
Backup -> dry-run guard -> apply -> verify -> idempotency -> coverage -> preview.
Writes ONLY: expense_scope (20 docs) + accounting_treatment=OPERATING (9 docs of 121 Oak).
Aborts if any record no longer matches the approved audit."""
import os, sys, json, asyncio
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient
from rental.portfolio_data import summarize_expenses, adjusted_cost_basis, noi_inputs, collected_income_t12

PID_OAK = '69dbabdf5347719e9849b402'
PID_812 = '69e40ae6268db576b07cafd0'
OFFICE = ('305 bruce', 'oficina', 'office')

def is_office(e):
    return (any(m in (e.get('property_address') or '').lower() for m in OFFICE)
            or any(m in (e.get('description') or '').lower() for m in OFFICE)
            or (e.get('category') or '').lower() == 'office')

async def main():
    apply = '--apply' in sys.argv
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    exps = [e async for e in db.property_expenses.find({}).sort('expense_date', 1)]
    assert len(exps) == 20, f'ABORT: expected 20 expenses, found {len(exps)}'
    plan = []
    for e in exps:
        pid = e.get('property_id') or ''
        if pid:
            scope = 'PROPERTY'
            tr = e.get('accounting_treatment') or ('OPERATING' if pid == PID_OAK and e.get('category') in ('utilities', 'maintenance') else None)
            if pid == PID_812:
                assert e['expense_number'] == 'EXP-2026-0020' and e.get('accounting_treatment') == 'CAPITAL_IMPROVEMENT', 'ABORT: 812 expense mismatch'
        else:
            assert is_office(e), f'ABORT: non-office unlinked expense {e["expense_number"]} — STOP'
            scope, tr = 'BUSINESS', e.get('accounting_treatment')
        plan.append((e, scope, tr))
    n_prop = sum(1 for _, s, _ in plan if s == 'PROPERTY'); n_bus = 20 - n_prop
    n_oper = sum(1 for e, s, t in plan if s == 'PROPERTY' and t == 'OPERATING' and e.get('property_id') == PID_OAK)
    pending = [(e, s, t) for e, s, t in plan if e.get('expense_scope') != s or (t and e.get('accounting_treatment') != t)]
    print(f'DRY-RUN: PROPERTY={n_prop} BUSINESS={n_bus} oak_OPERATING={n_oper} | pending writes={len(pending)}')
    assert n_prop == 10 and n_bus == 10 and n_oper == 9, 'ABORT: counts do not match approved audit'
    if not apply:
        for e, s, t in pending:
            print(f"  WOULD WRITE {e['expense_number']}: scope→{s} treatment→{t or '(unchanged)'}")
        cli.close(); return
    if pending:
        os.makedirs(os.path.join(os.path.dirname(__file__), 'backups'), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        path = os.path.join(os.path.dirname(__file__), 'backups', f'etapa4a_before_{ts}.json')
        json.dump([{'expense_id': str(e['_id']), 'expense_number': e['expense_number'],
                    'property_id': e.get('property_id'), 'expense_scope': e.get('expense_scope'),
                    'accounting_treatment': e.get('accounting_treatment'), 'category': e.get('category'),
                    'amount': e.get('amount'), 'date': e.get('expense_date')} for e, _, _ in pending],
                  open(path, 'w'), indent=2, ensure_ascii=False)
        assert len(json.load(open(path))) == len(pending)
        print(f'BACKUP OK (readable): {path} ({len(pending)} records)')
        for e, s, t in pending:
            upd = {'expense_scope': s}
            if t and e.get('accounting_treatment') != t:
                upd['accounting_treatment'] = t
            r = await db.property_expenses.update_one({'_id': e['_id']}, {'$set': upd})
            print(f"  WROTE {e['expense_number']}: {upd} (modified={r.modified_count})")
    # verify
    exps2 = [e async for e in db.property_expenses.find({})]
    v_prop = sum(1 for e in exps2 if e.get('expense_scope') == 'PROPERTY')
    v_bus = sum(1 for e in exps2 if e.get('expense_scope') == 'BUSINESS')
    v_oak_op = sum(1 for e in exps2 if e.get('property_id') == PID_OAK and e.get('accounting_treatment') == 'OPERATING')
    v_812_cap = sum(1 for e in exps2 if e.get('property_id') == PID_812 and e.get('accounting_treatment') == 'CAPITAL_IMPROVEMENT')
    v_bus_linked = sum(1 for e in exps2 if e.get('expense_scope') == 'BUSINESS' and (e.get('property_id') or ''))
    props = {str(p['_id']) async for p in db.properties.find({}, {'_id': 1})}
    dangling = sum(1 for e in exps2 if (e.get('property_id') or '') and e['property_id'] not in props)
    print(f'VERIFY: PROPERTY={v_prop} BUSINESS={v_bus} oak_OPERATING={v_oak_op} 812_CAPITAL={v_812_cap} business_linked={v_bus_linked} dangling={dangling}')
    # coverage + preview
    for pid, label in ((PID_OAK, '121 Oak Ave'), (PID_812, '812 NE 2nd')):
        pays = [p async for p in db.rental_payments.find({'property_id': pid, 'status': {'$in': ['completed', 'paid']}}, {'payment_date': 1, 'amount': 1})]
        pm = sorted({p['payment_date'].strftime('%Y-%m') for p in pays})
        pexp = [e for e in exps2 if e.get('property_id') == pid]
        em = sorted({(e.get('expense_date') or '')[:7] for e in pexp if e.get('expense_date')})
        t12 = 'INSUFFICIENT_DATA' if len(pm) == 0 else ('FULL_T12' if len(pm) >= 12 else 'PARTIAL_T12')
        summary = summarize_expenses(exps2, pid)
        inv = await db.investments.find_one({'property_id': pid})
        income = await collected_income_t12(db, pid)
        acq = inv.get('closing_costs') if inv.get('closing_costs') is not None else (summary['acquisition_costs'] or None)
        acb, unk = adjusted_cost_basis(inv.get('purchase_price'), acq, summary['capital_improvements'])
        noi = noi_inputs(income, summary)
        cev = inv.get('current_estimated_value')
        f = lambda v: 'UNKNOWN' if v is None else f'${v:,.2f}'
        print(f'\n── PREVIEW {label} ── payments: months={len(pm)} range={pm[0] if pm else "-"}..{pm[-1] if pm else "-"} | expenses: months={len(em)} range={em[0] if em else "-"}..{em[-1] if em else "-"} | T12={t12}')
        print(f'  Purchase={f(inv.get("purchase_price"))} Acquisition={f(acq)} Capital=${summary["capital_improvements"]:,.2f} ACB={f(acb)} {unk}')
        print(f'  Operating(all-time)=${summary["operating_expenses"]:,.2f} Unclassified=${summary["unclassified"]:,.2f} BusinessExcluded=${summary["business_expenses_excluded"]:,.2f}')
        print(f'  CollectedIncomeT12={f(income)} [{t12}] NOI_T12_PREVIEW={f(noi["noi_t12_preview"])} [{t12}] CurrentEstValue={f(cev)} CapRatePreview=' + (f'{noi["noi_t12_preview"]/cev*100:.2f}%' if cev and noi['noi_t12_preview'] is not None else 'UNKNOWN (needs current_estimated_value)'))
    cli.close()

asyncio.run(main())
