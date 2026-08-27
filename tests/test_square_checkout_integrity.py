"""Regression tests for Square hosted-checkout rent payment integrity."""
from pathlib import Path

from rental.payment_processors_router import (
    _square_order_is_paid,
    _square_webhook_can_complete,
)


def test_square_order_type_alone_is_not_payment_proof():
    order = {
        "state": "OPEN",
        "tenders": [{"type": "CARD", "card_details": {"status": "AUTHORIZED"}}],
    }
    assert _square_order_is_paid(order) is False


def test_square_order_without_card_status_is_not_payment_proof():
    order = {"state": "OPEN", "tenders": [{"type": "CARD"}]}
    assert _square_order_is_paid(order) is False


def test_square_captured_card_is_paid():
    order = {
        "state": "OPEN",
        "tenders": [{"type": "CARD", "card_details": {"status": "CAPTURED"}}],
    }
    assert _square_order_is_paid(order) is True


def test_square_completed_order_is_paid_without_tenders():
    assert _square_order_is_paid({"state": "COMPLETED", "tenders": []}) is True


def test_square_unverified_completed_webhook_cannot_settle_rent():
    assert _square_webhook_can_complete({"status": "COMPLETED"}, verified=False) is False


def test_square_verified_approved_webhook_cannot_settle_rent():
    assert _square_webhook_can_complete({"status": "APPROVED"}, verified=True) is False


def test_square_verified_completed_webhook_can_settle_rent():
    assert _square_webhook_can_complete({"status": "COMPLETED"}, verified=True) is True


def test_source_does_not_restore_false_positive_paths():
    source = Path("rental/payment_processors_router.py").read_text(encoding="utf-8")
    assert 'or t.get("type")' not in source
    assert 'if pay_obj.get("status") in ("COMPLETED", "APPROVED")' not in source

    status_fn = source.split(
        'async def tenant_checkout_payment_status(payment_id: str, request: Request):', 1
    )[1].split('async def _try_complete_from_webhook', 1)[0]
    fallback = status_fn.rsplit("if not paid:", 1)[1].split("if paid:", 1)[0]
    assert "matched_payment_id" in fallback
    assert "payload_ids" not in fallback
