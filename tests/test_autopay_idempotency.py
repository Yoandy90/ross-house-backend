"""Regression tests for monthly autopay double-charge protection."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rental.autopay_cron import (
    _claim_monthly_attempt,
    _month_bounds,
    _stripe_autopay_idempotency_key,
)


class _UpdateResult:
    def __init__(self, modified_count: int):
        self.modified_count = modified_count


class _FakeAutopayCollection:
    def __init__(self, modified_count: int):
        self.modified_count = modified_count
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        return _UpdateResult(self.modified_count)


class _FakeDB:
    def __init__(self, modified_count: int):
        self.autopay_config = _FakeAutopayCollection(modified_count)


def test_month_bounds_handles_december_rollover():
    start, nxt = _month_bounds(datetime(2026, 12, 15, tzinfo=timezone.utc))
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert nxt == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_stripe_idempotency_key_is_stable_per_payment_and_month():
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert _stripe_autopay_idempotency_key("pay123", now) == "autopay:pay123:2026-08"
    assert _stripe_autopay_idempotency_key("pay123", now) == _stripe_autopay_idempotency_key("pay123", now)


@pytest.mark.asyncio
async def test_atomic_claim_requires_enabled_and_only_allows_missing_or_older_attempt():
    db = _FakeDB(modified_count=1)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

    assert await _claim_monthly_attempt(db, "ap1", now) is True
    query, update = db.autopay_config.calls[0]

    assert query["_id"] == "ap1"
    assert query["enabled"] is True
    assert query["$or"] == [
        {"last_attempt_date": {"$exists": False}},
        {"last_attempt_date": None},
        {"last_attempt_date": {"$lt": datetime(2026, 8, 1, tzinfo=timezone.utc)}},
    ]
    assert update["$set"]["last_attempt_date"] == now
    assert update["$set"]["last_attempt_status"] == "processing"


@pytest.mark.asyncio
async def test_atomic_claim_loser_cannot_charge():
    db = _FakeDB(modified_count=0)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert await _claim_monthly_attempt(db, "ap1", now) is False


def test_claim_is_before_every_provider_charge_in_source():
    source = Path("rental/autopay_cron.py").read_text(encoding="utf-8")

    helcim_call = source.index("tx = await helcim_purchase_with_token(")
    helcim_claim = source.rfind("_claim_monthly_attempt", 0, helcim_call)
    assert helcim_claim != -1 and helcim_claim < helcim_call

    stripe_call = source.index("intent = stripe.PaymentIntent.create(")
    stripe_claim = source.rfind("_claim_monthly_attempt", 0, stripe_call)
    assert stripe_claim != -1 and stripe_claim < stripe_call


def test_stripe_provider_call_uses_deterministic_idempotency_key():
    source = Path("rental/autopay_cron.py").read_text(encoding="utf-8")
    stripe_block = source.split("intent = stripe.PaymentIntent.create(", 1)[1].split("\n        )", 1)[0]
    assert "idempotency_key=_stripe_autopay_idempotency_key" in stripe_block


def test_ambiguous_failures_do_not_release_monthly_claim():
    source = Path("rental/autopay_cron.py").read_text(encoding="utf-8")
    assert '"last_attempt_status": "failed_unknown"' in source
    assert '"$unset": {"last_attempt_date"' not in source
