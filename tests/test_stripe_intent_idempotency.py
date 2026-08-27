"""Regression coverage for tenant Stripe PaymentIntent creation idempotency."""
from pathlib import Path

from rental.stripe_pkg.tenant_payments_router import _stripe_rent_intent_idempotency_key


def test_rent_intent_key_is_stable_per_contract_month():
    assert _stripe_rent_intent_idempotency_key("contract123", 2026, 8) == "rent:contract123:2026-08"
    assert _stripe_rent_intent_idempotency_key("contract123", 2026, 8) == _stripe_rent_intent_idempotency_key(
        "contract123", 2026, 8)


def test_rent_intent_key_changes_for_different_period_or_contract():
    august = _stripe_rent_intent_idempotency_key("contract123", 2026, 8)
    september = _stripe_rent_intent_idempotency_key("contract123", 2026, 9)
    other_contract = _stripe_rent_intent_idempotency_key("contract999", 2026, 8)
    assert august != september
    assert august != other_contract


def test_payment_intent_create_uses_deterministic_provider_key():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    create_block = source.split("intent = stripe.PaymentIntent.create(", 1)[1].split("\n        )", 1)[0]
    assert "idempotency_key=_stripe_rent_intent_idempotency_key" in create_block
    assert "uuid" not in create_block.lower()


def test_idempotency_key_excludes_financial_amounts():
    # A changed late fee / amount for the same rent period must not mint a new
    # provider identity. Stripe should reject parameter drift on the same key.
    key_source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    helper = key_source.split("def _stripe_rent_intent_idempotency_key", 1)[1].split("\n\n", 1)[0]
    assert "amount" not in helper
    assert "late_fee" not in helper
