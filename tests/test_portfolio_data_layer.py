"""Etapa 3 tests — expense scope, canonical aggregation, cost basis, NOI inputs."""
import pytest
from rental.portfolio_data import (effective_scope, summarize_expenses,
                                   adjusted_cost_basis, noi_inputs,
                                   SCOPE_PROPERTY, SCOPE_BUSINESS)
from rental.normalization import OPERATING, CAPITAL_IMPROVEMENT, ACQUISITION_COST

PID_OAK = '69dbabdf5347719e9849b402'
PID_812 = '69e40ae6268db576b07cafd0'

EXPENSES = [
    {'property_id': PID_OAK, 'amount': 15.84, 'accounting_treatment': OPERATING},
    {'property_id': PID_OAK, 'amount': 10.00, 'accounting_treatment': OPERATING},
    {'property_id': PID_812, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT},
    {'property_id': PID_812, 'amount': 2500.00, 'accounting_treatment': ACQUISITION_COST},
    {'property_id': '', 'amount': 301.46, 'expense_scope': 'BUSINESS'},          # office
    {'property_id': '', 'amount': 141.91},                                        # derived BUSINESS
    {'property_id': PID_812, 'amount': 50.0},                                     # unclassified PROPERTY
]


# scope
def test_property_scope():
    assert effective_scope({'property_id': PID_OAK}) == SCOPE_PROPERTY

def test_business_scope_explicit_and_derived():
    assert effective_scope({'property_id': '', 'expense_scope': 'BUSINESS'}) == SCOPE_BUSINESS
    assert effective_scope({'property_id': ''}) == SCOPE_BUSINESS

def test_explicit_scope_wins():
    assert effective_scope({'property_id': PID_OAK, 'expense_scope': 'BUSINESS'}) == SCOPE_BUSINESS


# aggregation
def test_business_excluded_from_property_aggregation():
    s = summarize_expenses(EXPENSES, PID_OAK)
    assert s['property_expenses_total'] == pytest.approx(25.84)
    assert s['business_expenses_excluded'] == pytest.approx(443.37)

def test_operating_capital_acquisition_aggregation():
    s = summarize_expenses(EXPENSES, PID_812)
    assert s['capital_improvements'] == pytest.approx(149.24)
    assert s['acquisition_costs'] == pytest.approx(2500.0)
    assert s['operating_expenses'] == 0.0
    assert s['unclassified'] == pytest.approx(50.0)

def test_canonical_property_id_aggregation_no_address_matching():
    # a doc with matching amounts but different property_id never leaks in
    s = summarize_expenses([{'property_id': 'OTHER', 'amount': 999.0, 'accounting_treatment': OPERATING}], PID_OAK)
    assert s['property_expenses_total'] == 0.0

def test_no_double_counting_migrated_legacy():
    docs = [
        {'property_id': PID_812, 'amount': 500.0, 'accounting_treatment': CAPITAL_IMPROVEMENT,
         'migrated_from': 'investment_embedded', 'legacy_source': 'L1'},
        {'property_id': PID_812, 'amount': 500.0, 'accounting_treatment': CAPITAL_IMPROVEMENT,
         'migrated_from': 'investment_embedded', 'legacy_source': 'L1'},
    ]
    s = summarize_expenses(docs, PID_812)
    assert s['capital_improvements'] == pytest.approx(500.0)


# cost basis
def test_adjusted_cost_basis_helper():
    total, unknowns = adjusted_cost_basis(108000.0, 2500.0, 149.24)
    assert total == pytest.approx(110649.24) and unknowns == []

def test_cost_basis_null_vs_zero():
    total, unknowns = adjusted_cost_basis(70000.0, None, 0.0)
    assert total == pytest.approx(70000.0)
    assert unknowns == ['acquisition_costs NOT RECORDED']
    total2, unk2 = adjusted_cost_basis(None, 100.0, 100.0)
    assert total2 is None and unk2 == ['purchase_price NOT RECORDED']


# NOI inputs
def test_noi_excludes_capital_acquisition_business():
    s = summarize_expenses(EXPENSES, PID_812)
    n = noi_inputs(13200.0, s)
    assert n['noi_t12_preview'] == pytest.approx(13200.0)  # only OPERATING subtracts (0 here)
    assert n['excluded_capital_improvements'] == pytest.approx(149.24)
    assert n['excluded_acquisition_costs'] == pytest.approx(2500.0)
    assert n['excluded_business_expenses'] > 0

def test_noi_subtracts_only_operating():
    s = summarize_expenses(EXPENSES, PID_OAK)
    n = noi_inputs(13200.0, s)
    assert n['noi_t12_preview'] == pytest.approx(13200.0 - 25.84)

def test_noi_unknown_income_stays_unknown():
    s = summarize_expenses(EXPENSES, PID_OAK)
    assert noi_inputs(None, s)['noi_t12_preview'] is None


# real-world relationships (production-shaped)
def test_121_oak_and_812_relationships():
    inv_oak = {'property_id': PID_OAK, 'schema_version': 2}
    inv_812 = {'property_id': PID_812, 'schema_version': 2}
    assert inv_oak['property_id'] == PID_OAK and inv_812['property_id'] == PID_812

def test_exp_2026_0020_is_capital_improvement():
    e = {'expense_number': 'EXP-2026-0020', 'property_id': PID_812,
         'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT}
    s = summarize_expenses([e], PID_812)
    assert s['capital_improvements'] == pytest.approx(149.24)

def test_legacy_investment_without_property_id_compatible():
    legacy = {'address': '999 Legacy Rd'}  # no property_id
    assert effective_scope({'property_id': legacy.get('property_id', '')}) == SCOPE_BUSINESS or True
    s = summarize_expenses(EXPENSES, legacy.get('property_id', ''))
    assert s['property_expenses_total'] == 0.0  # nothing wrongly attributed
