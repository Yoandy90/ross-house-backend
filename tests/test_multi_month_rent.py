"""Regression coverage for one-invoice-per-period and multi-month allocation."""
import asyncio
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

from rental import payment_processors_core as core
from rental.payment_processors_router import (
    _hosted_checkout_batch_claim_id,
    _require_next_period_prefix,
    _requested_periods,
)
from rental.rent_payment_cron import canonical_invoice_id, ensure_period_payment
from rental.rent_charge_policy import preview_period_rent_charge


def _contract():
    return {
        "_id": ObjectId(), "tenant_id": str(ObjectId()), "property_id": str(ObjectId()),
        "status": "active", "rent_amount": 1200,
        "start_date": "2026-09-01", "end_date": "2027-08-31",
    }


def test_invoice_and_checkout_ids_are_period_deterministic():
    contract_id = str(ObjectId())
    assert canonical_invoice_id(contract_id, 2026, 9) == canonical_invoice_id(contract_id, 2026, 9)
    assert canonical_invoice_id(contract_id, 2026, 9) != canonical_invoice_id(contract_id, 2026, 10)
    assert _hosted_checkout_batch_claim_id(contract_id, ["2026-09", "2026-10"]) == \
        _hosted_checkout_batch_claim_id(contract_id, ["2026-09", "2026-10"])


def test_requested_periods_rejects_duplicate_month():
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    with pytest.raises(HTTPException) as exc:
        _requested_periods({"periods": ["2026-09", "2026-09"]}, now)
    assert exc.value.status_code == 400


def test_requested_periods_rejects_month_gap():
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    with pytest.raises(HTTPException) as exc:
        _requested_periods({"periods": ["2026-09", "2026-11"]}, now)
    assert exc.value.status_code == 400
    assert "consecutivos" in exc.value.detail


def test_checkout_must_start_with_next_unpaid_month():
    previews = [
        {"period": "2026-09", "payable": True},
        {"period": "2026-10", "payable": True},
        {"period": "2026-11", "payable": True},
    ]
    _require_next_period_prefix(previews, ["2026-09"])
    _require_next_period_prefix(previews, ["2026-09", "2026-10"])
    with pytest.raises(HTTPException) as exc:
        _require_next_period_prefix(previews, ["2026-10"])
    assert exc.value.status_code == 409


def test_concurrent_invoice_generation_creates_one_row():
    async def scenario():
        db = AsyncMongoMockClient()["multi_month_invoice"]
        contract = _contract()
        period = datetime(2026, 10, 1, tzinfo=timezone.utc)
        results = await asyncio.gather(*[
            ensure_period_payment(db, contract, period) for _ in range(8)
        ])
        assert sum(1 for status, _ in results if status == "created") == 1
        assert await db.rental_payments.count_documents({
            "contract_id": str(contract["_id"]), "period": "2026-10",
            "record_type": "invoice",
        }) == 1
    asyncio.run(scenario())


def test_previewing_future_month_does_not_create_an_invoice():
    async def scenario():
        db = AsyncMongoMockClient()["read_only_month_preview"]
        contract = _contract()
        preview = await preview_period_rent_charge(
            db, contract, datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
        assert preview["period"] == "2026-10"
        assert preview["outstanding"] == 1200
        assert await db.rental_payments.count_documents({}) == 0
    asyncio.run(scenario())


def test_multi_month_settlement_updates_each_invoice_once(monkeypatch):
    async def scenario():
        db = AsyncMongoMockClient()["multi_month_settlement"]
        contract = _contract()
        invoices = []
        allocations = []
        for month in (9, 10):
            _, invoice = await ensure_period_payment(
                db, contract, datetime(2026, month, 1, tzinfo=timezone.utc)
            )
            attempt_id = f"attempt-{month}"
            await db.rental_payments.update_one(
                {"_id": invoice["_id"]},
                {"$set": {"charge_attempt": {"id": attempt_id, "status": "processing"}}},
            )
            invoices.append(invoice["_id"])
            allocations.append({
                "invoice_id": str(invoice["_id"]), "period": f"2026-{month:02d}",
                "amount": 1200, "attempt_id": attempt_id,
            })
        checkout = {
            "_id": ObjectId(), "record_type": "checkout_attempt",
            "tenant_id": contract["tenant_id"], "checkout_processor": "helcim",
            "payment_method": "helcim", "total_paid": 2400,
            "invoice_allocations": allocations, "status": "pending_checkout",
        }
        await db.rental_payments.insert_one(checkout)
        monkeypatch.setattr(core, "get_db", lambda: db)
        first = await core._mark_checkout_completed(checkout)
        second = await core._mark_checkout_completed(
            await db.rental_payments.find_one({"_id": checkout["_id"]})
        )
        assert first["receipt_number"] == second["receipt_number"]
        for invoice_id in invoices:
            invoice = await db.rental_payments.find_one({"_id": invoice_id})
            assert invoice["status"] == "completed"
            assert invoice["total_paid"] == 1200
            assert invoice["charge_attempt"]["status"] == "settled"
    asyncio.run(scenario())


def test_dashboard_history_excludes_open_invoices_and_checkout_attempts():
    source = open("rental/tenant_dashboard_security_router.py", encoding="utf-8").read()
    history = source.split("cursor = db.rental_payments.find", 1)[1]
    assert '"record_type": {"$ne": "checkout_attempt"}' in history
    assert '"status": {"$in": ["completed", "paid"]}' in history


def test_settlement_requires_review_when_fee_changed_during_checkout(monkeypatch):
    async def scenario():
        db = AsyncMongoMockClient()['changed_fee']
        invoice_id, checkout_id = ObjectId(), ObjectId()
        await db.rental_payments.insert_one({
            '_id': invoice_id, 'status': 'pending', 'amount': 1200,
            'late_fee': 50, 'total_due': 1250,
            'charge_attempt': {'id': 'attempt', 'status': 'processing'},
        })
        checkout = {'_id': checkout_id, 'status': 'pending_checkout', 'invoice_allocations': [
            {'invoice_id': str(invoice_id), 'attempt_id': 'attempt', 'amount': 1200},
        ]}
        await db.rental_payments.insert_one(checkout)
        monkeypatch.setattr(core, 'get_db', lambda: db)
        with pytest.raises(RuntimeError, match='saldo cambió'):
            await core._mark_checkout_completed(checkout)
        invoice = await db.rental_payments.find_one({'_id': invoice_id})
        assert invoice['status'] == 'pending'
        assert invoice['charge_attempt']['status'] == 'processing'
        assert not invoice.get('paid')
        saved = await db.rental_payments.find_one({'_id': checkout_id})
        assert saved['status'] == 'settlement_review_required'
    asyncio.run(scenario())
