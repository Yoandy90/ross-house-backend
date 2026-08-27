"""Admin-only, read-only queue for payment exceptions that need reconciliation.

This router deliberately exposes no mutation/resolution action. It aggregates a
small sanitized view of financial states where automatic processing failed
closed and a human should investigate before changing any balance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from rental.shared import auth_admin, get_db

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
