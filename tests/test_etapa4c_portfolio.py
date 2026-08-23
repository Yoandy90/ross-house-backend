"""ETAPA 4C — Portfolio profesional: 27 tests obligatorios."""
import os, sys
import pytest

from rental.portfolio_data import (professional_analysis, acquisition_costs_resolved,
                                   summarize_expenses, cost_basis_preview, equity_preview,
                                   unrealized_gain_preview,
                                   COMPLETE, PARTIAL, INSUFFICIENT)
from rental.normalization import propose_treatment, CAPITAL_IMPROVEMENT

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from etapa4c_plan import (CLOSING_CHARGES_812, INSPECTION_CHARGES_812, COMMON_FIELDS,
                          INVESTMENT_UPDATES_812, KEEP_812, PROP_812)

PID = PROP_812
EMPTY = {'property_expenses_total': 0.0, 'operating_expenses': 0.0, 'capital_improvements': 0.0,
         'acquisition_costs': 0.0, 'unclassified': 0.0, 'business_expenses_excluded': 0.0}


def summary(**over):
    return {**EMPTY, **over}


def analyze(**kw):
    base = dict(purchase_price=108000.0, closing_costs_manual=None, current_estimated_value=None,
                arv=None, loan_balance=None, summary=summary(), income_t12=0.0,
                months_with_data=0, property_status='')
    base.update(kw)
    return professional_analysis(**base)


# 1. Purchase price 812 = 108000 (plan mantiene KEEP, sin update)
def test_01_purchase_price_812_kept_at_108000():
    assert KEEP_812['purchase_price'] == 108000.0
    assert 'purchase_price' not in INVESTMENT_UPDATES_812  # nunca se reescribe


# 2. Loan balance acepta 0 explícito
def test_02_loan_balance_explicit_zero():
    assert INVESTMENT_UPDATES_812['loan_balance'] == 0.0
    a = analyze(loan_balance=0.0, current_estimated_value=150000.0)
    assert a['valuation']['equity']['value'] == 150000.0
    assert a['valuation']['equity']['status'] == COMPLETE


# 3. null != 0
def test_03_null_is_not_zero():
    a_null = analyze(loan_balance=None, current_estimated_value=150000.0)
    a_zero = analyze(loan_balance=0.0, current_estimated_value=150000.0)
    assert a_null['valuation']['equity']['status'] == INSUFFICIENT
    assert a_zero['valuation']['equity']['status'] == COMPLETE
    assert a_null['valuation']['loan_balance'] is None  # nunca casteado a 0


# 4. ACQUISITION_COST canónico gana sobre closing_costs fallback
def test_04_canonical_overrides_manual_fallback():
    r = acquisition_costs_resolved(1043.50, 9999.0)
    assert r['value'] == 1043.50 and r['source'] == 'canonical'


# 5. Sin double-counting de acquisition (nunca canónico + manual sumados)
def test_05_no_acquisition_double_count():
    a = analyze(closing_costs_manual=1043.50, summary=summary(acquisition_costs=1043.50))
    assert a['acquisition']['acquisition_costs']['value'] == 1043.50  # NO 2087
    assert a['acquisition']['adjusted_cost_basis']['value'] == pytest.approx(108000.0 + 1043.50)


# 6. Capital improvements incluidos en ACB
def test_06_capex_in_acb():
    a = analyze(summary=summary(acquisition_costs=1043.50, capital_improvements=149.24))
    assert a['acquisition']['adjusted_cost_basis']['value'] == pytest.approx(108000 + 1043.50 + 149.24)


# 7. Operating expenses excluidos del ACB
def test_07_operating_excluded_from_acb():
    a = analyze(summary=summary(acquisition_costs=1043.50, operating_expenses=500.0))
    assert a['acquisition']['adjusted_cost_basis']['value'] == pytest.approx(109043.50)


