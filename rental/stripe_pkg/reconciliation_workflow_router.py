"""Admin-only, read-only overview of reconciliation decision workflows."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
EXECUTION_STALE_SECONDS = 300
MAX_WORKFLOW_SCAN = 1000

OUTCOME_CAPABILITIES = {
    "provider_confirmed_paid": {"mode": "local_invoice_completion", "financial_write": True, "requires_exact_invoice": True, "provider_call": False},
    "provider_confirmed_not_paid": {"mode": "record_only", "financial_write": False, "requires_exact_invoice": False, "provider_call": False},
    "needs_refund_review": {"mode": "review_only", "financial_write": False, "requires_exact_invoice": False, "provider_call": False},
    "needs_manual_credit_review": {"mode": "review_only", "financial_write": False, "requires_exact_invoice": False, "provider_call": False},
    "dismiss_non_financial": {"mode": "record_only", "financial_write": False, "requires_exact_invoice": False, "provider_call": False},
}


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _claim_age_seconds(claim: dict | None, now: datetime | None = None) -> int | None:
    if not claim:
        return None
    stamp = _as_utc(claim.get("created_at"))
    if not stamp:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - stamp).total_seconds()))


def _workflow_state(confirmation: dict | None, claim: dict | None, result: dict | None, *, now: datetime | None = None) -> str:
    if result:
        return "executed" if str(result.get("execution_status") or "") == "completed" else "requires_review"
    if claim:
        age = _claim_age_seconds(claim, now)
        return "requires_review" if age is not None and age >= EXECUTION_STALE_SECONDS else "execution_started"
    return "confirmed" if confirmation else "proposed"


def _execution_capability(outcome: str) -> dict:
    return dict(OUTCOME_CAPABILITIES.get(str(outcome or ""), {
        "mode": "unsupported",
        "financial_write": False,
        "requires_exact_invoice": False,
        "provider_call": False,
    }))


def _workflow_summary(proposal: dict, confirmation: dict | None, claim: dict | None, result: dict | None, *, now: datetime | None = None) -> dict:
    state = _workflow_state(confirmation, claim, result, now=now)
    outcome = str(proposal.get("outcome") or "")
    return {
        "proposal_id": str(proposal.get("_id") or ""),
        "proposal_digest": str(proposal.get("proposal_digest") or ""),
        "source": str(proposal.get("source") or ""),
        "item_id": str(proposal.get("item_id") or ""),
        "exception_status": str(proposal.get("exception_status") or ""),
        "exception_updated_at": str(proposal.get("exception_updated_at") or ""),
        "outcome": outcome,
        "capability": _execution_capability(outcome),
        "reason": str(proposal.get("reason") or ""),
        "evidence_reference": str(proposal.get("evidence_reference") or ""),
        "proposer": proposal.get("proposer") or {},
        "confirmation_id": str((confirmation or {}).get("_id") or ""),
        "confirmer": (confirmation or {}).get("confirmer") or None,
        "executor": (claim or result or {}).get("executor") or None,
        "state": state,
        "recovery_required": state == "requires_review" and result is None and claim is not None,
        "execution_claim_id": str((claim or {}).get("_id") or ""),
        "execution_claim_age_seconds": _claim_age_seconds(claim, now),
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


async def _collect(cursor, limit: int) -> list[dict]:
    items = []
    async for doc in cursor:
        items.append(doc)
        if len(items) >= limit:
            break
    return items


async def _load_parts(db, proposal: dict):
    proposal_id = str(proposal.get("_id") or "")
    confirmation = await db[ACTIONS_COLLECTION].find_one({"action": "confirmation", "proposal_id": proposal_id})
    claim = result = None
    if confirmation:
        confirmation_oid = confirmation.get("_id")
        claim = await db[ACTIONS_COLLECTION].find_one({"_id": _execution_claim_id(confirmation_oid), "action": "execution_claim"})
        result = await db[ACTIONS_COLLECTION].find_one({"_id": _execution_result_id(confirmation_oid), "action": "execution_result"})
    return confirmation, claim, result


@router.get("/admin/payment-reconciliation/workflows")
async def admin_reconciliation_workflows(request: Request, limit: int = 100, state: str = ""):
    await auth_admin(request)
    db = get_db()
    safe_limit = max(1, min(int(limit or 100), 200))
    requested_state = str(state or "").strip().lower()
    if requested_state and requested_state not in WORKFLOW_STATES:
        raise HTTPException(status_code=400, detail="Invalid workflow state")

    scan_limit = min(MAX_WORKFLOW_SCAN, max(safe_limit, safe_limit * 5)) if requested_state else safe_limit
    cursor = db[ACTIONS_COLLECTION].find({"action": "proposal"}).sort("created_at", -1).limit(scan_limit)
    proposals = await _collect(cursor, scan_limit)
    now = datetime.now(timezone.utc)
    items = []
    for proposal in proposals:
        confirmation, claim, result = await _load_parts(db, proposal)
        summary = _workflow_summary(proposal, confirmation, claim, result, now=now)
        if requested_state and summary["state"] != requested_state:
            continue
        items.append(summary)
        if len(items) >= safe_limit:
            break

    by_state: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    recovery_required = 0
    for item in items:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1
        by_outcome[item["outcome"]] = by_outcome.get(item["outcome"], 0) + 1
        recovery_required += int(bool(item.get("recovery_required")))

    return {
        "items": items,
        "count": len(items),
        "by_state": by_state,
        "by_outcome": by_outcome,
        "recovery_required": recovery_required,
        "filter": {"state": requested_state or None, "limit": safe_limit},
        "scan": {"limit": scan_limit, "max": MAX_WORKFLOW_SCAN, "bounded": True},
        "read_only": True,
    }


@router.get("/admin/payment-reconciliation/workflows/{proposal_id}")
async def admin_reconciliation_workflow_detail(proposal_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    proposal = await db[ACTIONS_COLLECTION].find_one({"action": "proposal", "_id": _object_id_or_string(proposal_id)})
    if not proposal:
        proposal = await db[ACTIONS_COLLECTION].find_one({"action": "proposal", "_id": str(proposal_id)})
    if not proposal:
        raise HTTPException(status_code=404, detail="Reconciliation workflow not found")
    confirmation, claim, result = await _load_parts(db, proposal)
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
