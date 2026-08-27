from datetime import datetime, timezone
from pathlib import Path

import pytest

from rental.autopay_cron import _claim_monthly_attempt


class Result:
    def __init__(self, count):
        self.modified_count = count


class Collection:
    def __init__(self, count=1):
        self.count = count
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        return Result(self.count)


class DB:
    def __init__(self, count=1):
        self.autopay_config = Collection(count)


@pytest.mark.asyncio
async def test_atomic_claim_persists_exact_invoice_and_amount_with_processing_marker():
    db = DB(1)
    now = datetime(2026, 8, 27, 19, 30, tzinfo=timezone.utc)
    assert await _claim_monthly_attempt(
        db,
        "ap-1",
        now,
        invoice_id="inv-123",
        amount=650.0,
    ) is True

    query, update = db.autopay_config.calls[0]
    assert query["_id"] == "ap-1"
    assert query["enabled"] is True
    assert update["$set"] == {
        "last_attempt_date": now,
        "last_attempt_status": "processing",
        "last_attempt_invoice_id": "inv-123",
        "last_attempt_amount": 650.0,
    }


@pytest.mark.asyncio
async def test_losing_claim_does_not_change_one_attempt_semantics():
    db = DB(0)
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert await _claim_monthly_attempt(
        db,
        "ap-1",
        now,
        invoice_id="inv-123",
        amount=650.0,
    ) is False
    assert len(db.autopay_config.calls) == 1


def test_both_provider_paths_claim_exact_invoice_before_provider_call():
    source = Path("rental/autopay_cron.py").read_text(encoding="utf-8")

    helcim_call = source.index("tx = await helcim_purchase_with_token(")
    helcim_prefix = source[:helcim_call]
    helcim_claim = helcim_prefix.rfind("_claim_monthly_attempt(")
    assert helcim_claim != -1
    helcim_block = helcim_prefix[helcim_claim:]
    assert "invoice_id=invoice_id" in helcim_block
    assert "amount=total" in helcim_block

    stripe_call = source.index("intent = stripe.PaymentIntent.create(")
    stripe_prefix = source[:stripe_call]
    stripe_claim = stripe_prefix.rfind("_claim_monthly_attempt(")
    assert stripe_claim != -1
    stripe_block = stripe_prefix[stripe_claim:]
    assert "invoice_id=invoice_id" in stripe_block
    assert "amount=total" in stripe_block


def test_ambiguous_paths_retain_invoice_trace_for_reconciliation():
    source = Path("rental/autopay_cron.py").read_text(encoding="utf-8")
    assert source.count('"last_attempt_invoice_id": str(invoice_id)') >= 4
    assert '"last_attempt_status": "failed_unknown"' in source
    assert '"last_attempt_status": "reconciliation_required"' in source
    assert '"$unset": {"last_attempt_date"' not in source
