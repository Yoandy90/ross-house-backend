"""Canonical admin lease-renewal boundary.

Renewal proposals are advisory records only. The active lease in
``rental_contracts`` remains the authority for tenant/property identity and
current rent; proposal status changes never mutate lease lifecycle, occupancy,
or payments.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException

from .lease_renewals_router import WINDOW_DAYS, _lease_market_context, _llm_analyze, _serialize
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewal Security"])

_RECOMMENDATIONS = {"renew", "raise", "non_renew"}
_PROPOSAL_STATUSES = {"draft", "approved", "rejected", "sent"}


def _oid(value: Any, detail: str) -> ObjectId:
    value = str(value or "").strip()
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(value)


def _parse_end(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="renewal_contract_end_date_invalid") from exc
    else:
        raise HTTPException(status_code=409, detail="renewal_contract_end_date_missing")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _rent(value: Any, detail: str = "renewal_rent_invalid") -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=detail) from exc
    if not math.isfinite(number) or number < 0:
        raise HTTPException(status_code=400, detail=detail)
    return number


def _validated_recommendation(value: Any) -> str:
    recommendation = str(value or "").strip().lower()
    if recommendation not in _RECOMMENDATIONS:
        raise HTTPException(status_code=400, detail="renewal_recommendation_invalid")
    return recommendation


async def _proposal(db, proposal_id: str) -> Dict[str, Any]:
    oid = _oid(proposal_id, "renewal_proposal_id_invalid")
    doc = await db.lease_renewal_proposals.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="renewal_proposal_not_found")
    return doc


async def _assert_current_contract(db, proposal: Dict[str, Any]) -> Dict[str, Any]:
    lease_id = str(proposal.get("lease_id") or "").strip()
    lease_oid = _oid(lease_id, "renewal_proposal_lease_invalid")
    contract = await db.rental_contracts.find_one({"_id": lease_oid, "status": "active"})
    if not contract:
        raise HTTPException(status_code=409, detail="renewal_contract_not_active")

    property_id = str(contract.get("property_id") or "").strip()
    if not property_id or property_id != str(proposal.get("property_id") or "").strip():
        raise HTTPException(status_code=409, detail="renewal_property_binding_changed")

    property_oid = _oid(property_id, "renewal_property_invalid")
    prop = await db.properties.find_one({"_id": property_oid})
    if not prop:
        raise HTTPException(status_code=409, detail="renewal_property_missing")
    if prop.get("archived_at"):
        raise HTTPException(status_code=409, detail="renewal_property_archived")

    tenant_id = str(contract.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=409, detail="renewal_tenant_binding_missing")
    proposal_tenant_id = str(proposal.get("tenant_id") or "").strip()
    if proposal_tenant_id and proposal_tenant_id != tenant_id:
        raise HTTPException(status_code=409, detail="renewal_tenant_binding_changed")

    canonical_end = _parse_end(contract.get("end_date") or contract.get("lease_end_date"))
    snapshot_end = proposal.get("lease_end_date")
    if snapshot_end:
        try:
            proposal_end = _parse_end(snapshot_end)
        except HTTPException:
            raise HTTPException(status_code=409, detail="renewal_proposal_stale")
        if proposal_end.date() != canonical_end.date():
            raise HTTPException(status_code=409, detail="renewal_proposal_stale")

    current_rent = _rent(contract.get("rent_amount", contract.get("monthly_rent", 0)), "renewal_contract_rent_invalid")
    snapshot_rent = proposal.get("current_rent")
    if snapshot_rent is not None:
        try:
            old_rent = float(snapshot_rent)
        except (TypeError, ValueError):
            raise HTTPException(status_code=409, detail="renewal_proposal_stale")
        if not math.isfinite(old_rent) or abs(old_rent - current_rent) > 0.005:
            raise HTTPException(status_code=409, detail="renewal_proposal_stale")

    return {**contract, "_canonical_end": canonical_end, "_canonical_rent": current_rent, "_property": prop}


def _proposal_doc(contract: Dict[str, Any], ctx: Dict[str, Any], rec: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    lease_id = str(contract.get("_id"))
    end_dt = contract["_canonical_end"]
    current_rent = contract["_canonical_rent"]
    recommendation = _validated_recommendation(rec.get("recommendation", "renew"))
    proposed_rent = _rent(rec.get("proposed_rent", current_rent))
    prop = contract.get("_property") or {}
    return {
        "lease_id": lease_id,
        "property_id": str(contract.get("property_id") or ""),
        "tenant_id": str(contract.get("tenant_id") or ""),
        "tenant_name": contract.get("tenant_name"),
        "tenant_email": contract.get("tenant_email"),
        "tenant_phone": contract.get("tenant_phone"),
        "property_address": contract.get("property_address") or prop.get("address"),
        "current_rent": current_rent,
        "lease_end_date": end_dt.isoformat(),
        "days_until_end": (end_dt - now).days,
        "recommendation": recommendation,
        "proposed_rent": proposed_rent,
        "confidence": str(rec.get("confidence") or "med")[:16],
        "rationale": str(rec.get("rationale") or "")[:4000],
        "highlights": list(rec.get("highlights") or [])[:20],
        "market_signals": ctx,
        "status": "draft",
        "authority_source": "rental_contracts",
        "created_at": now,
        "updated_at": now,
    }


@router.get("/proposals")
async def secure_list_proposals(
    status: Optional[str] = None,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    """Compatibility listing with canonical, idempotent proposal generation."""
    del admin
    if status is not None and status not in _PROPOSAL_STATUSES:
        raise HTTPException(status_code=400, detail="renewal_status_filter_invalid")

    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=WINDOW_DAYS)
    active = await db.rental_contracts.find({"status": "active"}).to_list(500)

    for raw in active:
        try:
            end_dt = _parse_end(raw.get("end_date") or raw.get("lease_end_date"))
        except HTTPException:
            continue
        if not (now <= end_dt <= soon):
            continue

        property_id = str(raw.get("property_id") or "").strip()
        tenant_id = str(raw.get("tenant_id") or "").strip()
        if not ObjectId.is_valid(property_id) or not tenant_id:
            continue
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
        if not prop or prop.get("archived_at"):
            continue

        canonical = {
            **raw,
            "_canonical_end": end_dt,
            "_canonical_rent": _rent(raw.get("rent_amount", raw.get("monthly_rent", 0)), "renewal_contract_rent_invalid"),
            "_property": prop,
        }
        lease_for_analysis = {**raw, "monthly_rent": canonical["_canonical_rent"]}
        ctx = await _lease_market_context(db, lease_for_analysis)
        rec = await _llm_analyze(lease_for_analysis, ctx)
        proposal = _proposal_doc(canonical, ctx, rec, now)
        await db.lease_renewal_proposals.update_one(
            {"lease_id": str(raw.get("_id"))},
            {"$setOnInsert": proposal},
            upsert=True,
        )

    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    docs = await db.lease_renewal_proposals.find(query).sort("days_until_end", 1).to_list(200)
    return {"proposals": [_serialize(d) for d in docs], "total": len(docs)}


@router.post("/refresh/{proposal_id}")
async def secure_refresh_proposal(proposal_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    del admin
    doc = await _proposal(db, proposal_id)
    if doc.get("status") != "draft":
        raise HTTPException(status_code=409, detail="renewal_proposal_not_editable")
    canonical = await _assert_current_contract(db, doc)
    lease_for_analysis = {**canonical, "monthly_rent": canonical["_canonical_rent"]}
    ctx = await _lease_market_context(db, lease_for_analysis)
    rec = await _llm_analyze(lease_for_analysis, ctx)
    now = datetime.now(timezone.utc)
    refreshed = _proposal_doc(canonical, ctx, rec, now)
    mutable = {
        k: refreshed[k]
        for k in (
            "tenant_id", "tenant_name", "tenant_email", "tenant_phone", "property_address",
            "current_rent", "lease_end_date", "days_until_end", "recommendation",
            "proposed_rent", "confidence", "rationale", "highlights", "market_signals",
            "authority_source", "updated_at",
        )
    }
    result = await db.lease_renewal_proposals.update_one(
        {"_id": doc["_id"], "status": "draft", "lease_id": str(doc.get("lease_id") or "")},
        {"$set": mutable},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_proposal_state_changed")
    updated = await db.lease_renewal_proposals.find_one({"_id": doc["_id"]})
    return _serialize(updated)


@router.patch("/{proposal_id}")
async def secure_edit_proposal(
    proposal_id: str,
    body: Dict[str, Any] = Body(...),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    del admin
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="renewal_payload_invalid")
    if "status" in body:
        raise HTTPException(status_code=409, detail="renewal_status_transition_managed")
    unknown = set(body) - {"proposed_rent", "recommendation", "rationale"}
    if unknown:
        raise HTTPException(status_code=400, detail="renewal_field_not_editable")

    doc = await _proposal(db, proposal_id)
    if doc.get("status") != "draft":
        raise HTTPException(status_code=409, detail="renewal_proposal_not_editable")
    await _assert_current_contract(db, doc)

    updates: Dict[str, Any] = {}
    if "proposed_rent" in body:
        updates["proposed_rent"] = _rent(body.get("proposed_rent"))
    if "recommendation" in body:
        updates["recommendation"] = _validated_recommendation(body.get("recommendation"))
    if "rationale" in body:
        rationale = str(body.get("rationale") or "")
        if len(rationale) > 4000:
            raise HTTPException(status_code=400, detail="renewal_rationale_too_long")
        updates["rationale"] = rationale
    if not updates:
        raise HTTPException(status_code=400, detail="renewal_nothing_to_update")
    updates["updated_at"] = datetime.now(timezone.utc)

    result = await db.lease_renewal_proposals.update_one(
        {"_id": doc["_id"], "status": "draft", "lease_id": str(doc.get("lease_id") or "")},
        {"$set": updates},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_proposal_state_changed")
    updated = await db.lease_renewal_proposals.find_one({"_id": doc["_id"]})
    return _serialize(updated)


@router.post("/{proposal_id}/approve")
async def secure_approve_proposal(proposal_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    doc = await _proposal(db, proposal_id)
    if doc.get("status") != "draft":
        raise HTTPException(status_code=409, detail="renewal_proposal_transition_invalid")
    await _assert_current_contract(db, doc)
    _validated_recommendation(doc.get("recommendation"))
    _rent(doc.get("proposed_rent"))
    now = datetime.now(timezone.utc)
    actor = str(admin.get("email") or admin.get("_id") or "admin") if isinstance(admin, dict) else str(admin)
    result = await db.lease_renewal_proposals.update_one(
        {"_id": doc["_id"], "status": "draft", "lease_id": str(doc.get("lease_id") or "")},
        {"$set": {"status": "approved", "approved_by": actor, "approved_at": now, "updated_at": now}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_proposal_state_changed")
    return {"ok": True, "status": "approved"}


@router.post("/{proposal_id}/reject")
async def secure_reject_proposal(
    proposal_id: str,
    body: Dict[str, Any] = Body(default={}),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    del admin
    doc = await _proposal(db, proposal_id)
    if doc.get("status") != "draft":
        raise HTTPException(status_code=409, detail="renewal_proposal_transition_invalid")
    await _assert_current_contract(db, doc)
    reason = str((body or {}).get("reason") or "")
    if len(reason) > 2000:
        raise HTTPException(status_code=400, detail="renewal_reject_reason_too_long")
    now = datetime.now(timezone.utc)
    result = await db.lease_renewal_proposals.update_one(
        {"_id": doc["_id"], "status": "draft", "lease_id": str(doc.get("lease_id") or "")},
        {"$set": {"status": "rejected", "reject_reason": reason, "updated_at": now}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_proposal_state_changed")
    return {"ok": True, "status": "rejected"}
