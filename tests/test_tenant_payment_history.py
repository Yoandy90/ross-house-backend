from datetime import datetime, timezone

from bson import ObjectId

from rental.tenant_payment_history import (
    is_payment_activity,
    normalize_payment_status,
    payment_activity_date,
    payment_activity_query,
    payment_period,
    serialize_payment_activity,
)


def test_untouched_future_invoice_is_not_payment_activity():
    invoice = {
        "record_type": "invoice", "status": "pending",
        "period": "2026-10", "amount": 1200,
    }
    assert not is_payment_activity(invoice)
    query = payment_activity_query(["contract-1"])
    assert query["record_type"] == {"$ne": "checkout_attempt"}


def test_internal_checkout_status_is_not_exposed():
    payment = {
        "status": "pending_checkout", "period": "2026-09",
        "created_at": datetime(2026, 9, 14, tzinfo=timezone.utc),
    }
    assert is_payment_activity(payment)
    assert normalize_payment_status(payment) == "processing"
    assert serialize_payment_activity(payment)["status"] == "processing"


def test_new_checkout_container_is_excluded_to_avoid_period_duplicates():
    payment = {
        "record_type": "checkout_attempt", "status": "pending_checkout",
        "periods": ["2026-09", "2026-10"],
    }
    assert not is_payment_activity(payment)


def test_period_normalizes_legacy_month_fields():
    assert payment_period({"period_year": 2026, "period_month": "September"}) == "2026-09"
    assert payment_period({"period_year": "2026", "period_month_num": "10"}) == "2026-10"


def test_completed_payment_uses_settlement_date_and_period():
    paid_at = datetime(2026, 9, 14, 15, 47, tzinfo=timezone.utc)
    item = serialize_payment_activity({
        "_id": ObjectId(), "record_type": "invoice", "status": "paid",
        "period": "2026-09", "amount": 1200, "total_paid": 1200,
        "payment_date": paid_at,
    })
    assert item["status"] == "completed"
    assert item["period"] == "2026-09"
    assert item["payment_date"] == paid_at.isoformat()


def test_processing_activity_uses_attempt_date_when_no_row_date_exists():
    attempt_at = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)
    payment = {
        "status": "pending", "charge_attempt": {
            "status": "processing", "created_at": attempt_at,
        },
    }
    assert is_payment_activity(payment)
    assert normalize_payment_status(payment) == "processing"
    assert payment_activity_date(payment, "processing") == attempt_at.isoformat()


def test_processing_activity_prefers_attempt_over_invoice_creation_date():
    created_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    attempt_at = datetime(2026, 10, 5, tzinfo=timezone.utc)
    payment = {
        "status": "pending", "created_at": created_at,
        "charge_attempt": {"status": "processing", "created_at": attempt_at},
    }
    assert payment_activity_date(payment, "processing") == attempt_at.isoformat()


def test_failed_charge_attempt_is_normalized_from_nested_state():
    payment = {
        "status": "pending", "charge_attempt": {"status": "failed"},
    }
    assert normalize_payment_status(payment) == "failed"
