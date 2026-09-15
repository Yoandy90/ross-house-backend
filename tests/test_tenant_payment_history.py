from datetime import datetime, timezone

from bson import ObjectId

from rental.tenant_payment_history import (
    collapse_payment_periods,
    is_payment_activity,
    normalize_payment_status,
    payment_activity_date,
    payment_activity_query,
    payment_period,
    serialize_payment_activity,
    payment_attempt_requires_review,
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
    now = datetime(2026, 9, 14, 0, 15, tzinfo=timezone.utc)
    assert normalize_payment_status(payment, now) == "processing"


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
    assert normalize_payment_status(
        payment, datetime(2026, 9, 14, 16, 15, tzinfo=timezone.utc)
    ) == "processing"
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


def test_duplicate_legacy_rows_collapse_to_one_period():
    items = [
        {"id": "old", "period": "2026-09", "status": "processing",
         "payment_date": "2026-09-14T10:00:00"},
        {"id": "new", "period": "2026-09", "status": "processing",
         "payment_date": "2026-09-14T11:00:00"},
        {"id": "oct", "period": "2026-10", "status": "processing",
         "payment_date": "2026-09-14T12:00:00"},
    ]
    collapsed = collapse_payment_periods(items)
    assert {item["id"] for item in collapsed} == {"new", "oct"}


def test_settled_row_wins_over_later_stale_attempt():
    items = [
        {"id": "paid", "period": "2026-09", "status": "completed",
         "payment_date": "2026-09-14T10:00:00"},
        {"id": "retry", "period": "2026-09", "status": "processing",
         "payment_date": "2026-09-14T11:00:00"},
    ]
    assert collapse_payment_periods(items) == [items[0]]


def test_unconfirmed_checkout_amount_is_not_reported_as_paid():
    row = serialize_payment_activity({'status': 'pending_checkout', 'amount': 1200, 'total_paid': 1200})
    assert row['total_paid'] == 0
    assert row['amount'] == 1200


def test_activity_uses_attempt_amount_when_invoice_fee_changes():
    row = serialize_payment_activity({'status': 'pending', 'amount': 1200, 'late_fee': 50,
                                      'charge_attempt': {'amount': 1200, 'status': 'processing'}})
    assert row['amount'] == 1200
    assert row['total_paid'] == 0


def test_stale_unconfirmed_attempt_requires_review_without_unlocking_it():
    attempt = {
        "status": "pending",
        "charge_attempt": {
            "id": "keep-this-claim",
            "status": "processing",
            "created_at": datetime(2026, 9, 14, 10, tzinfo=timezone.utc),
        },
    }
    now = datetime(2026, 9, 14, 11, tzinfo=timezone.utc)
    assert payment_attempt_requires_review(attempt, now)
    assert normalize_payment_status(attempt, now) == "review_required"
    assert attempt["charge_attempt"]["id"] == "keep-this-claim"
    assert attempt["charge_attempt"]["status"] == "processing"


def test_recent_unconfirmed_attempt_remains_processing():
    attempt = {
        "status": "pending_checkout",
        "submitted_at": datetime(2026, 9, 14, 10, 45, tzinfo=timezone.utc),
    }
    now = datetime(2026, 9, 14, 11, tzinfo=timezone.utc)
    assert not payment_attempt_requires_review(attempt, now)
    assert normalize_payment_status(attempt, now) == "processing"
