"""Tenant-facing payment-history projection.

Open rent invoices are obligations, not payment activity.  This module keeps
the mobile history limited to settled payments and genuine payment attempts,
while hiding provider/internal state names from the public API.
"""
from __future__ import annotations

from calendar import month_name
from datetime import datetime, timedelta, timezone


SETTLED_STATUSES = frozenset({"completed", "paid"})
PROCESSING_STATUSES = frozenset({
    "pending_checkout", "creating_checkout", "checkout_creation_unknown",
    "pending_verification", "processing", "unknown",
})
OPEN_INVOICE_STATUSES = frozenset({"pending", "late"})
FAILED_STATUSES = frozenset({"failed", "declined", "rejected"})
REFUNDED_STATUSES = frozenset({"refunded", "refund"})
CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})
REVIEW_STATUSES = frozenset({"reconciliation_required", "settlement_review_required"})
REVIEW_AFTER = timedelta(minutes=30)

PUBLIC_ACTIVITY_STATUSES = (
    SETTLED_STATUSES | PROCESSING_STATUSES | FAILED_STATUSES |
    REFUNDED_STATUSES | CANCELLED_STATUSES | REVIEW_STATUSES |
    OPEN_INVOICE_STATUSES | {"partial"}
)


def payment_activity_query(contract_ids: list[str]) -> dict:
    """Mongo query that excludes untouched current/future rent invoices."""
    return {
        "contract_id": {"$in": contract_ids},
        "record_type": {"$ne": "checkout_attempt"},
        "$or": [
            {"status": {"$in": sorted(PUBLIC_ACTIVITY_STATUSES)}},
            {"charge_attempt": {"$exists": True}},
        ],
    }


def _status(value) -> str:
    return str(value or "").strip().lower()


def is_payment_activity(payment: dict) -> bool:
    """Reject plain invoices even when a broad legacy query returns them."""
    if payment.get("record_type") == "checkout_attempt":
        return False
    status = _status(payment.get("status"))
    if status == "partial":
        return (
            float(payment.get("total_paid") or 0) > 0 or
            bool(payment.get("charge_attempt"))
        )
    if status in OPEN_INVOICE_STATUSES and not payment.get("charge_attempt"):
        return False
    return status in PUBLIC_ACTIVITY_STATUSES or bool(payment.get("charge_attempt"))


def is_payment_timeline_candidate(payment: dict) -> bool:
    """Include activity plus plain invoices needed to select the next month."""
    if is_payment_activity(payment):
        return True
    return (
        payment.get("record_type") != "checkout_attempt"
        and _status(payment.get("status")) in OPEN_INVOICE_STATUSES
        and bool(payment_period(payment))
    )


