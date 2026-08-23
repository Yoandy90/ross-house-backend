"""Canonical Portfolio Data Layer (Etapa 3) — READ-ONLY aggregation helpers.

Canonical expense source: property_expenses, joined by property_id
(investment.property_id -> properties._id -> property_expenses.property_id).
Address matching is NEVER used for aggregation once property_id exists.

Anti-double-counting rule:
  - investments.expenses[] is LEGACY/FROZEN (never written to, never deleted).
  - Canonical aggregation counts ONLY property_expenses docs.
  - If a legacy embedded expense is ever migrated, the canonical doc must carry
    migrated_from='investment_embedded' + legacy_source id; aggregation counts
    the canonical doc once and ignores the embedded original.

None of these helpers feed any VISIBLE metric yet (formula freeze).
"""
from datetime import datetime, timedelta

from rental.normalization import OPERATING, CAPITAL_IMPROVEMENT, ACQUISITION_COST

SCOPE_PROPERTY = 'PROPERTY'
SCOPE_BUSINESS = 'BUSINESS'


def effective_scope(exp: dict) -> str:
    """Explicit expense_scope wins; else derived: property_id ⇒ PROPERTY, none ⇒ BUSINESS."""
    scope = (exp.get('expense_scope') or '').upper()
    if scope in (SCOPE_PROPERTY, SCOPE_BUSINESS):
        return scope
    return SCOPE_PROPERTY if (exp.get('property_id') or '') else SCOPE_BUSINESS


def summarize_expenses(expenses: list[dict], property_id: str) -> dict:
    """Pure aggregation for one property from canonical property_expenses docs.
    UNCLASSIFIED = PROPERTY expenses without accounting_treatment (never guessed)."""
    out = {'property_expenses_total': 0.0, 'operating_expenses': 0.0,
           'capital_improvements': 0.0, 'acquisition_costs': 0.0,
           'unclassified': 0.0, 'business_expenses_excluded': 0.0}
    seen_migrated = set()
    for e in expenses:
        amt = float(e.get('amount') or 0)
        if e.get('migrated_from') == 'investment_embedded':
            key = (e.get('legacy_source'), amt)
            if key in seen_migrated:
                continue  # double-counting guard
            seen_migrated.add(key)
        if effective_scope(e) == SCOPE_BUSINESS:
            out['business_expenses_excluded'] += amt
            continue
        if str(e.get('property_id') or '') != str(property_id):
            continue
        out['property_expenses_total'] += amt
        tr = e.get('accounting_treatment')
        if tr == OPERATING:
            out['operating_expenses'] += amt
        elif tr == CAPITAL_IMPROVEMENT:
            out['capital_improvements'] += amt
        elif tr == ACQUISITION_COST:
            out['acquisition_costs'] += amt
        else:
            out['unclassified'] += amt
    return out


def adjusted_cost_basis(purchase_price, acquisition_costs, capital_improvements):
    """ADJUSTED COST BASIS = purchase + acquisition costs + capital improvements.
    Returns None (UNKNOWN) if purchase_price itself is unknown. Missing cost
    components that are None are reported as unknown, NOT silently zeroed."""
    if purchase_price is None:
        return None, ['purchase_price NOT RECORDED']
    unknowns = []
    total = float(purchase_price)
    for name, v in (('acquisition_costs', acquisition_costs), ('capital_improvements', capital_improvements)):
        if v is None:
            unknowns.append(f'{name} NOT RECORDED')
        else:
            total += float(v)
    return total, unknowns


def noi_inputs(income_t12: float | None, summary: dict) -> dict:
    """Future-NOI inputs. ONLY OPERATING expenses may reduce NOI. Capital
    improvements, acquisition costs and business expenses are excluded.
    Does NOT compute/replace the visible NOI."""
    return {
        'gross_property_income_t12': income_t12,
        'operating_expenses': summary['operating_expenses'],
        'excluded_capital_improvements': summary['capital_improvements'],
        'excluded_acquisition_costs': summary['acquisition_costs'],
        'excluded_business_expenses': summary['business_expenses_excluded'],
        'excluded_unclassified': summary['unclassified'],
        'noi_t12_preview': (income_t12 - summary['operating_expenses']) if income_t12 is not None else None,
    }


async def collected_income_t12(db, property_id: str, now: datetime | None = None) -> float:
    """Actually-collected rent for the trailing 12 months (status completed/paid)."""
    now = now or datetime.utcnow()
    since = now - timedelta(days=365)
    pipeline = [
        {'$match': {'property_id': str(property_id),
                    'status': {'$in': ['completed', 'paid']},
                    'payment_date': {'$gte': since}}},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}},
    ]
    res = await db.rental_payments.aggregate(pipeline).to_list(1)
    return float(res[0]['total']) if res else 0.0


async def property_expense_summary(db, property_id: str) -> dict:
    docs = [e async for e in db.property_expenses.find(
        {}, {'amount': 1, 'property_id': 1, 'expense_scope': 1,
             'accounting_treatment': 1, 'migrated_from': 1, 'legacy_source': 1})]
    return summarize_expenses(docs, property_id)


# ── Etapa 4B: valuation previews + data-quality badges (PREVIEW ONLY) ────────
COMPLETE, PARTIAL, INSUFFICIENT = 'COMPLETE', 'PARTIAL', 'INSUFFICIENT_DATA'


def cost_basis_preview(purchase_price, acquisition_costs, capital_improvements) -> dict:
    total, unknowns = adjusted_cost_basis(purchase_price, acquisition_costs, capital_improvements)
    if total is None:
        return {'value': None, 'status': INSUFFICIENT, 'notes': unknowns}
    return {'value': total, 'status': PARTIAL if unknowns else COMPLETE, 'notes': unknowns}


def equity_preview(current_estimated_value, loan_balance) -> dict:
    if current_estimated_value is None:
        return {'value': None, 'status': INSUFFICIENT, 'notes': ['current_estimated_value NOT RECORDED']}
    if loan_balance is None:
        return {'value': None, 'status': INSUFFICIENT, 'notes': ['loan_balance NOT RECORDED']}
    return {'value': float(current_estimated_value) - float(loan_balance), 'status': COMPLETE, 'notes': []}


def unrealized_gain_preview(current_estimated_value, cost_basis: dict) -> dict:
    if current_estimated_value is None or cost_basis['value'] is None:
        return {'value': None, 'status': INSUFFICIENT, 'notes': ['inputs missing']}
    return {'value': float(current_estimated_value) - cost_basis['value'],
            'status': cost_basis['status'], 'notes': cost_basis['notes']}
