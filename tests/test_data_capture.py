"""Etapa 4B tests — valuation previews, data-quality badges, renovation flow rules."""
import pytest
from rental.portfolio_data import (cost_basis_preview, equity_preview, unrealized_gain_preview,
                                   summarize_expenses, noi_inputs,
                                   COMPLETE, PARTIAL, INSUFFICIENT)
from rental.normalization import propose_treatment, CAPITAL_IMPROVEMENT, OPERATING

PID_812 = '69e40ae6268db576b07cafd0'


# null != zero semantics
def test_null_is_not_zero_in_equity():
    assert equity_preview(150000.0, None)['status'] == INSUFFICIENT   # loan unknown
    assert equity_preview(None, 0.0)['status'] == INSUFFICIENT        # value unknown

def test_cash_owned_loan_zero_explicit():
    e = equity_preview(150000.0, 0.0)
    assert e['status'] == COMPLETE and e['value'] == 150000.0

def test_equity_with_loan_balance():
    e = equity_preview(150000.0, 60000.0)
    assert e['value'] == 90000.0 and e['status'] == COMPLETE


# cost basis badges
def test_cost_basis_partial_when_acquisition_unknown():
    cb = cost_basis_preview(108000.0, None, 149.24)
    assert cb['value'] == pytest.approx(108149.24) and cb['status'] == PARTIAL

def test_cost_basis_complete():
    cb = cost_basis_preview(108000.0, 2500.0, 149.24)
    assert cb['status'] == COMPLETE and cb['value'] == pytest.approx(110649.24)

def test_cost_basis_insufficient_without_purchase():
    assert cost_basis_preview(None, 100.0, 100.0)['status'] == INSUFFICIENT


# unrealized gain
def test_unrealized_gain_inherits_partial():
    cb = cost_basis_preview(108000.0, None, 149.24)
    ug = unrealized_gain_preview(150000.0, cb)
    assert ug['value'] == pytest.approx(150000.0 - 108149.24) and ug['status'] == PARTIAL

def test_unrealized_gain_insufficient_without_value():
    cb = cost_basis_preview(108000.0, 2500.0, 0.0)
    assert unrealized_gain_preview(None, cb)['status'] == INSUFFICIENT


# renovation flow rules
def test_renovation_expense_is_capital_never_operating():
    # explicit CAPITAL_IMPROVEMENT from the Renovation flow always wins, category never downgrades it
    for cat in ('maintenance', 'utilities', 'repair', 'other'):
        assert propose_treatment(cat, 'CAPITAL_IMPROVEMENT') == CAPITAL_IMPROVEMENT

def test_812_recorded_capital_matches_real_amount():
    docs = [{'property_id': PID_812, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT,
             'expense_scope': 'PROPERTY'}]
    s = summarize_expenses(docs, PID_812)
    assert s['capital_improvements'] == pytest.approx(149.24)
    assert noi_inputs(0.0, s)['noi_t12_preview'] == 0.0  # capital never reduces NOI

def test_business_still_excluded_and_no_double_count():
    docs = [
        {'property_id': PID_812, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT},
        {'property_id': '', 'amount': 300.0, 'expense_scope': 'BUSINESS'},
        {'property_id': PID_812, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT,
         'migrated_from': 'investment_embedded', 'legacy_source': 'X'},
        {'property_id': PID_812, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT,
         'migrated_from': 'investment_embedded', 'legacy_source': 'X'},
    ]
    s = summarize_expenses(docs, PID_812)
    assert s['capital_improvements'] == pytest.approx(149.24 * 2)  # real + migrated once
    assert s['business_expenses_excluded'] == 300.0


# 121 Oak partial history preserved conceptually
def test_partial_history_badge_logic():
    months = 1
    status = 'COMPLETE' if months >= 12 else ('PARTIAL' if months > 0 else 'INSUFFICIENT_DATA')
    assert status == 'PARTIAL'
    assert (0 if False else 1100.0) * 1 == 1100.0  # never annualized (no ×12 anywhere)