# 8. Business expenses excluidos del ACB
def test_08_business_excluded_from_acb():
    a = analyze(summary=summary(acquisition_costs=1043.50, business_expenses_excluded=2712.84))
    assert a['acquisition']['adjusted_cost_basis']['value'] == pytest.approx(109043.50)


# 9-11. Capital / acquisition / business excluidos del NOI
def test_09_capital_excluded_from_noi():
    a = analyze(income_t12=1100.0, months_with_data=1,
                summary=summary(capital_improvements=149.24, operating_expenses=232.75))
    assert a['operations']['noi_t12']['value'] == pytest.approx(1100 - 232.75)

def test_10_acquisition_excluded_from_noi():
    a = analyze(income_t12=1100.0, months_with_data=1,
                summary=summary(acquisition_costs=1043.50, operating_expenses=232.75))
    assert a['operations']['noi_t12']['value'] == pytest.approx(1100 - 232.75)

def test_11_business_excluded_from_noi():
    a = analyze(income_t12=1100.0, months_with_data=1,
                summary=summary(business_expenses_excluded=2712.84, operating_expenses=232.75))
    assert a['operations']['noi_t12']['value'] == pytest.approx(1100 - 232.75)


# 12. Se usa renta realmente cobrada
def test_12_actual_collected_rent_used():
    a = analyze(income_t12=1100.0, months_with_data=1)
    assert a['operations']['collected_income_t12']['value'] == 1100.0


# 13. Historia parcial NO se anualiza
def test_13_partial_history_not_annualized():
    a = analyze(income_t12=1100.0, months_with_data=1, summary=summary(operating_expenses=232.75))
    assert a['operations']['collected_income_t12']['status'] == PARTIAL
    assert a['operations']['noi_t12']['value'] == pytest.approx(867.25)  # nunca ×12
    assert a['operations']['noi_t12']['status'] == PARTIAL
    assert '1 month' in a['operations']['collected_income_t12']['notes'][0]


# 14-16. Sin CEV: Cap Rate / Equity / Unrealized Gain no disponibles (nunca 0 / 0%)
def test_14_cap_rate_unavailable_without_cev():
    a = analyze(income_t12=1100.0, months_with_data=1, current_estimated_value=None)
    assert a['performance']['cap_rate']['value'] is None
    assert a['performance']['cap_rate']['status'] == INSUFFICIENT
    assert 'Current property value required' in a['performance']['cap_rate']['notes']

def test_15_equity_unavailable_without_cev():
    a = analyze(current_estimated_value=None, loan_balance=0.0)
    assert a['valuation']['equity']['value'] is None
    assert a['valuation']['equity']['status'] == INSUFFICIENT

def test_16_unrealized_gain_unavailable_without_cev():
    a = analyze(current_estimated_value=None)
    assert a['valuation']['unrealized_gain']['value'] is None
    assert a['valuation']['unrealized_gain']['status'] == INSUFFICIENT


# 17. Propiedad cash: equity funciona cuando existe CEV
def test_17_cash_owned_equity_with_cev():
    a = analyze(current_estimated_value=140000.0, loan_balance=0.0)
    assert a['valuation']['equity']['value'] == 140000.0
    assert a['valuation']['equity']['status'] == COMPLETE


# 18. Alertas de datos faltantes DERIVADAS del estado de los datos
def test_18_missing_data_derived_not_hardcoded():
    a812 = analyze(property_status='maintenance')  # 812 hoy: CEV/ARV/loan null, sin acq, en obra
    assert set(a812['missing_data']) == {'current_estimated_value', 'arv', 'loan_balance',
                                         'acquisition_costs', 'renovation_expenses', 'income_history'}
    full = analyze(current_estimated_value=140000.0, arv=160000.0, loan_balance=0.0,
                   closing_costs_manual=None, summary=summary(acquisition_costs=1043.50),
                   income_t12=13200.0, months_with_data=12, property_status='rented')
    assert full['missing_data'] == []  # sin datos faltantes ⇒ sin alertas


