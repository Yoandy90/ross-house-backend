from datetime import datetime, timezone

import pytest

from rental.rent_charge_policy import (
    current_period_query,
    invoice_balance,
    resolve_current_rent_charge,
)


def test_current_period_query_matches_all_supported_period_encodings():
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    q = current_period_query("contract-1", now)
    assert q["contract_id"] == "contract-1"
    assert {"period": "2026-08"} in q["$or"]
    assert {"period_year": 2026, "period_month_num": 8} in q["$or"]
    assert {"period_year": 2026, "period_month": "August"} in q["$or"]


def test_pending_invoice_uses_total_due_and_ignores_client_concepts():
    result = invoice_balance({
        "status": "pending",
        "amount": 1000,
        "late_fee": 75,
        "total_due": 1075,
        "total_paid": 0,
    })
    assert result == {
        "status": "pending",
        "amount": 1000.0,
        "late_fee": 75.0,
        "total_due": 1075.0,
        "total_paid": 0.0,
        "outstanding": 1075.0,
    }


def test_partial_invoice_charges_only_remaining_balance():
    result = invoice_balance({
        "status": "partial",
        "amount": 1000,
        "late_fee": 50,
        "total_due": 1050,
        "total_paid": 400,
    })
    assert result["outstanding"] == 650.0


def test_legacy_invoice_falls_back_to_amount_plus_server_fee():
    result = invoice_balance({
        "status": "late",
        "amount": 900,
        "late_fee": 60,
        "total_paid": 100,
    })
    assert result["total_due"] == 960.0
    assert result["outstanding"] == 860.0


@pytest.mark.parametrize("status", ["paid", "completed", "cancelled", "canceled"])
def test_settled_or_cancelled_invoice_never_has_chargeable_balance(status):
    result = invoice_balance({
        "status": status,
        "amount": 1000,
        "late_fee": 50,
        "total_due": 1050,
        "total_paid": 0,
    })
    assert result["outstanding"] == 0.0


def test_overpayment_clamps_outstanding_to_zero():
    assert invoice_balance({
        "status": "partial",
        "amount": 1000,
        "total_due": 1000,
        "total_paid": 1200,
    })["outstanding"] == 0.0


class _Payments:
    def __init__(self, docs):
        self.docs = list(docs)
        self.queries = []

    async def find_one(self, query):
        self.queries.append(query)
        return self.docs.pop(0) if self.docs else None


class _DB:
    def __init__(self, docs):
        self.rental_payments = _Payments(docs)


@pytest.mark.asyncio
async def test_existing_invoice_is_returned_without_generation():
    invoice = {
        "_id": "invoice-1",
        "status": "partial",
        "amount": 1000,
        "late_fee": 50,
        "total_due": 1050,
        "total_paid": 250,
    }
    db = _DB([invoice])
    result = await resolve_current_rent_charge(
        db,
        {"_id": "contract-1"},
        datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert result["invoice_id"] == "invoice-1"
    assert result["outstanding"] == 800.0
    assert len(db.rental_payments.queries) == 1
