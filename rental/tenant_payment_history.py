"""Tenant-facing payment-history projection.

Open rent invoices are obligations, not payment activity.  This module keeps
the mobile history limited to settled payments and genuine payment attempts,
while hiding provider/internal state names from the public API.
"""
from __future__ import annotations

from calendar import month_name
from datetime import datetime


SETTLED_STATUSES = frozenset({"completed", "paid"})
PROCESSING_STATUSES = frozenset({
    "pending_checkout", "creating_checkout", "checkout_creation_unknown",
    "pending_verification", "processing", "unknown",
})
FAILED_STATUSES = frozenset({"failed", "declined", "rejected"})
REFUNDED_STATUSES = frozenset({"refunded", "refund"})
CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})

PUBLIC_ACTIVITY_STATUSES = (
    SETTLED_STATUSES | PROCESSING_STATUSES | FAILED_STATUSES |
    REFUNDED_STATUSES | CANCELLED_STATUSES | {"partial"}
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
    return status in PUBLIC_ACTIVITY_STATUSES or bool(payment.get("charge_attempt"))


def normalize_payment_status(payment: dict) -> str:
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
    status = normalize_payment_status(payment)
    paid_amount = float(payment.get("total_paid") or 0)
    amount = float(payment.get("amount") or 0)
    return {
        "id": str(payment.get("_id", "")),
        "receipt_number": payment.get("receipt_number", ""),
        "amount": amount,
        "late_fee": float(payment.get("late_fee") or 0),
        "total_paid": paid_amount if paid_amount > 0 else amount,
        "payment_method": payment.get("payment_method", ""),
        "reference_number": payment.get("reference_number", ""),
        "period": payment_period(payment),
        "period_month": payment.get("period_month", ""),
        "period_year": payment.get("period_year", 0),
        "payment_date": payment_activity_date(payment, status),
        "status": status,
        "notes": payment.get("notes", ""),
    }
