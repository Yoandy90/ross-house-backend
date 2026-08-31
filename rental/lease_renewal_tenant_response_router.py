"""Authenticated, non-contractual tenant responses to renewal proposals.

A response records tenant intent only. It never creates a lease, changes rent,
signs a document, activates occupancy, or mutates the current lease lifecycle.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from .lease_renewal_security_router import _assert_current_contract, _proposal, _rent, _validated_recommendation
from .shared import get_db, require_role
from .tenant_integrity import resolve_authenticated_tenant

router = APIRouter(prefix="/tenant/lease-renewals", tags=["Tenant Lease Renewal Response"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_id(tenant: Dict[str, Any]) -> str:
    value = str(tenant.get("_id") or tenant.get("id") or "").strip()
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=403, detail="renewal_tenant_identity_invalid")
    return value


async def _canonical_tenant(user: Dict[str, Any]) -> Dict[str, Any]:
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        raise HTTPException(status_code=403, detail="renewal_tenant_identity_unresolved")
    _tenant_id(tenant)
    return tenant


def _terms(proposal: Dict[str, Any], canonical: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "proposal_id": str(proposal.get("_id") or ""),
        "lease_id": str(proposal.get("lease_id") or ""),
        "property_id": str(proposal.get("property_id") or ""),
        "tenant_id": str(proposal.get("tenant_id") or ""),
        "recommendation": _validated_recommendation(proposal.get("recommendation")),
        "current_rent": f"{_rent(proposal.get('current_rent')):.2f}",
        "proposed_rent": f"{_rent(proposal.get('proposed_rent')):.2f}",
        "lease_end_date": canonical["_canonical_end"].date().isoformat(),
    }


def _digest(terms: Dict[str, Any]) -> str:
    payload = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _allowed_decisions(recommendation: str):
    return {"acknowledge"} if recommendation == "non_renew" else {"accept", "decline"}


async def _offer(db, proposal: Dict[str, Any], tenant_id: str):
    if proposal.get("status") != "approved":
        raise HTTPException(status_code=409, detail="renewal_offer_not_approved")
    canonical = await _assert_current_contract(db, proposal)
    if str(canonical.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=403, detail="renewal_offer_not_owned")
    deliveries = await db.lease_renewal_notification_outbox.find({
        "proposal_id": str(proposal["_id"]), "tenant_id": tenant_id, "status": "sent"
    }).limit(2).to_list(2)
    if len(deliveries) != 1:
        raise HTTPException(status_code=409, detail="renewal_offer_not_released")
    terms = _terms(proposal, canonical)
    return canonical, terms, _digest(terms)


def _response_view(doc: Dict[str, Any] | None):
    if not doc:
        return None
    return {
        "response_id": str(doc.get("_id") or ""),
        "decision": doc.get("decision"),
        "terms_digest": doc.get("terms_digest"),
        "created_at": doc.get("created_at"),
    }


@router.get("")
async def list_tenant_renewals(db=Depends(get_db), user=Depends(require_role("tenant"))):
    tenant = await _canonical_tenant(user)
    tenant_id = _tenant_id(tenant)
    proposals = await db.lease_renewal_proposals.find({"tenant_id": tenant_id, "status": "approved"}).limit(20).to_list(20)
    offers = []
    for proposal in proposals:
        try:
            canonical, terms, digest = await _offer(db, proposal, tenant_id)
        except HTTPException:
            continue
        response = await db.lease_renewal_responses.find_one({"proposal_id": str(proposal["_id"])})
        prop = canonical.get("_property") or {}
        offers.append({
            "proposal_id": str(proposal["_id"]),
            "property_address": str(canonical.get("property_address") or prop.get("address") or "")[:500],
            "recommendation": terms["recommendation"],
            "current_rent": terms["current_rent"],
            "proposed_rent": terms["proposed_rent"],
            "lease_end_date": terms["lease_end_date"],
            "allowed_decisions": sorted(_allowed_decisions(terms["recommendation"])),
            "terms_digest": digest,
            "response": _response_view(response),
        })
    return {"offers": offers, "total": len(offers)}


@router.post("/{proposal_id}/respond")
async def respond_to_renewal(
    proposal_id: str,
    body: Dict[str, Any] = Body(...),
    db=Depends(get_db),
    user=Depends(require_role("tenant")),
):
    if not isinstance(body, dict) or set(body) != {"decision", "terms_digest"}:
        raise HTTPException(status_code=400, detail="renewal_response_payload_invalid")
    decision = str(body.get("decision") or "").strip().lower()
    echoed_digest = str(body.get("terms_digest") or "").strip().lower()
    if len(echoed_digest) != 64 or any(ch not in "0123456789abcdef" for ch in echoed_digest):
        raise HTTPException(status_code=400, detail="renewal_response_digest_invalid")

    tenant = await _canonical_tenant(user)
    tenant_id = _tenant_id(tenant)
    proposal = await _proposal(db, proposal_id)
    _canonical, terms, digest = await _offer(db, proposal, tenant_id)
    if echoed_digest != digest:
        raise HTTPException(status_code=409, detail="renewal_response_terms_changed")
    if decision not in _allowed_decisions(terms["recommendation"]):
        raise HTTPException(status_code=400, detail="renewal_response_decision_invalid")

    existing = await db.lease_renewal_responses.find_one({"proposal_id": proposal_id})
    if existing:
        if (
            existing.get("tenant_id") == tenant_id
            and existing.get("terms_digest") == digest
            and existing.get("decision") == decision
        ):
            return {"ok": True, "idempotent": True, "response": _response_view(existing)}
        raise HTTPException(status_code=409, detail="renewal_response_already_recorded")

    now = _now()
    doc = {
        "proposal_id": proposal_id,
        "lease_id": terms["lease_id"],
        "property_id": terms["property_id"],
        "tenant_id": tenant_id,
        "decision": decision,
        "terms_digest": digest,
        "terms": terms,
        "authority": "tenant_authenticated_intent_only",
        "creates_contract": False,
        "activates_occupancy": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await db.lease_renewal_responses.insert_one(doc)
        doc["_id"] = result.inserted_id
    except DuplicateKeyError:
        raced = await db.lease_renewal_responses.find_one({"proposal_id": proposal_id})
        if raced and raced.get("tenant_id") == tenant_id and raced.get("terms_digest") == digest and raced.get("decision") == decision:
            return {"ok": True, "idempotent": True, "response": _response_view(raced)}
        raise HTTPException(status_code=409, detail="renewal_response_already_recorded")
    return {"ok": True, "idempotent": False, "response": _response_view(doc)}


async def ensure_indexes(db) -> None:
    await db.lease_renewal_responses.create_index("proposal_id", unique=True)
    await db.lease_renewal_responses.create_index([("tenant_id", 1), ("created_at", -1)])

