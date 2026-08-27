"""Regression guards for tenant Stripe confirmation integrity."""
from pathlib import Path

from rental.stripe_pkg.tenant_payments_router import (
    _intent_belongs_to_tenant,
    _stripe_payment_identity_query,
)


def test_payment_identity_query_matches_both_legacy_fields_and_reference():
    assert _stripe_payment_identity_query("pi_123") == {
        "$or": [
            {"stripe_payment_intent_id": "pi_123"},
            {"stripe_payment_intent": "pi_123"},
            {"reference_number": "pi_123"},
        ]
    }


def test_intent_must_belong_to_authenticated_tenant():
    assert _intent_belongs_to_tenant({"tenant_id": "tenant-a"}, "tenant-a") is True
    assert _intent_belongs_to_tenant({"tenant_id": "tenant-b"}, "tenant-a") is False
    assert _intent_belongs_to_tenant({}, "tenant-a") is False
    assert _intent_belongs_to_tenant(None, "tenant-a") is False


def test_confirm_endpoint_is_not_a_financial_writer():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    confirm = source.split("async def tenant_confirm_stripe_payment", 1)[1]

    assert "PaymentIntent.retrieve" in confirm
    assert "_intent_belongs_to_tenant" in confirm
    assert "_stripe_payment_identity_query" in confirm
    assert ".rental_payments.insert_one(" not in confirm
    assert ".rental_payments.update_one(" not in confirm


def test_confirm_contract_is_bound_to_authenticated_tenant():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    confirm = source.split("async def tenant_confirm_stripe_payment", 1)[1]
    assert '"tenant_id": tenant["_id"]' in confirm
    assert "El contrato del pago no pertenece a este inquilino" in confirm


def test_mobile_consumer_contract_only_requires_success_flag():
    """Document the API compatibility assumption verified in the mobile caller.

    The current app checks confirm.success and does not require an immediate
    receipt_number/payment_id, so webhook-authoritative persistence is compatible.
    """
    # This test intentionally documents the backend response contract rather than
    # importing the separate React Native repository into backend CI.
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    confirm = source.split("async def tenant_confirm_stripe_payment", 1)[1]
    assert '"success": True' in confirm
    assert '"processing": True' in confirm
