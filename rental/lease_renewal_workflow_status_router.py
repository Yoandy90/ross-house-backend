"""Canonical, read-only administrative view of one lease-renewal workflow.

The client must not infer authority by joining proposal, delivery, response,
contract, and rollover documents. This boundary verifies their exact lineage
server-side and returns only bounded operational state without tenant contact,
provider evidence, lifecycle claim values, or recovery actor identities.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from .lease_renewal_contract_generation_router import _renewal_contract_id
from .lease_renewal_security_router import _proposal, _validated_recommendation
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewal Workflow Status"])

_PROPOSAL_STATES = {"draft", "approved", "rejected", "sent"}
_DELIVERY_STATES = {
    "pending", "claimed", "sent", "retryable_failure",
    "ambiguous_provider_result", "failed",
}
_DECISIONS = {"accept", "decline", "acknowledge"}
_CONTRACT_STATES = {
    "draft", "pending", "pending_signature", "pending_tenant", "pending_landlord",
    "pending_signatures", "pending_activation", "active", "expired", "terminated",
}
_ROLLOVER_STATES = {"claimed", "transferring", "committed", "recovery_required", "completed"}
_ROLLOVER_STAGES = {
    "record_created", "claim_prior", "claim_renewal", "transfer_projections",
    "expire_prior", "activate_renewal", "commit_record", "clear_claims",
    "clear_prior_claim", "clear_renewal_claim", "complete", "complete_record",
    "normalize_projections", "manual_recovery_confirmed",
    "manual_recovery_clear_claims", "manual_recovery_finalize_record",
}


def _id(value: Any, detail: str) -> str:
    value = str(value or "").strip()
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=409, detail=detail)
    return value


def _bounded_state(value: Any, allowed: set[str], detail: str) -> str:
    state = str(value or "").strip().lower()
    if state not in allowed:
        raise HTTPException(status_code=409, detail=detail)
    return state


async def _one_by_proposal(collection, proposal_id: str, detail: str) -> Optional[Dict[str, Any]]:
    rows = await collection.find({"proposal_id": proposal_id}).limit(2).to_list(2)
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail=detail)
    return rows[0] if rows else None


def _exact(doc: Dict[str, Any], expected: Dict[str, str], detail: str) -> None:
    if any(str(doc.get(key) or "") != value for key, value in expected.items()):
        raise HTTPException(status_code=409, detail=detail)


async def _verified_workflow(db, proposal_id: str) -> Dict[str, Any]:
    proposal = await _proposal(db, proposal_id)
    proposal_state = _bounded_state(
        proposal.get("status"), _PROPOSAL_STATES, "renewal_workflow_proposal_state_invalid"
    )
    lease_id = _id(proposal.get("lease_id"), "renewal_workflow_prior_contract_invalid")
    property_id = _id(proposal.get("property_id"), "renewal_workflow_property_invalid")
    tenant_id = _id(proposal.get("tenant_id"), "renewal_workflow_tenant_invalid")
    recommendation = _validated_recommendation(proposal.get("recommendation"))

    old = await db.rental_contracts.find_one({"_id": ObjectId(lease_id)})
    if not old:
        raise HTTPException(status_code=409, detail="renewal_workflow_prior_contract_missing")
    _exact(old, {"property_id": property_id, "tenant_id": tenant_id},
           "renewal_workflow_prior_contract_binding_changed")
    if str(old.get("unit_id") or "").strip() != str(proposal.get("unit_id") or old.get("unit_id") or "").strip():
        raise HTTPException(status_code=409, detail="renewal_workflow_prior_unit_binding_changed")
    if not await db.properties.find_one({"_id": ObjectId(property_id)}):
        raise HTTPException(status_code=409, detail="renewal_workflow_property_missing")
    if not await db.tenants.find_one({"_id": ObjectId(tenant_id)}):
        raise HTTPException(status_code=409, detail="renewal_workflow_tenant_missing")

    delivery = await _one_by_proposal(
        db.lease_renewal_notification_outbox, proposal_id,
        "renewal_workflow_multiple_delivery_records",
    )
    delivery_state = None
    if delivery:
        if proposal_state not in {"approved", "sent"}:
            raise HTTPException(status_code=409, detail="renewal_workflow_delivery_before_approval")
        _exact(delivery, {"lease_id": lease_id, "property_id": property_id, "tenant_id": tenant_id},
               "renewal_workflow_delivery_binding_changed")
        delivery_state = _bounded_state(
            delivery.get("status"), _DELIVERY_STATES, "renewal_workflow_delivery_state_invalid"
        )
        attempts = delivery.get("attempts", 0)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0 or attempts > 20:
            raise HTTPException(status_code=409, detail="renewal_workflow_delivery_attempts_invalid")

    response = await _one_by_proposal(
        db.lease_renewal_responses, proposal_id,
        "renewal_workflow_multiple_response_records",
    )
    decision = None
    if response:
        if response.get("_id") != proposal.get("_id"):
            raise HTTPException(status_code=409, detail="renewal_workflow_response_identity_changed")
        _exact(response, {"lease_id": lease_id, "property_id": property_id, "tenant_id": tenant_id},
               "renewal_workflow_response_binding_changed")
        decision = _bounded_state(
            response.get("decision"), _DECISIONS, "renewal_workflow_response_decision_invalid"
        )
        if recommendation == "non_renew" and decision != "acknowledge":
            raise HTTPException(status_code=409, detail="renewal_workflow_response_decision_mismatch")
        if recommendation != "non_renew" and decision not in {"accept", "decline"}:
            raise HTTPException(status_code=409, detail="renewal_workflow_response_decision_mismatch")
        if delivery_state != "sent":
            raise HTTPException(status_code=409, detail="renewal_workflow_response_without_delivery")

    renewal_id = _renewal_contract_id(proposal_id)
    new = await db.rental_contracts.find_one({"_id": renewal_id})
    contract_state = None
    if new:
        source = new.get("renewal_source") or {}
        _exact(new, {"property_id": property_id, "tenant_id": tenant_id},
               "renewal_workflow_contract_binding_changed")
        _exact(source, {"proposal_id": proposal_id, "prior_contract_id": lease_id},
               "renewal_workflow_contract_source_changed")
        if str(new.get("unit_id") or "").strip() != str(old.get("unit_id") or "").strip():
            raise HTTPException(status_code=409, detail="renewal_workflow_contract_unit_changed")
        if decision != "accept":
            raise HTTPException(status_code=409, detail="renewal_workflow_contract_without_acceptance")
        terms_digest = str((response or {}).get("terms_digest") or "")
        if len(terms_digest) != 64 or any(ch not in "0123456789abcdef" for ch in terms_digest.lower()):
            raise HTTPException(status_code=409, detail="renewal_workflow_response_digest_invalid")
        if str(source.get("terms_digest") or "").lower() != terms_digest.lower():
            raise HTTPException(status_code=409, detail="renewal_workflow_contract_terms_changed")
        contract_state = _bounded_state(
            new.get("status"), _CONTRACT_STATES, "renewal_workflow_contract_state_invalid"
        )

    rollover = await db.lease_renewal_rollovers.find_one({"_id": renewal_id}) if new else None
    rollover_state = None
    rollover_stage = None
    if rollover:
        _exact(
            rollover,
            {"proposal_id": proposal_id, "prior_contract_id": lease_id,
             "renewal_contract_id": str(renewal_id), "property_id": property_id,
             "tenant_id": tenant_id},
            "renewal_workflow_rollover_binding_changed",
        )
        rollover_state = _bounded_state(
            rollover.get("state"), _ROLLOVER_STATES, "renewal_workflow_rollover_state_invalid"
        )
        rollover_stage = _bounded_state(
            rollover.get("stage"), _ROLLOVER_STAGES, "renewal_workflow_rollover_stage_invalid"
        )
        if rollover_state != "completed" and contract_state not in {"pending_activation", "active"}:
            raise HTTPException(status_code=409, detail="renewal_workflow_rollover_contract_state_invalid")
    if contract_state == "active" and rollover_state not in {"committed", "recovery_required", "completed"}:
        raise HTTPException(status_code=409, detail="renewal_workflow_active_without_rollover")
    if contract_state in {"expired", "terminated"} and rollover_state != "completed":
        raise HTTPException(status_code=409, detail="renewal_workflow_terminal_contract_without_completed_rollover")

    return {
        "proposal": proposal,
        "proposal_state": proposal_state,
        "recommendation": recommendation,
        "delivery": delivery,
        "delivery_state": delivery_state,
        "response": response,
        "decision": decision,
        "contract": new,
        "contract_state": contract_state,
        "rollover": rollover,
        "rollover_state": rollover_state,
        "rollover_stage": rollover_stage,
    }


def _next_action(flow: Dict[str, Any]) -> str:
    rollover_state = flow["rollover_state"]
    if rollover_state == "completed":
        return "completed"
    if rollover_state in {"recovery_required", "committed"}:
        return "inspect_or_recover_rollover"
    if rollover_state in {"claimed", "transferring"}:
        return "await_rollover_completion"
    contract_state = flow["contract_state"]
    if contract_state == "active":
        return "inspect_rollover_integrity"
    if contract_state == "pending_activation":
        return "await_effective_date_or_rollover"
    if contract_state in {"draft", "pending", "pending_signature", "pending_tenant", "pending_landlord", "pending_signatures"}:
        return "collect_contract_signatures"
    if flow["decision"] == "accept":
        return "generate_contract"
    if flow["decision"] in {"decline", "acknowledge"}:
        return "closed"
    delivery_state = flow["delivery_state"]
    if delivery_state == "sent":
        return "await_tenant_response"
    if delivery_state == "ambiguous_provider_result":
        return "inspect_delivery"
    if delivery_state == "claimed":
        return "await_delivery_worker"
    if delivery_state == "retryable_failure":
        return "retry_delivery"
    if delivery_state == "failed":
        return "review_delivery_failure"
    if delivery_state == "pending":
        return "send_notification"
    if flow["proposal_state"] in {"approved", "sent"}:
        return "repair_notification_intent"
    if flow["proposal_state"] == "rejected":
        return "closed"
    return "review_or_approve_proposal"


def _view(proposal_id: str, flow: Dict[str, Any]) -> Dict[str, Any]:
    delivery = flow["delivery"]
    response = flow["response"]
    contract = flow["contract"]
    rollover = flow["rollover"]
    next_action = _next_action(flow)
    return {
        "ok": True,
        "read_only": True,
        "integrity": "verified",
        "proposal_id": proposal_id,
        "proposal": {
            "status": flow["proposal_state"],
            "recommendation": flow["recommendation"],
        },
        "delivery": None if not delivery else {
            "status": flow["delivery_state"],
            "attempts": delivery.get("attempts", 0),
            "manual_review_required": flow["delivery_state"] in {"ambiguous_provider_result", "failed"},
        },
        "tenant_response": None if not response else {
            "decision": flow["decision"],
            "recorded": True,
        },
        "contract": None if not contract else {
            "contract_id": str(contract["_id"]),
            "status": flow["contract_state"],
            "start_date": contract.get("start_date"),
            "end_date": contract.get("end_date"),
            "tenant_signed": bool(contract.get("tenant_signature")),
            "landlord_or_admin_signed": bool(contract.get("landlord_signature") or contract.get("admin_signature")),
        },
        "rollover": None if not rollover else {
            "state": flow["rollover_state"],
            "stage": flow["rollover_stage"],
            "manual_recovery_required": flow["rollover_state"] in {"recovery_required", "committed"},
            "automatic_retry_allowed": False,
        },
        "next_action": next_action,
    }


@router.get("/{proposal_id}/workflow-status")
async def get_workflow_status(
    proposal_id: str,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    del admin
    return _view(proposal_id, await _verified_workflow(db, proposal_id))