# 19. Legacy investments.expenses[] no se double-cuenta
def test_19_legacy_embedded_not_double_counted():
    docs = [
        {'property_id': PID, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT,
         'expense_scope': 'PROPERTY', 'migrated_from': 'investment_embedded', 'legacy_source': 'x1'},
        {'property_id': PID, 'amount': 149.24, 'accounting_treatment': CAPITAL_IMPROVEMENT,
         'expense_scope': 'PROPERTY', 'migrated_from': 'investment_embedded', 'legacy_source': 'x1'},
    ]
    assert summarize_expenses(docs, PID)['capital_improvements'] == pytest.approx(149.24)


# 20. Remodelación crea property_expense canónico (CAPITAL_IMPROVEMENT explícito gana)
def test_20_renovation_creates_canonical_capital():
    for cat in ('repair', 'maintenance', 'other'):
        assert propose_treatment(cat, 'CAPITAL_IMPROVEMENT') == CAPITAL_IMPROVEMENT


# 21. property_id correcto en el flujo de remodelación (plan usa property, no investment)
def test_21_renovation_correct_property_id():
    assert COMMON_FIELDS['property_id'] == PROP_812
    assert COMMON_FIELDS['expense_scope'] == 'PROPERTY'
    docs = [{'property_id': PROP_812, 'amount': 100.0, 'expense_scope': 'PROPERTY',
             'accounting_treatment': CAPITAL_IMPROVEMENT}]
    assert summarize_expenses(docs, 'otra-prop')['capital_improvements'] == 0.0  # no cruza props


# 22-23. Dirección canónica 812 NE 2nd; snapshot legacy preservado
def test_22_canonical_address_812():
    assert KEEP_812['canonical_address'].strip() == '812 NE 2nd'

def test_23_legacy_address_snapshot_preserved():
    assert KEEP_812['address_snapshot'].strip() == '812 ND 2da'


# 24. Gastos BUSINESS (oficina) siguen excluidos
def test_24_business_office_excluded():
    docs = [{'property_id': '', 'amount': 2712.84, 'expense_scope': 'BUSINESS'}]
    s = summarize_expenses(docs, PID)
    assert s['business_expenses_excluded'] == 2712.84
    assert s['property_expenses_total'] == 0.0


# 25. Paridad i18n EN/ES para claves nuevas del Portfolio (pf.*)
def test_25_i18n_parity_new_keys():
    import json
    base = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "i18n")
    if not os.path.isdir(base):
        pytest.skip("frontend no disponible en este entorno")
    with open(os.path.join(base, "en.json")) as f:
        en = json.load(f)
    with open(os.path.join(base, "es.json")) as f:
        es = json.load(f)
    pf_en = set((en.get('pf') or {}).keys())
    pf_es = set((es.get('pf') or {}).keys())
    assert pf_en and pf_en == pf_es


# 26. /analysis requiere Admin (auth_admin se ejecuta antes de cualquier acceso a datos)
def test_26_analysis_requires_admin():
    import inspect
    from rental.investments_router import investment_analysis_preview
    src = inspect.getsource(investment_analysis_preview)
    assert 'await auth_admin(request)' in src
    assert src.index('auth_admin') < src.index('find_one')  # auth ANTES de tocar la DB


# 27. Retro-compatibilidad: claves legacy del /analysis intactas + estructura nueva aditiva
def test_27_backward_compatible_structure():
    a = analyze()
    for k in ('acquisition', 'valuation', 'operations', 'performance', 'data_quality', 'missing_data'):
        assert k in a
    # cash-on-cash nunca definitivo con remodelación incompleta
    coc = analyze(current_estimated_value=140000.0, income_t12=1100.0, months_with_data=1,
                  summary=summary(acquisition_costs=1043.50, capital_improvements=149.24),
                  property_status='maintenance')['performance']['cash_on_cash']
    assert coc['status'] in (PARTIAL, INSUFFICIENT)
