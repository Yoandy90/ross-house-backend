"""Canonical Stripe rent settlement helpers.

The signed Stripe webhook is the only financial writer for native rent
PaymentIntents. It may complete an existing canonical monthly invoice, but it
must never invent a second completed rent row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

CHARGEABLE_STATUSES = ("pending", "late", "partial")


def stripe_payment_identity_query(payment_intent_id: str) -> dict:
    """Match every historical field used to persist a Stripe PaymentIntent id."""
    return {"$or": [
        {"stripe_payment_intent_id": payment_intent_id},
        {"stripe_payment_intent": payment_intent_id},
        {"reference_number": payment_intent_id},
    ]}


def _month_number(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        month = int(text)
        return month if 1 <= month <= 12 else None
    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(text.title(), fmt).month
        except ValueError:
            pass
    return None


def _legacy_period_query(contract_id: str, metadata: Any) -> dict | None:
    """Build a fail-closed fallback for pre-invoice_id PaymentIntents."""
    if not hasattr(metadata, "get"):
        return None
    try:
        year = int(metadata.get("period_year"))
    except (TypeError, ValueError):
        return None
    month = _month_number(metadata.get("period_month_num") or metadata.get("period_month"))
    if month is None:
        return None
    month_name = datetime(year, month, 1).strftime("%B")
    return {
        "contract_id": str(contract_id),
        "status": {"$in": list(CHARGEABLE_STATUSES)},
        "$or": [
            {"period": f"{year:04d}-{month:02d}"},
            {"period_year": year, "period_month_num": month},
            {"period_year": year, "period_month": month_name},
            {"period_year": year, "period_month": {"$regex": f"^{month_name[:3]}", "$options": "i"}},
        ],
    }


async def _find_invoice(db, contract_id: str, metadata: Any) -> dict | None:
    invoice_id = str(metadata.get("invoice_id") or "") if hasattr(metadata, "get") else ""
    if invoice_id:
        try:
            oid = ObjectId(invoice_id)
        except Exception:
            return None
        return await db.rental_payments.find_one({
            "_id": oid,
            "contract_id": str(contract_id),
            "status": {"$in": list(CHARGEABLE_STATUSES)},
        })

    legacy_query = _legacy_period_query(contract_id, metadata)
    if legacy_query is None:
        return None
    return await db.rental_payments.find_one(legacy_query)


def _money_cents(value: Any) -> int:
    return int(round(float(value or 0) * 100))


def _deterministic_receipt(pi_id: str, now: datetime) -> str:
    suffix = "".join(ch for ch in pi_id if ch.isalnum())[-10:].upper() or "STRIPE"
    return f"STR-{now.strftime('%Y%m%d')}-{suffix}"


async def reconcile_succeeded_rent_payment(
    db,
    *,
    payment_intent_id: str,
    amount_cents: int,
    metadata: Any,
    three_ds_evidence: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Apply one succeeded Stripe PI to its canonical monthly rent invoice.

    Returns a small status object and never inserts a new rental_payments row.
    Amount mismatch, missing/ambiguous invoice, or malformed metadata fail closed.
    """
    now = now or datetime.now(timezone.utc)
    pi_id = str(payment_intent_id or "").strip()
    if not pi_id or amount_cents <= 0 or not hasattr(metadata, "get"):
        return {"status": "invalid", "settled": False}

    existing = await db.rental_payments.find_one(stripe_payment_identity_query(pi_id))
    if existing is not None:
        return {
            "status": "duplicate",
            "settled": str(existing.get("status") or "").lower() in {"paid", "completed"},
            "payment_id": str(existing.get("_id", "")),
            "receipt_number": existing.get("receipt_number", ""),
        }

    contract_id = str(metadata.get("contract_id") or "").strip()
    tenant_id = str(metadata.get("tenant_id") or "").strip()
    if not contract_id or not tenant_id:
        return {"status": "invalid_metadata", "settled": False}

    invoice = await _find_invoice(db, contract_id, metadata)
    if invoice is None:
        return {"status": "invoice_not_found", "settled": False}

    invoice_tenant = str(invoice.get("tenant_id") or "")
    if invoice_tenant and invoice_tenant != tenant_id:
        return {"status": "tenant_mismatch", "settled": False}

    total_due_cents = _money_cents(invoice.get("total_due") or (
        float(invoice.get("amount") or 0) + float(invoice.get("late_fee") or 0)))
    already_paid_cents = _money_cents(invoice.get("total_paid"))
    outstanding_cents = max(total_due_cents - already_paid_cents, 0)

    # The PI must pay exactly the canonical outstanding balance captured by the
    # server. A stale PI after a partial/manual payment requires reconciliation.
    if outstanding_cents <= 0 or int(amount_cents) != outstanding_cents:
        return {
            "status": "amount_mismatch",
            "settled": False,
            "expected_cents": outstanding_cents,
            "received_cents": int(amount_cents),
        }

    receipt_number = _deterministic_receipt(pi_id, now)
    update_doc = {
        "status": "completed",
        "paid": True,
        "payment_method": "stripe",
        "payment_date": now,
        "total_paid": round(total_due_cents / 100, 2),
        "stripe_payment_intent_id": pi_id,
        "reference_number": pi_id,
        "three_ds": three_ds_evidence,
        "receipt_number": receipt_number,
        "updated_at": now,
    }

    result = await db.rental_payments.update_one(
        {"_id": invoice["_id"], "status": {"$in": list(CHARGEABLE_STATUSES)}},
        {"$set": update_doc},
    )
    if getattr(result, "modified_count", 0) == 1:
        return {
            "status": "completed",
            "settled": True,
            "payment_id": str(invoice["_id"]),
            "receipt_number": receipt_number,
        }

    # Concurrent webhook retry may have won the status transition. Re-read by
    # PI identity; only the same persisted PI is considered idempotent success.
    existing = await db.rental_payments.find_one(stripe_payment_identity_query(pi_id))
    if existing is not None:
        return {
            "status": "duplicate",
            "settled": str(existing.get("status") or "").lower() in {"paid", "completed"},
            "payment_id": str(existing.get("_id", "")),
            "receipt_number": existing.get("receipt_number", ""),
        }

    return {"status": "concurrent_change", "settled": False}
