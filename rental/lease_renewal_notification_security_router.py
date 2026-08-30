"""Durable, admin-only notification outbox for approved lease renewals.

This layer intentionally does not send email/SMS itself. Approval creates an
idempotent outbox intent keyed by proposal_id. A later sender can resolve the
canonical tenant contact at send time, avoiding direct tenant PII in the outbox.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from .lease_renewal_security_router import (
    _assert_current_contract,
    _proposal,
    _rent,
    _serialize,
    _validated_recommendation,
)
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewal Notifications"])


def _message_for(proposal: Dict[str, Any], canonical: Dict[str, Any]) -> Dict[str, str]:
    recommendation = _validated_recommendation(proposal.get("recommendation"))
    proposed_rent = _rent(proposal.get("proposed_rent"))
    address = str(
        canonical.get("property_address")
        or (canonical.get("_property") or {}).get("address")
        or "su propiedad"
    ).strip()
    end_date = canonical["_canonical_end"].date().isoformat()

    if recommendation == "non_renew":
        subject = "Actualización sobre su contrato de arrendamiento"
        body = (
            f"Su contrato para {address}, con vencimiento {end_date}, requiere una conversación "
            "con nuestra oficina sobre las opciones al finalizar el término actual. "
            "Por favor comuníquese con Ross House Rentals para revisar los próximos pasos."
        )
    elif recommendation == "raise":
        subject = "Propuesta de renovación de contrato"
        body = (
            f"Tenemos una propuesta de renovación para {address}, cuyo contrato vence {end_date}. "
            f"La renta propuesta es ${proposed_rent:,.2f} mensuales. "
            "Por favor comuníquese con Ross House Rentals para revisar y aceptar los términos."
        )
    else:
        subject = "Propuesta de renovación de contrato"
        body = (
            f"Tenemos una propuesta de renovación para {address}, cuyo contrato vence {end_date}. "
            f"La renta propuesta es ${proposed_rent:,.2f} mensuales. "
            "Por favor comuníquese con Ross House Rentals para revisar y aceptar los términos."
        )
    return {"subject": subject[:240], "body": body[:4000]}


async def _ensure_outbox_intent(
    db,
    proposal: Dict[str, Any],
    canonical: Dict[str, Any],
    actor: str,
) -> bool:
    proposal_id = str(proposal.get("_id") or "")
    if not proposal_id or not ObjectId.is_valid(proposal_id):
        raise HTTPException(status_code=409, detail="renewal_proposal_id_invalid_for_outbox")

    message = _message_for(proposal, canonical)
    now = datetime.now(timezone.utc)
    outbox = {
        "proposal_id": proposal_id,
        "lease_id": str(proposal.get("lease_id") or ""),
        "property_id": str(proposal.get("property_id") or ""),
        "tenant_id": str(canonical.get("tenant_id") or ""),
        "channel": "email",
        "subject": message["subject"],
        "message": message["body"],
        "status": "pending",
        "attempts": 0,
        "created_by": actor,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.lease_renewal_notification_outbox.update_one(
        {"proposal_id": proposal_id},
        {"$setOnInsert": outbox},
        upsert=True,
    )
    return getattr(result, "upserted_id", None) is not None


@router.post("/{proposal_id}/approve", name="secure_approve_proposal")
async def secure_approve_and_queue(
    proposal_id: str,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    """Approve a draft proposal and ensure exactly one durable notification intent."""
    doc = await _proposal(db, proposal_id)
    if doc.get("status") not in {"draft", "approved"}:
        raise HTTPException(status_code=409, detail="renewal_proposal_transition_invalid")

    canonical = await _assert_current_contract(db, doc)
    _validated_recommendation(doc.get("recommendation"))
    _rent(doc.get("proposed_rent"))
    actor = str(admin.get("email") or admin.get("_id") or "admin") if isinstance(admin, dict) else str(admin)
    now = datetime.now(timezone.utc)

    if doc.get("status") == "draft":
        result = await db.lease_renewal_proposals.update_one(
            {"_id": doc["_id"], "status": "draft", "lease_id": str(doc.get("lease_id") or "")},
            {"$set": {
                "status": "approved",
                "approved_by": actor,
                "approved_at": now,
                "updated_at": now,
            }},
        )
        if getattr(result, "matched_count", 0) != 1:
            latest = await db.lease_renewal_proposals.find_one({"_id": doc["_id"]})
            if not latest or latest.get("status") != "approved":
                raise HTTPException(status_code=409, detail="renewal_proposal_state_changed")
            doc = latest
        else:
            doc = {**doc, "status": "approved", "approved_by": actor, "approved_at": now}

    queued_now = await _ensure_outbox_intent(db, doc, canonical, actor)
    return {"ok": True, "status": "approved", "notification_queued": True, "queued_now": queued_now}


@router.get("/notification-outbox")
async def list_notification_outbox(
    status: Optional[str] = None,
    limit: int = 100,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    del admin
    allowed = {"pending", "claimed", "sent", "retryable_failure", "ambiguous_provider_result", "failed"}
    if status is not None and status not in allowed:
        raise HTTPException(status_code=400, detail="renewal_notification_status_invalid")
    limit = max(1, min(int(limit), 200))
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    docs = await db.lease_renewal_notification_outbox.find(query).sort("created_at", -1).to_list(limit)
    return {"notifications": [_serialize(d) for d in docs], "total": len(docs)}


async def ensure_indexes(db) -> None:
    await db.lease_renewal_notification_outbox.create_index("proposal_id", unique=True)
    await db.lease_renewal_notification_outbox.create_index([("status", 1), ("next_attempt_at", 1), ("created_at", 1)])
    await db.lease_renewal_notification_outbox.create_index("claim_id", sparse=True)
