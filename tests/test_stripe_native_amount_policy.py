from pathlib import Path

from rental.stripe_pkg.tenant_payments_router import _native_rent_pi_idempotency_key


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


def test_native_pi_idempotency_is_stable_for_same_balance_snapshot():
    charge = {
        "invoice_id": "66d0aa112233445566778899",
        "total_due": 1050.0,
        "total_paid": 400.0,
        "outstanding": 650.0,
    }
    assert _native_rent_pi_idempotency_key(charge) == _native_rent_pi_idempotency_key(dict(charge))


def test_native_pi_idempotency_changes_when_balance_or_invoice_changes():
    base = {
        "invoice_id": "66d0aa112233445566778899",
        "total_due": 1050.0,
        "total_paid": 400.0,
        "outstanding": 650.0,
    }
    balance_changed = {**base, "total_paid": 500.0, "outstanding": 550.0}
    invoice_changed = {**base, "invoice_id": "66d0aa112233445566778800"}
    assert _native_rent_pi_idempotency_key(base) != _native_rent_pi_idempotency_key(balance_changed)
    assert _native_rent_pi_idempotency_key(base) != _native_rent_pi_idempotency_key(invoice_changed)


def test_native_pi_creation_passes_explicit_idempotency_key():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    create = source.split("async def tenant_create_stripe_payment", 1)[1].split(
        "async def tenant_confirm_stripe_payment", 1
    )[0]
    assert "_native_rent_pi_idempotency_key(charge)" in create
    assert "PaymentIntent.create(" in create
    assert "idempotency_key=idempotency_key" in create
