"""Regression guards for hosted-checkout server-side rent amounts."""
from pathlib import Path


SOURCE = Path("rental/payment_processors_router.py").read_text(encoding="utf-8")
ROUTE = SOURCE.split("async def tenant_create_checkout_payment", 1)[1].split(
    "def _clover_signature_valid", 1
)[0]


def test_hosted_checkout_uses_server_charge_resolver():
    assert "from .hosted_rent_charge import resolve_hosted_rent_charge" in SOURCE
    assert "await resolve_hosted_rent_charge(" in ROUTE
    assert 'amount = charge["amount"]' in ROUTE
    assert 'late_fee = charge["late_fee"]' in ROUTE
    assert 'total = charge["outstanding"]' in ROUTE


def test_tenant_body_cannot_choose_financial_values():
    assert 'data.get("late_fee")' not in ROUTE
    assert 'data.get("rent_amount")' not in ROUTE
    assert 'data.get("amount")' not in ROUTE


def test_charge_is_resolved_before_claim_and_provider_calls():
    resolver_pos = ROUTE.index("await resolve_hosted_rent_charge(")
    claim_pos = ROUTE.index("await db.rental_payments.insert_one(claim_doc)")
    stripe_pos = ROUTE.index("stripe_lib.checkout.Session.create(")
    hosted_pos = ROUTE.index("await core.create_hosted_checkout(")
    assert resolver_pos < claim_pos < stripe_pos
    assert resolver_pos < claim_pos < hosted_pos


def test_provider_amount_is_exact_outstanding_balance():
    assert '"unit_amount": int(round(total * 100))' in ROUTE
    assert "amount_cents=int(round(total * 100))" in ROUTE
    assert '"amount": total' in ROUTE


def test_claim_keeps_invoice_traceability_and_existing_idempotency():
    assert '"invoice_id": charge["invoice_id"]' in ROUTE
    assert '"invoice_total_due": charge["total_due"]' in ROUTE
    assert '"invoice_total_paid": charge["total_paid"]' in ROUTE
    assert 'idempotency_key=f"hosted-rent:{claim_id}"' in ROUTE
    assert "await db.rental_payments.insert_one(claim_doc)" in ROUTE
    assert '"status": "checkout_creation_unknown"' in ROUTE
