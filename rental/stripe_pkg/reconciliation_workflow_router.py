"""Admin-only, read-only overview of reconciliation decision workflows."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.stripe_pkg.reconciliation_execution_router import (
    _execution_claim_id,
    _execution_result_id,
    _workflow_record_snapshot,
)
from rental.stripe_pkg.reconciliation_resolution_router import ACTIONS_COLLECTION

router = APIRouter()

WORKFLOW_STATES = (
    "proposed",
    "confirmed",
    "execution_started",
    "executed",
    "requires_review",
)


def _workflow_state(confirmation: dict | None, claim: dict | None, result: dict | None) -> str:
    if result:
        return "requires_review" if str(result.get("execution_status") or "") == "requires_review" else "executed"
    if claim:
        return "execution_started"
    if confirmation:
        return "confirmed"
    return "proposed"


def _workflow_summary(proposal: dict, confirmation: dict | None, claim: dict | None, result: dict | None) -> dict:
    state = _workflow_state(confirmation, claim, result)
    return {
        "proposal_id": str(proposal.get("_id") or ""),
        "proposal_digest": str(proposal.get("proposal_digest") or ""),
        "source": str(proposal.get("source") or ""),
        "item_id": str(proposal.get("item_id") or ""),
        "exception_status": str(proposal.get("exception_status") or ""),
        "exception_updated_at": str(proposal.get("exception_updated_at") or ""),
        "outcome": str(proposal.get("outcome") or ""),
        "reason": str(proposal.get("reason") or ""),
        "evidence_reference": str(proposal.get("evidence_reference") or ""),
        "proposer": proposal.get("proposer") or {},
        "confirmation_id": str((confirmation or {}).get("_id") or ""),
        "confirmer": (confirmation or {}).get("confirmer") or None,
        "executor": (claim or result or {}).get("executor") or None,
        "state": state,
        "requires_second_admin": confirmation is None,
        "requires_third_admin": confirmation is not None and claim is None,
        "financial_effect": str((result or claim or proposal).get("financial_effect") or "none"),
        "execution_status": str((result or claim or proposal).get("execution_status") or "not_executed"),
        "result": str((result or {}).get("result") or ""),
        "created_at": proposal.get("created_at"),
        "confirmed_at": (confirmation or {}).get("confirmed_at"),
        "execution_started_at": (claim or {}).get("created_at"),
        "executed_at": (result or {}).get("executed_at"),
    }


async def _collect_proposals(cursor, limit: int) -> list[dict]:
    proposals = []
    async for doc in cursor:
        proposals.append(doc)
        if len(proposals) >= limit:
            break
    return proposals


@router.get("/admin/payment-reconciliation/workflows")
async def admin_reconciliation_workflows(request: Request, limit: int = 100, state: str = ""):
    await auth_admin(request)
    db = get_db()
    safe_limit = max(1, min(int(limit or 100), 200))
    requested_state = str(state or "").strip().lower()
    if requested_state and requested_state not in WORKFLOW_STATES:
        raise HTTPException(status_code=400, detail="Invalid workflow state")

    cursor = db[ACTIONS_COLLECTION].find({"action": "proposal"}).sort("created_at", -1).limit(safe_limit)
    proposals = await _collect_proposals(cursor, safe_limit)
    items = []
    for proposal in proposals:
        proposal_id = str(proposal.get("_id") or "")
        confirmation = await db[ACTIONS_COLLECTION].find_one({"action": "confirmation", "proposal_id": proposal_id})
        claim = result = None
        if confirmation:
            confirmation_oid = confirmation.get("_id")
            claim = await db[ACTIONS_COLLECTION].find_one({
                "_id": _execution_claim_id(confirmation_oid),
                "action": "execution_claim",
            })
            result = await db[ACTIONS_COLLECTION].find_one({
                "_id": _execution_result_id(confirmation_oid),
                "action": "execution_result",
            })
        summary = _workflow_summary(proposal, confirmation, claim, result)
        if requested_state and summary["state"] != requested_state:
            continue
        items.append(summary)
        if len(items) >= safe_limit:
            break

    by_state = {name: 0 for name in WORKFLOW_STATES}
    by_outcome: dict[str, int] = {}
    for item in items:
        by_state[item["state"]] += 1
        outcome = item["outcome"]
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

    return {
        "items": items,
        "count": len(items),
        "by_state": {k: v for k, v in by_state.items() if v},
        "by_outcome": by_outcome,
        "filter": {"state": requested_state or None, "limit": safe_limit},
        "read_only": True,
    }


@router.get("/admin/payment-reconciliation/workflows/{proposal_id}")
async def admin_reconciliation_workflow_detail(proposal_id: str, request: Request):
    """Sanitized workflow detail; delegates no mutations or provider operations."""
    await auth_admin(request)
    db = get_db()
    proposal = await db[ACTIONS_COLLECTION].find_one({"action": "proposal", "_id": _object_id_or_string(proposal_id)})
    if not proposal:
        # exact legacy string fallback only
        proposal = await db[ACTIONS_COLLECTION].find_one({"action": "proposal", "_id": str(proposal_id)})
    if not proposal:
        raise HTTPException(status_code=404, detail="Reconciliation workflow not found")

    confirmation = await db[ACTIONS_COLLECTION].find_one({"action": "confirmation", "proposal_id": str(proposal.get("_id"))})
    claim = result = None
    if confirmation:
        confirmation_oid = confirmation.get("_id")
        claim = await db[ACTIONS_COLLECTION].find_one({"_id": _execution_claim_id(confirmation_oid), "action": "execution_claim"})
        result = await db[ACTIONS_COLLECTION].find_one({"_id": _execution_result_id(confirmation_oid), "action": "execution_result"})

    return {
        "summary": _workflow_summary(proposal, confirmation, claim, result),
        "proposal": _workflow_record_snapshot(proposal),
        "confirmation": _workflow_record_snapshot(confirmation),
        "execution_claim": _workflow_record_snapshot(claim),
        "execution_result": _workflow_record_snapshot(result),
        "read_only": True,
    }


def _object_id_or_string(value: str):
    from bson import ObjectId
    try:
        return ObjectId(str(value))
    except Exception:
        return str(value)
