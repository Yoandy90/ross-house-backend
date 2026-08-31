"""Two-person, fail-closed recovery for uncertain renewal deliveries.

Recovery is intentionally manual. It never contacts the provider, never
requeues an uncertain send, and never exposes the canonical tenant contact.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException

from .lease_renewal_notification_sender import _canonical_delivery
from .lease_renewal_security_router import _serialize
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals/notification-outbox", tags=["Lease Renewal Delivery Recovery"])

RECOVERY_MIN_CLAIM_AGE = timedelta(minutes=15)
_RECOVERABLE = {"claimed", "ambiguous_provider_result"}
_EVIDENCE_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,300}$")
_REASONS = {
    "sent": {
        "provider_dashboard_confirmed_delivered",
        "provider_event_confirmed_delivered",
        "provider_support_confirmed_delivered",
    },
    "failed": {
        "provider_dashboard_confirmed_not_accepted",
        "provider_support_confirmed_not_accepted",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: Any) -> ObjectId:
    value = str(value or "").strip()
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail="renewal_notification_id_invalid")
    return ObjectId(value)


def _actor(admin: Any) -> Dict[str, Any]:
    if not isinstance(admin, dict):
        raise HTTPException(status_code=403, detail="renewal_recovery_admin_identity_missing")
    actor_id = str(admin.get("_id") or admin.get("id") or "").strip()
    email = str(admin.get("email") or "").strip().lower()
    keys = []
    if actor_id:
        keys.append(f"id:{actor_id}")
    if email:
        keys.append(f"email:{email}")
    if not keys:
        raise HTTPException(status_code=403, detail="renewal_recovery_admin_identity_missing")
    return {"ref": actor_id or email, "keys": keys}


def _safe_age(value: Any, now: datetime) -> timedelta:
    if not isinstance(value, datetime):
        raise HTTPException(status_code=409, detail="renewal_recovery_claim_time_missing")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return now - value.astimezone(timezone.utc)


def _assert_recoverable(doc: Dict[str, Any], now: datetime) -> None:
    status = doc.get("status")
    if status not in _RECOVERABLE:
        raise HTTPException(status_code=409, detail="renewal_delivery_not_recoverable")
    if status == "claimed":
        anchor = doc.get("provider_started_at") or doc.get("claimed_at")
        if _safe_age(anchor, now) < RECOVERY_MIN_CLAIM_AGE:
            raise HTTPException(status_code=409, detail="renewal_delivery_claim_still_fresh")


async def _intent(db, notification_id: str) -> Dict[str, Any]:
    doc = await db.lease_renewal_notification_outbox.find_one({"_id": _oid(notification_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="renewal_notification_not_found")
    return doc


async def _canonical_health(db, doc: Dict[str, Any]) -> Dict[str, Any]:
    try:
        await _canonical_delivery(db, doc)
        return {"valid": True, "failure_code": None}
    except Exception as exc:
        # Only bounded internal error codes are returned; never recipient data.
        code = re.sub(r"[^a-z0-9_]", "_", str(exc).lower())[:120]
        return {"valid": False, "failure_code": code or "canonical_validation_failed"}


@router.get("/{notification_id}/delivery-inspection")
async def inspect_delivery(notification_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    del admin
    doc = await _intent(db, notification_id)
    recovery = doc.get("recovery") if isinstance(doc.get("recovery"), dict) else None
    return {
        "notification_id": str(doc["_id"]),
        "proposal_id": str(doc.get("proposal_id") or ""),
        "lease_id": str(doc.get("lease_id") or ""),
        "property_id": str(doc.get("property_id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "status": doc.get("status"),
        "channel": doc.get("channel"),
        "attempts": int(doc.get("attempts") or 0),
        "claimed_at": doc.get("claimed_at"),
        "provider_started_at": doc.get("provider_started_at"),
        "provider": doc.get("provider"),
        "provider_message_id": doc.get("provider_message_id"),
        "failure_code": doc.get("failure_code"),
        "automatic_retry_allowed": bool(doc.get("automatic_retry_allowed", False)),
        "claim_present": bool(doc.get("claim_id")),
        "canonical": await _canonical_health(db, doc),
        "recovery": _serialize(recovery) if recovery else None,
    }


@router.post("/{notification_id}/delivery-resolution/propose")
async def propose_resolution(
    notification_id: str,
    body: Dict[str, Any] = Body(...),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="renewal_recovery_payload_invalid")
    outcome = str(body.get("outcome") or "").strip()
    reason = str(body.get("reason") or "").strip()
    evidence = str(body.get("provider_evidence_reference") or "").strip()
    if outcome not in _REASONS or reason not in _REASONS[outcome]:
        raise HTTPException(status_code=400, detail="renewal_recovery_outcome_or_reason_invalid")
    if not _EVIDENCE_RE.fullmatch(evidence):
        raise HTTPException(status_code=400, detail="renewal_recovery_evidence_reference_invalid")

    doc = await _intent(db, notification_id)
    now = _now()
    _assert_recoverable(doc, now)
    expected_status = str(body.get("expected_status") or "")
    expected_attempts = body.get("expected_attempts")
    if expected_status != doc.get("status") or expected_attempts != int(doc.get("attempts") or 0):
        raise HTTPException(status_code=409, detail="renewal_recovery_snapshot_stale")
    actor = _actor(admin)
    resolution_id = uuid.uuid4().hex
    recovery = {
        "resolution_id": resolution_id,
        "state": "pending_confirmation",
        "outcome": outcome,
        "reason": reason,
        "provider_evidence_reference": evidence,
        "expected_status": expected_status,
        "expected_attempts": expected_attempts,
        "proposed_by": actor["ref"],
        "proposer_keys": actor["keys"],
        "proposed_at": now,
    }
    query = {
        "_id": doc["_id"], "status": expected_status,
        "attempts": expected_attempts,
        "$or": [{"recovery": {"$exists": False}}, {"recovery.state": {"$ne": "pending_confirmation"}}],
    }
    if doc.get("claim_id"):
        query["claim_id"] = doc["claim_id"]
    result = await db.lease_renewal_notification_outbox.update_one(
        query, {"$set": {"recovery": recovery, "updated_at": now}}
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_recovery_state_changed")
    return {"ok": True, "resolution_id": resolution_id, "state": "pending_confirmation"}


@router.post("/{notification_id}/delivery-resolution/{resolution_id}/confirm")
async def confirm_resolution(
    notification_id: str,
    resolution_id: str,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    doc = await _intent(db, notification_id)
    now = _now()
    _assert_recoverable(doc, now)
    recovery = doc.get("recovery") or {}
    if recovery.get("state") != "pending_confirmation" or recovery.get("resolution_id") != resolution_id:
        raise HTTPException(status_code=409, detail="renewal_recovery_resolution_invalid")
    actor = _actor(admin)
    if set(actor["keys"]) & set(recovery.get("proposer_keys") or []):
        raise HTTPException(status_code=409, detail="renewal_recovery_distinct_confirmer_required")

    outcome = recovery.get("outcome")
    if outcome not in {"sent", "failed"}:
        raise HTTPException(status_code=409, detail="renewal_recovery_outcome_invalid")
    completed = {
        **recovery,
        "state": "confirmed",
        "confirmed_by": actor["ref"],
        "confirmer_keys": actor["keys"],
        "confirmed_at": now,
    }
    fields = {
        "status": outcome,
        "recovery": completed,
        "automatic_retry_allowed": False,
        "updated_at": now,
    }
    if outcome == "sent":
        fields["sent_at"] = now
        fields["sent_resolution"] = "manual_provider_evidence"
    else:
        fields["failure_code"] = "manual_provider_evidence_not_accepted"

    query = {
        "_id": doc["_id"],
        "status": recovery.get("expected_status"),
        "attempts": recovery.get("expected_attempts"),
        "recovery.resolution_id": resolution_id,
        "recovery.state": "pending_confirmation",
    }
    if doc.get("claim_id"):
        query["claim_id"] = doc["claim_id"]
    result = await db.lease_renewal_notification_outbox.update_one(query, {"$set": fields})
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_recovery_state_changed")
    return {"ok": True, "status": outcome, "automatic_retry_allowed": False}