def _as_utc(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def payment_attempt_requires_review(payment: dict, now: datetime | None = None) -> bool:
    """Flag an unresolved attempt that outlived the normal checkout return window.

    This changes only the tenant-facing label. It never releases the claim or
    guesses whether the provider moved money.
    """
    status = _status(payment.get("status"))
    attempt = payment.get("charge_attempt") or {}
    attempt_status = _status(attempt.get("status"))
    if status in REVIEW_STATUSES or attempt_status in REVIEW_STATUSES:
        return True
    if status not in PROCESSING_STATUSES and attempt_status not in {"processing", "unknown"}:
        return False
    started = _as_utc(
        attempt.get("created_at") or payment.get("submitted_at") or payment.get("created_at")
    )
    if not started:
        return False
    current = _as_utc(now or datetime.now(timezone.utc))
    return bool(current and current - started >= REVIEW_AFTER)


def normalize_payment_status(payment: dict, now: datetime | None = None) -> str:
    """Return stable, tenant-safe status vocabulary."""
    status = _status(payment.get("status"))
    if status in SETTLED_STATUSES:
        return "completed"
    if status in REFUNDED_STATUSES:
        return "refunded"
    if status in FAILED_STATUSES:
        return "failed"
    if status in CANCELLED_STATUSES:
        return "cancelled"
    if status == "partial":
        return "partial"
    if status in OPEN_INVOICE_STATUSES and not payment.get("charge_attempt"):
        return "pending"
    if payment_attempt_requires_review(payment, now):
        return "review_required"
    attempt_status = _status((payment.get("charge_attempt") or {}).get("status"))
    if attempt_status in FAILED_STATUSES:
        return "failed"
    return "processing"


def payment_period(payment: dict) -> str:
    """Return canonical YYYY-MM for legacy and current rows."""
    period = str(payment.get("period") or "")
    if len(period) >= 7 and period[4] == "-":
        return period[:7]
    try:
        year = int(payment.get("period_year") or 0)
        month = int(payment.get("period_month_num") or 0)
    except (TypeError, ValueError):
        year = month = 0
    if not 1 <= month <= 12:
        label = str(payment.get("period_month") or "").strip().lower()
        month = next(
            (index for index in range(1, 13)
             if label and month_name[index].lower().startswith(label[:3])),
            0,
        )
    if year > 0 and 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    return ""


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def payment_activity_date(payment: dict, normalized_status: str) -> str:
    """Use settlement time for paid rows and attempt time otherwise."""
    if normalized_status in {"completed", "refunded"}:
        keys = ("payment_date", "paid_at", "completed_at", "updated_at", "created_at")
    else:
        keys = ("submitted_at",)
    for key in keys:
        value = payment.get(key)
        if value:
            return _iso(value)
    attempt = payment.get("charge_attempt") or {}
    if attempt.get("created_at"):
        return _iso(attempt["created_at"])
    for key in ("updated_at", "created_at"):
        if payment.get(key):
            return _iso(payment[key])
    return ""


def serialize_payment_activity(payment: dict) -> dict:
    from rental.manual_payment_confirmation import recorded_paid_amount
    status = normalize_payment_status(payment)
    paid_amount = float(payment.get("total_paid") or 0)
    amount = float((payment.get("charge_attempt") or {}).get("amount")
                   or payment.get("amount") or 0)
    confirmed_paid = recorded_paid_amount(payment) if status == "completed" else (paid_amount if status == "partial" else 0.0)
    if status == 'completed':
        amount = confirmed_paid
    return {
        "id": str(payment.get("_id", "")),
        "receipt_number": payment.get("receipt_number", ""),
        "amount": amount,
        "late_fee": float(payment.get("late_fee") or 0),
        "total_paid": confirmed_paid,
        "payment_method": payment.get("payment_method", ""),
        "reference_number": payment.get("reference_number", ""),
        "period": payment_period(payment),
        "period_month": payment.get("period_month", ""),
        "period_year": payment.get("period_year", 0),
        "payment_date": payment_activity_date(payment, status),
        "status": status,
        "notes": payment.get("notes", ""),
    }


def collapse_payment_periods(items: list[dict]) -> list[dict]:
    """Expose one tenant-facing row per rent period.

    Legacy data may contain more than one local row for the same month. A
    settled row always wins; otherwise the most recent activity represents the
    current state of that month.
    """
    by_period: dict[str, dict] = {}
    without_period = []
    priority = {
        "completed": 6,
        "refunded": 5,
        "partial": 4,
        "review_required": 3,
        "processing": 3,
        "pending": 2,
        "failed": 2,
        "cancelled": 1,
    }
    for item in items:
        period = str(item.get("period") or "")
        if not period:
            without_period.append(item)
            continue
        current = by_period.get(period)
        candidate_key = (
            priority.get(str(item.get("status") or ""), 0),
            str(item.get("payment_date") or ""),
        )
        current_key = (
            priority.get(str((current or {}).get("status") or ""), 0),
            str((current or {}).get("payment_date") or ""),
        )
        if current is None or candidate_key > current_key:
            by_period[period] = item
    return list(by_period.values()) + without_period


def sequential_payment_timeline(items: list[dict]) -> list[dict]:
    """Return settled months plus only the oldest unresolved rent month.

    The database retains every invoice and provider attempt for audit and
    reconciliation. The tenant timeline is deliberately mortgage-like: paid
    months accumulate, while only the next unpaid month is presented as open.
    """
    collapsed = collapse_payment_periods(items)
    settled = [item for item in collapsed if item.get("status") == "completed"]
    open_statuses = {"pending", "partial", "processing", "review_required"}
    unresolved = [
        item for item in collapsed
        if item.get("status") in open_statuses and item.get("period")
    ]
    next_open = min(unresolved, key=lambda item: str(item.get("period"))) \
        if unresolved else None
    return settled + ([next_open] if next_open else [])
