"""Regression tests for cross-processor webhook settlement policy."""

from rental.webhook_settlement_policy import (
    bofa_webhook_can_settle,
    clover_webhook_can_settle,
    helcim_transaction_can_settle,
    helcim_webhook_may_lookup,
)


def test_clover_requires_verified_approved():
    assert clover_webhook_can_settle(verified=False, status="APPROVED") is False
    assert clover_webhook_can_settle(verified=True, status="PAID") is False
    assert clover_webhook_can_settle(verified=True, status="SUCCESS") is False
    assert clover_webhook_can_settle(verified=True, status="APPROVED") is True


def test_bofa_requires_verified_accept():
    assert bofa_webhook_can_settle(verified=False, decision="ACCEPT") is False
    assert bofa_webhook_can_settle(verified=True, decision="DECLINE") is False
    assert bofa_webhook_can_settle(verified=True, decision="ACCEPT") is True


def test_helcim_webhook_only_authorizes_provider_lookup():
    assert helcim_webhook_may_lookup(
        verified=False, event_type="cardTransaction", transaction_id="tx_1") is False
    assert helcim_webhook_may_lookup(
        verified=True, event_type="other", transaction_id="tx_1") is False
    assert helcim_webhook_may_lookup(
        verified=True, event_type="cardTransaction", transaction_id="") is False
    assert helcim_webhook_may_lookup(
        verified=True, event_type="cardTransaction", transaction_id="tx_1") is True


def test_helcim_authoritative_transaction_requires_approved_exact_usd_amount():
    good = dict(status="APPROVED", amount_cents=125000,
                expected_amount_cents=125000, currency="USD")
    assert helcim_transaction_can_settle(**good) is True
    assert helcim_transaction_can_settle(**{**good, "status": "DECLINED"}) is False
    assert helcim_transaction_can_settle(**{**good, "amount_cents": 124999}) is False
    assert helcim_transaction_can_settle(**{**good, "currency": "CAD"}) is False
    assert helcim_transaction_can_settle(**{**good, "amount_cents": None}) is False
