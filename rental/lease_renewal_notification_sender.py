"""Safe sender/worker for lease-renewal notification intents.

The outbox is authoritative. Workers atomically claim one eligible intent and
re-resolve the exact proposal, active contract, property and tenant contact at
send time. A provider timeout/transport exception is terminally ambiguous and
is never retried automatically.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pymongo import ReturnDocument

from .lease_renewal_security_router import _assert_current_contract
from .shared import auth_admin, get_db

logger = logging.getLogger("lease_renewal_notification_sender")
router = APIRouter(prefix="/admin/lease-renewals/notification-outbox", tags=["Lease Renewal Notification Sender"])

MAX_ATTEMPTS = 3
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ProviderRetryableFailure(Exception):
    """Provider definitely rejected before accepting; retry can be safe."""


class ProviderTerminalFailure(Exception):
    """Provider definitely rejected permanently."""


class ProviderAmbiguousResult(Exception):
    """Request may have reached provider; automatic retry is unsafe."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker_identity(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value or "worker"))
    return cleaned[:80] or "worker"


async def claim_next(db, worker_id: str) -> Optional[Dict[str, Any]]:
    """Atomically claim one eligible intent; never reclaim uncertain claims."""
    now = _now()
    claim_id = uuid.uuid4().hex
    return await db.lease_renewal_notification_outbox.find_one_and_update(
        {
            "status": {"$in": ["pending", "retryable_failure"]},
            "attempts": {"$lt": MAX_ATTEMPTS},
            "$or": [
                {"next_attempt_at": {"$exists": False}},
                {"next_attempt_at": None},
                {"next_attempt_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "status": "claimed",
                "claim_id": claim_id,
                "claimed_by": _worker_identity(worker_id),
                "claimed_at": now,
                "updated_at": now,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def _canonical_delivery(db, intent: Dict[str, Any]) -> Dict[str, Any]:
    proposal_id = str(intent.get("proposal_id") or "").strip()
    if not ObjectId.is_valid(proposal_id):
        raise ProviderTerminalFailure("proposal_binding_invalid")
    proposal = await db.lease_renewal_proposals.find_one({"_id": ObjectId(proposal_id), "status": "approved"})
    if not proposal:
        raise ProviderTerminalFailure("proposal_not_approved")

    for field in ("lease_id", "property_id", "tenant_id"):
        if str(intent.get(field) or "") != str(proposal.get(field) or ""):
            raise ProviderTerminalFailure(f"{field}_binding_changed")

    try:
        canonical = await _assert_current_contract(db, proposal)
    except HTTPException as exc:
        raise ProviderTerminalFailure(str(exc.detail)) from exc

    tenant_id = str(canonical.get("tenant_id") or "").strip()
    if tenant_id != str(intent.get("tenant_id") or "") or not ObjectId.is_valid(tenant_id):
        raise ProviderTerminalFailure("tenant_binding_changed")
    tenants = await db.tenants.find({"_id": ObjectId(tenant_id)}).limit(2).to_list(2)
    if len(tenants) != 1:
        raise ProviderTerminalFailure("canonical_tenant_missing_or_ambiguous")
    email = str(tenants[0].get("email_normalized") or tenants[0].get("email") or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise ProviderTerminalFailure("canonical_tenant_email_invalid")
    if intent.get("channel") != "email":
        raise ProviderTerminalFailure("channel_not_supported")
    subject = str(intent.get("subject") or "").strip()[:240]
    message = str(intent.get("message") or "").strip()[:4000]
    if not subject or not message:
        raise ProviderTerminalFailure("message_invalid")
    return {"email": email, "subject": subject, "message": message}


def _sendgrid_sync(delivery: Dict[str, Any]) -> Dict[str, Any]:
    key = os.getenv("SENDGRID_API_KEY")
    sender = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not key:
        raise ProviderRetryableFailure("provider_not_configured")
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        response = SendGridAPIClient(key).send(Mail(
            from_email=(sender, "Ross House Rentals"),
            to_emails=delivery["email"],
            subject=delivery["subject"],
            plain_text_content=delivery["message"],
        ))
    except ProviderRetryableFailure:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None:
            raise ProviderAmbiguousResult("provider_transport_or_timeout") from exc
        if int(status) == 429 or int(status) >= 500:
            raise ProviderRetryableFailure(f"provider_http_{status}") from exc
        raise ProviderTerminalFailure(f"provider_http_{status}") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status not in (200, 201, 202):
        if status == 429 or status >= 500:
            raise ProviderRetryableFailure(f"provider_http_{status}")
        raise ProviderTerminalFailure(f"provider_http_{status}")
    headers = getattr(response, "headers", {}) or {}
    return {"provider": "sendgrid", "provider_message_id": str(headers.get("X-Message-Id") or "")[:200] or None}


async def send_via_provider(delivery: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_sendgrid_sync, delivery)


async def _finish(db, intent: Dict[str, Any], status: str, **fields: Any) -> bool:
    result = await db.lease_renewal_notification_outbox.update_one(
        {"_id": intent["_id"], "status": "claimed", "claim_id": intent["claim_id"]},
        {"$set": {"status": status, "updated_at": _now(), **fields}},
    )
    return getattr(result, "matched_count", 0) == 1


async def process_claimed(
    db,
    intent: Dict[str, Any],
    sender: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]] = send_via_provider,
) -> str:
    """Send one exact claim and conservatively persist the provider outcome."""
    try:
        delivery = await _canonical_delivery(db, intent)
    except ProviderTerminalFailure as exc:
        await _finish(db, intent, "failed", failure_code=str(exc), automatic_retry_allowed=False)
        return "failed"

    started = _now()
    marked = await db.lease_renewal_notification_outbox.update_one(
        {"_id": intent["_id"], "status": "claimed", "claim_id": intent["claim_id"]},
        {"$set": {"provider_started_at": started, "updated_at": started}},
    )
    if getattr(marked, "matched_count", 0) != 1:
        return "claim_lost"

    try:
        confirmation = await sender(delivery)
    except ProviderAmbiguousResult as exc:
        await _finish(db, intent, "ambiguous_provider_result", failure_code=str(exc), automatic_retry_allowed=False)
        return "ambiguous_provider_result"
    except ProviderRetryableFailure as exc:
        exhausted = int(intent.get("attempts") or 0) >= MAX_ATTEMPTS
        await _finish(
            db, intent, "failed" if exhausted else "retryable_failure",
            failure_code=str(exc), automatic_retry_allowed=not exhausted,
        )
        return "failed" if exhausted else "retryable_failure"
    except ProviderTerminalFailure as exc:
        await _finish(db, intent, "failed", failure_code=str(exc), automatic_retry_allowed=False)
        return "failed"
    except Exception:
        logger.exception("renewal notification provider result ambiguous")
        await _finish(db, intent, "ambiguous_provider_result", failure_code="provider_unclassified_exception", automatic_retry_allowed=False)
        return "ambiguous_provider_result"

    saved = await _finish(
        db, intent, "sent", sent_at=_now(), automatic_retry_allowed=False,
        provider=confirmation.get("provider"),
        provider_message_id=confirmation.get("provider_message_id"),
    )
    return "sent" if saved else "claim_lost_after_provider_confirmation"


async def run_once(db, worker_id: str, sender=send_via_provider) -> str:
    intent = await claim_next(db, worker_id)
    if not intent:
        return "idle"
    return await process_claimed(db, intent, sender)


@router.post("/process-next")
async def process_next(db=Depends(get_db), admin=Depends(auth_admin)):
    actor = admin.get("_id") or admin.get("email") if isinstance(admin, dict) else admin
    outcome = await run_once(db, f"admin:{actor}")
    return {"ok": True, "outcome": outcome}
