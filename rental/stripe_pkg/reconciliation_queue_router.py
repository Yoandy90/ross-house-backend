"""Admin-only, read-only queue for payment exceptions that need reconciliation.

This router deliberately exposes no mutation/resolution action. It aggregates a
small sanitized view of financial states where automatic processing failed
closed and a human should investigate before changing any balance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.stripe_pkg.rent_reconciliation import stripe_payment_identity_query

router = APIRouter()

STRIPE_RECONCILIATION_STATUSES = (
    "amount_mismatch",
    "invoice_not_found",
    "tenant_mismatch",
    "invalid_metadata",
)
AUTOPAY_RECONCILIATION_STATUSES = (
    "failed_unknown",
    "reconciliation_required",
)
HOSTED_RECONCILIATION_STATUSES = (
    "creating_checkout",
    "checkout_creation_unknown",
)

_BASE_SEVERITY = {
    "reconciliation_required": 4,
    "amount_mismatch": 3,
    "invoice_not_found": 3,
    "tenant_mismatch": 3,
    "failed_unknown": 3,
    "checkout_creation_unknown": 3,
    "invalid_metadata": 2,
    "creating_checkout": 1,
}
_SEVERITY_LABELS = {1: "low", 2: "medium", 3: "high", 4: "critical"}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _priority(item: dict, now: datetime) -> dict:
    """Attach triage-only age/severity without changing financial state."""
    stamp = _as_utc_datetime(item.get("updated_at"))
    age_seconds = max(0, int((now - stamp).total_seconds())) if stamp else None
    score = _BASE_SEVERITY.get(str(item.get("status") or ""), 2)

    if item.get("status") == "creating_checkout":
        if age_seconds is not None and age_seconds >= 3600:
            score = max(score, 3)
        elif age_seconds is not None and age_seconds >= 300:
            score = max(score, 2)
    elif age_seconds is not None and age_seconds >= 86400:
        score = min(4, score + 1)

    return {
        **item,
        "age_seconds": age_seconds,
        "severity": _SEVERITY_LABELS[score],
        "severity_score": score,
    }


def _hosted_item(doc: dict) -> dict:
    """Return a sanitized hosted-checkout exception; never return provider secrets."""
    return {
        "source": "hosted_checkout",
        "id": str(doc.get("_id", "")),
        "status": str(doc.get("status") or ""),
        "processor": str(doc.get("checkout_processor") or ""),
        "contract_id": str(doc.get("contract_id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "invoice_id": str(doc.get("invoice_id") or ""),
        "amount": float(doc.get("total_paid") or 0),
        "period": str(doc.get("period") or ""),
        "reference_id": str(doc.get("checkout_order_id") or doc.get("checkout_external_id") or ""),
        "updated_at": _iso(doc.get("updated_at") or doc.get("created_at")),
    }


def _stripe_item(doc: dict) -> dict:
    """Return only aggregate Stripe reconciliation status and PI/event identifiers."""
    return {
        "source": "stripe_webhook",
        "id": str(doc.get("_id", "")),
        "status": str(doc.get("reconciliation_status") or ""),
        "processor": "stripe",
        "event_id": str(doc.get("event_id") or ""),
        "reference_id": str(doc.get("account_id") or ""),
        "updated_at": _iso(doc.get("processed_at") or doc.get("created_at")),
    }


def _autopay_item(doc: dict) -> dict:
    """Return autopay operational state without saved payment-method/token fields."""
    return {
        "source": "autopay",
        "id": str(doc.get("_id", "")),
        "status": str(doc.get("last_attempt_status") or ""),
        "processor": str(doc.get("processor") or "stripe"),
        "tenant_id": str(doc.get("user_id") or ""),
        "reference_id": str(doc.get("last_attempt_intent_id") or ""),
        "amount": float(doc.get("last_attempt_amount") or 0),
        "updated_at": _iso(doc.get("last_attempt_date") or doc.get("updated_at")),
    }


def _invoice_snapshot(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {
        "id": str(doc.get("_id", "")),
        "status": str(doc.get("status") or ""),
        "contract_id": str(doc.get("contract_id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "period": str(doc.get("period") or ""),
        "amount": float(doc.get("amount") or 0),
        "late_fee": float(doc.get("late_fee") or 0),
        "total_due": float(doc.get("total_due") or 0),
        "total_paid": float(doc.get("total_paid") or 0),
        "payment_method": str(doc.get("payment_method") or ""),
        "receipt_number": str(doc.get("receipt_number") or ""),
        "reference_number": str(doc.get("reference_number") or ""),
        "updated_at": _iso(doc.get("updated_at") or doc.get("payment_date") or doc.get("created_at")),
    }


def _autopay_snapshot(doc: dict | None) -> dict | None:
    if not doc:
        return None
    return {
        "id": str(doc.get("_id", "")),
        "enabled": bool(doc.get("enabled", False)),
        "processor": str(doc.get("processor") or "stripe"),
        "tenant_id": str(doc.get("user_id") or ""),
        "day_of_month": int(doc.get("day_of_month") or 1),
        "status": str(doc.get("last_attempt_status") or ""),
        "amount": float(doc.get("last_attempt_amount") or 0),
        "reference_id": str(doc.get("last_attempt_intent_id") or ""),
        "retry_count": int(doc.get("retry_count") or 0),
        "updated_at": _iso(doc.get("last_attempt_date") or doc.get("updated_at")),
    }


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


async def _find_by_id(collection, value: str) -> dict | None:
    """Resolve normal ObjectId-backed records without assuming every legacy id is one."""
    oid = _object_id(value)
    if oid is not None:
        doc = await collection.find_one({"_id": oid})
        if doc is not None:
            return doc
    return await collection.find_one({"_id": str(value)})


def _investigation_hint(source: str, status: str) -> str:
    hints = {
        ("hosted_checkout", "creating_checkout"): "Confirm provider-side checkout creation before any new payment attempt.",
        ("hosted_checkout", "checkout_creation_unknown"): "Verify provider transaction/session state before creating or charging again.",
        ("stripe_webhook", "amount_mismatch"): "Compare the Stripe PI amount with the current canonical invoice balance; do not auto-credit.",
        ("stripe_webhook", "invoice_not_found"): "Locate the intended canonical invoice using contract/period records before any balance change.",
        ("stripe_webhook", "tenant_mismatch"): "Verify tenant and contract ownership before any reconciliation action.",
        ("stripe_webhook", "invalid_metadata"): "Review the PaymentIntent origin and canonical invoice linkage before any reconciliation action.",
        ("autopay", "failed_unknown"): "Confirm provider outcome before any manual retry because the remote charge may have succeeded.",
        ("autopay", "reconciliation_required"): "Provider charge was observed; reconcile the canonical invoice before any new charge attempt.",
    }
    return hints.get((source, status), "Investigate source records before changing any financial state.")


async def _collect(cursor, mapper, limit: int) -> list[dict]:
    items: list[dict] = []
    async for doc in cursor:
        items.append(mapper(doc))
        if len(items) >= limit:
            break
    return items


@router.get("/admin/payment-reconciliation")
async def admin_payment_reconciliation(request: Request, limit: int = 100):
    """List sanitized payment exceptions requiring human investigation."""
    await auth_admin(request)
    db = get_db()
    safe_limit = max(1, min(int(limit or 100), 200))
    now = datetime.now(timezone.utc)

    hosted = await _collect(
        db.rental_payments.find({
            "status": {"$in": list(HOSTED_RECONCILIATION_STATUSES)},
        }).sort("updated_at", -1).limit(safe_limit),
        _hosted_item,
        safe_limit,
    )
    stripe = await _collect(
        db.stripe_webhook_events.find({
            "reconciliation_status": {"$in": list(STRIPE_RECONCILIATION_STATUSES)},
        }).sort("processed_at", -1).limit(safe_limit),
        _stripe_item,
        safe_limit,
    )
    autopay = await _collect(
        db.autopay_config.find({
            "last_attempt_status": {"$in": list(AUTOPAY_RECONCILIATION_STATUSES)},
        }).sort("last_attempt_date", -1).limit(safe_limit),
        _autopay_item,
        safe_limit,
    )

    items = [_priority(item, now) for item in hosted + stripe + autopay]
    items.sort(
        key=lambda item: (
            item.get("severity_score") or 0,
            item.get("age_seconds") or 0,
            item.get("updated_at") or "",
        ),
        reverse=True,
    )
    items = items[:safe_limit]

    counts: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for item in items:
        source = item["source"]
        severity = item["severity"]
        counts[source] = counts.get(source, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1

    return {
        "items": items,
        "count": len(items),
        "by_source": counts,
        "by_severity": by_severity,
        "read_only": True,
    }


@router.get("/admin/payment-reconciliation/{source}/{item_id}")
async def admin_payment_reconciliation_detail(request: Request, source: str, item_id: str):
    """Return one sanitized investigation snapshot without mutating financial state."""
    await auth_admin(request)
    db = get_db()
    now = datetime.now(timezone.utc)
    normalized_source = str(source or "").strip().lower()

    if normalized_source == "hosted_checkout":
        doc = await _find_by_id(db.rental_payments, item_id)
        if not doc or str(doc.get("status") or "") not in HOSTED_RECONCILIATION_STATUSES:
            raise HTTPException(status_code=404, detail="Reconciliation item not found")
        item = _priority(_hosted_item(doc), now)
        invoice = None
        invoice_id = str(doc.get("invoice_id") or "")
        if invoice_id:
            invoice = _invoice_snapshot(await _find_by_id(db.rental_payments, invoice_id))
        return {
            "item": item,
            "invoice": invoice,
            "autopay": None,
            "investigation_hint": _investigation_hint(normalized_source, item["status"]),
            "read_only": True,
        }

    if normalized_source == "stripe_webhook":
        doc = await _find_by_id(db.stripe_webhook_events, item_id)
        if not doc or str(doc.get("reconciliation_status") or "") not in STRIPE_RECONCILIATION_STATUSES:
            raise HTTPException(status_code=404, detail="Reconciliation item not found")
        item = _priority(_stripe_item(doc), now)
        payment = None
        pi_id = str(doc.get("account_id") or "")
        if pi_id:
            payment = await db.rental_payments.find_one(stripe_payment_identity_query(pi_id))
        return {
            "item": item,
            "invoice": _invoice_snapshot(payment),
            "autopay": None,
            "investigation_hint": _investigation_hint(normalized_source, item["status"]),
            "read_only": True,
        }

    if normalized_source == "autopay":
        doc = await _find_by_id(db.autopay_config, item_id)
        if not doc or str(doc.get("last_attempt_status") or "") not in AUTOPAY_RECONCILIATION_STATUSES:
            raise HTTPException(status_code=404, detail="Reconciliation item not found")
        item = _priority(_autopay_item(doc), now)
        return {
            "item": item,
            "invoice": None,
            "autopay": _autopay_snapshot(doc),
            "investigation_hint": _investigation_hint(normalized_source, item["status"]),
            "read_only": True,
        }

    raise HTTPException(status_code=404, detail="Reconciliation item not found")
