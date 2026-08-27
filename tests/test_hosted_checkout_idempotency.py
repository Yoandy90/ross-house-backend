"""Regression guards for hosted rent-checkout creation idempotency."""
from pathlib import Path

from rental.payment_processors_router import (
    _HARDENED_WEBHOOK_PATHS,
    _OVERRIDDEN_PATHS,
    _hosted_checkout_claim_id,
)


def test_claim_id_is_stable_per_contract_month_and_processor_independent():
    first = _hosted_checkout_claim_id("contract-1", 2026, 8)
    again = _hosted_checkout_claim_id("contract-1", 2026, 8)
    september = _hosted_checkout_claim_id("contract-1", 2026, 9)
    other = _hosted_checkout_claim_id("contract-2", 2026, 8)
    assert first == again
    assert first != september
    assert first != other


def test_existing_webhook_override_contract_is_unchanged():
    assert _HARDENED_WEBHOOK_PATHS == {
        "/webhooks/clover",
        "/webhooks/bofa",
        "/webhooks/hpay",
    }
    assert "/tenant/create-checkout-payment" in _OVERRIDDEN_PATHS


def test_local_claim_occurs_before_any_provider_creation():
    source = Path("rental/payment_processors_router.py").read_text(encoding="utf-8")
    route = source.split('async def tenant_create_checkout_payment', 1)[1].split(
        'def _clover_signature_valid', 1
    )[0]
    claim_pos = route.index("await db.rental_payments.insert_one(claim_doc)")
    stripe_pos = route.index("stripe_lib.checkout.Session.create(")
    hosted_pos = route.index("await core.create_hosted_checkout(")
    assert claim_pos < stripe_pos
    assert claim_pos < hosted_pos


def test_ambiguous_creation_failure_retains_claim():
    source = Path("rental/payment_processors_router.py").read_text(encoding="utf-8")
    route = source.split('async def tenant_create_checkout_payment', 1)[1].split(
        'def _clover_signature_valid', 1
    )[0]
    assert '"status": "checkout_creation_unknown"' in route
    assert "delete_one" not in route
    assert "find_one_and_delete" not in route


def test_stripe_hosted_checkout_has_provider_idempotency_key():
    source = Path("rental/payment_processors_router.py").read_text(encoding="utf-8")
    route = source.split('async def tenant_create_checkout_payment', 1)[1].split(
        'def _clover_signature_valid', 1
    )[0]
    stripe_block = route.split("stripe_lib.checkout.Session.create(", 1)[1].split("\n            )", 1)[0]
    assert 'idempotency_key=f"hosted-rent:{claim_id}"' in stripe_block
