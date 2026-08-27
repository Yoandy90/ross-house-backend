from pathlib import Path


def test_native_pi_financial_values_are_server_resolved():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    create = source.split("async def tenant_create_stripe_payment", 1)[1].split(
        "async def tenant_confirm_stripe_payment", 1
    )[0]
    assert "resolve_current_rent_charge" in create
    assert 'charge.get("outstanding")' in create
    assert 'charge.get("invoice_id")' in create
    assert 'data.get("late_fee")' not in create
    assert 'data.get("rent_amount")' not in create
    assert 'data.get("amount")' not in create
    assert '"amount": total_cents' in create
    assert '"invoice_id"' in create
    assert '"invoice_total_due"' in create
    assert '"invoice_total_paid"' in create


def test_native_pi_metadata_carries_reconciliation_identity():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    create = source.split("async def tenant_create_stripe_payment", 1)[1].split(
        "async def tenant_confirm_stripe_payment", 1
    )[0]
    for key in (
        '"tenant_id"',
        '"contract_id"',
        '"invoice_id"',
        '"period_month"',
        '"period_month_num"',
        '"period_year"',
        '"invoice_total_due"',
        '"invoice_total_paid"',
        '"charge_amount"',
    ):
        assert key in create
