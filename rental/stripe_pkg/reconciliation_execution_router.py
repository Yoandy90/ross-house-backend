"""Guarded local execution for already dual-confirmed reconciliation decisions.

This phase deliberately performs no processor calls, refunds, new charges, or
claim releases. A third distinct admin may execute a confirmed decision. Only
`provider_confirmed_paid` can change a canonical local invoice, and only when
an exact invoice linkage already exists in system data and the confirmed amount
matches the invoice's current outstanding balance exactly.

Execution is fail-closed and append-audited:
1. verify proposal + second-admin confirmation;
2. verify third admin is distinct;
3. re-check exact exception version;
4. append deterministic execution_claim BEFORE any local financial write;
5. apply at most one guarded canonical-invoice transition;
6. append execution_result with sanitized before/after snapshots.

A crash after execution_claim blocks automatic re-execution and requires human
investigation, which is safer than risking a duplicated local credit.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from rental.shared import auth_admin, get_db
from rental.stripe_pkg.reconciliation_queue_router import (
    AUTOPAY_RECONCILIATION_STATUSES,
    HOSTED_RECONCILIATION_STATUSES,
    STRIPE_RECONCILIATION_STATUSES,
    _find_by_id,
    _invoice_snapshot,
)
from rental.stripe_pkg.reconciliation_resolution_router import (
    ACTIONS_COLLECTION,
    _active_exception,
    _admin_identity,
    _deterministic_object_id,
)
from rental.stripe_pkg.rent_reconciliation import CHARGEABLE_STATUSES, stripe_payment_identity_query

router = APIRouter()

EXECUTABLE_OUTCOMES = (
    "provider_confirmed_paid",
    "provider_confirmed_not_paid",
    "needs_refund_review",
    "needs_manual_credit_review",
    "dismiss_non_financial",
)


def _same_admin(left: dict, right: dict) -> bool:
    left_id = str((left or {}).get("id") or "")
    right_id = str((right or {}).get("id") or "")
    left_email = str((left or {}).get("email") or "").strip().lower()
    right_email = str((right or {}).get("email") or "").strip().lower()
    return bool((left_id and left_id == right_id) or (left_email and left_email == right_email))


def _money_cents(value: Any) -> int:
    return int(round(float(value or 0) * 100))


def _invoice_balance_snapshot(invoice: dict | None) -> dict | None:
    if not invoice:
        return None
    total_due = float(invoice.get("total_due") or (
        float(invoice.get("amount") or 0) + float(invoice.get("late_fee") or 0)
    ))
    total_paid = float(invoice.get("total_paid") or 0)
    outstanding = max(total_due - total_paid, 0)
    return {
        "invoice": _invoice_snapshot(invoice),
        "total_due_cents": _money_cents(total_due),
        "total_paid_cents": _money_cents(total_paid),
        "outstanding_cents": _money_cents(outstanding),
    }


def _execution_receipt(confirmation_id: str, invoice_id: str) -> str:
    digest = hashlib.sha256(f"manual-recon:{confirmation_id}:{invoice_id}".encode("utf-8")).hexdigest()[:12].upper()
    return f"MAN-RECON-{digest}"


async def _load_confirmed_decision(db, confirmation_id: str) -> tuple[dict, dict, ObjectId]:
    try:
        confirmation_oid = ObjectId(str(confirmation_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Confirmed reconciliation decision not found")

    confirmation = await db[ACTIONS_COLLECTION].find_one({
        "_id": confirmation_oid,
        "action": "confirmation",
    })
    if not confirmation:
        raise HTTPException(status_code=404, detail="Confirmed reconciliation decision not found")

    try:
        proposal_oid = ObjectId(str(confirmation.get("proposal_id") or ""))
    except Exception:
        raise HTTPException(status_code=409, detail="Confirmed reconciliation decision is incomplete")

    proposal = await db[ACTIONS_COLLECTION].find_one({
        "_id": proposal_oid,
        "action": "proposal",
    })
    if not proposal:
        raise HTTPException(status_code=409, detail="Confirmed reconciliation decision is incomplete")

    expected_digest = str(proposal.get("proposal_digest") or "")
    confirmed_digest = str(confirmation.get("proposal_digest") or "")
    if not expected_digest or not hmac.compare_digest(expected_digest, confirmed_digest):
        raise HTTPException(status_code=409, detail="Confirmed reconciliation digest mismatch")
    if str(proposal.get("outcome") or "") != str(confirmation.get("outcome") or ""):
        raise HTTPException(status_code=409, detail="Confirmed reconciliation outcome mismatch")

    return proposal, confirmation, confirmation_oid


async def _recheck_exception_version(db, proposal: dict) -> dict:
    exception = await _active_exception(
        db,
        str(proposal.get("source") or ""),
        str(proposal.get("item_id") or ""),
    )
    if (
        exception is None
        or str(exception.get("status") or "") != str(proposal.get("exception_status") or "")
        or str(exception.get("updated_at") or "") != str(proposal.get("exception_updated_at") or "")
    ):
        raise HTTPException(status_code=409, detail="Reconciliation item changed after approval")
    return exception


async def _proven_invoice_for_execution(db, proposal: dict) -> dict | None:
    """Resolve only an invoice linkage already proven by local system data.

    No request-supplied invoice id is used for discovery. The caller may later
    echo/confirm the resolved id, but cannot choose an arbitrary invoice.
    """
    source = str(proposal.get("source") or "")
    item_id = str(proposal.get("item_id") or "")

    if source == "hosted_checkout":
        source_doc = await _find_by_id(db.rental_payments, item_id)
        if not source_doc or str(source_doc.get("status") or "") not in HOSTED_RECONCILIATION_STATUSES:
            return None
        invoice_id = str(source_doc.get("invoice_id") or "")
        if not invoice_id:
            return None
        return await _find_by_id(db.rental_payments, invoice_id)

    if source == "autopay":
        source_doc = await _find_by_id(db.autopay_config, item_id)
        if not source_doc or str(source_doc.get("last_attempt_status") or "") not in AUTOPAY_RECONCILIATION_STATUSES:
            return None
        invoice_id = str(source_doc.get("last_attempt_invoice_id") or "")
        if not invoice_id:
            return None
        return await _find_by_id(db.rental_payments, invoice_id)

    if source == "stripe_webhook":
        source_doc = await _find_by_id(db.stripe_webhook_events, item_id)
        if not source_doc or str(source_doc.get("reconciliation_status") or "") not in STRIPE_RECONCILIATION_STATUSES:
            return None
        pi_id = str(source_doc.get("account_id") or "")
        if not pi_id:
            return None
        return await db.rental_payments.find_one(stripe_payment_identity_query(pi_id))

    return None


def _execution_claim_id(confirmation_id: ObjectId) -> ObjectId:
    return _deterministic_object_id("recon-execution-claim", confirmation_id)


def _execution_result_id(confirmation_id: ObjectId) -> ObjectId:
    return _deterministic_object_id("recon-execution-result", confirmation_id)


def _workflow_record_snapshot(doc: dict | None) -> dict | None:
    if not doc:
        return None
    action = str(doc.get("action") or "")
    base = {
        "id": str(doc.get("_id") or ""),
        "action": action,
        "proposal_id": str(doc.get("proposal_id") or ""),
        "confirmation_id": str(doc.get("confirmation_id") or ""),
        "proposal_digest": str(doc.get("proposal_digest") or ""),
        "source": str(doc.get("source") or ""),
        "item_id": str(doc.get("item_id") or ""),
        "outcome": str(doc.get("outcome") or ""),
        "financial_effect": str(doc.get("financial_effect") or ""),
        "execution_status": str(doc.get("execution_status") or ""),
        "created_at": doc.get("created_at") or doc.get("confirmed_at") or doc.get("executed_at"),
    }
    if action == "proposal":
        base["proposer"] = doc.get("proposer") or {}
        base["reason"] = str(doc.get("reason") or "")
        base["evidence_reference"] = str(doc.get("evidence_reference") or "")
        base["exception_status"] = str(doc.get("exception_status") or "")
        base["exception_updated_at"] = str(doc.get("exception_updated_at") or "")
    elif action == "confirmation":
        base["proposer"] = doc.get("proposer") or {}
        base["confirmer"] = doc.get("confirmer") or {}
    elif action in {"execution_claim", "execution_result"}:
        base["executor"] = doc.get("executor") or {}
        base["invoice_id"] = str(doc.get("invoice_id") or "")
        base["confirmed_amount_cents"] = int(doc.get("confirmed_amount_cents") or 0)
        if action == "execution_result":
            base["result"] = str(doc.get("result") or "")
            base["before"] = doc.get("before")
            base["after"] = doc.get("after")
    return base


async def _readiness(db, proposal: dict, confirmation: dict) -> dict:
    outcome = str(proposal.get("outcome") or "")
    readiness = {
        "outcome": outcome,
        "requires_third_admin": True,
        "provider_calls": False,
        "can_execute": True,
        "financial_write": outcome == "provider_confirmed_paid",
        "invoice": None,
        "outstanding_cents": 0,
        "reason": "ready",
    }
    if outcome not in EXECUTABLE_OUTCOMES:
        readiness.update({"can_execute": False, "reason": "unsupported_outcome"})
        return readiness

    await _recheck_exception_version(db, proposal)

    if outcome == "provider_confirmed_paid":
        invoice = await _proven_invoice_for_execution(db, proposal)
        balance = _invoice_balance_snapshot(invoice)
        if not invoice or not balance:
            readiness.update({"can_execute": False, "reason": "exact_invoice_link_unavailable"})
            return readiness
        if str(invoice.get("status") or "").lower() not in set(CHARGEABLE_STATUSES):
            readiness.update({"can_execute": False, "reason": "invoice_not_chargeable", "invoice": balance["invoice"]})
            return readiness
        if balance["outstanding_cents"] <= 0:
            readiness.update({"can_execute": False, "reason": "invoice_has_no_outstanding", "invoice": balance["invoice"]})
            return readiness
        readiness.update({
            "invoice": balance["invoice"],
            "outstanding_cents": balance["outstanding_cents"],
        })
    return readiness


@router.get("/admin/payment-reconciliation/resolution-confirmations/{confirmation_id}/execution-readiness")
async def reconciliation_execution_readiness(confirmation_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    proposal, confirmation, _confirmation_oid = await _load_confirmed_decision(db, confirmation_id)
    readiness = await _readiness(db, proposal, confirmation)
    return {
        **readiness,
        "proposal_id": str(confirmation.get("proposal_id") or ""),
        "confirmation_id": confirmation_id,
        "proposal_digest": str(proposal.get("proposal_digest") or ""),
        "read_only": True,
    }


@router.get("/admin/payment-reconciliation/resolution-proposals/{proposal_id}/workflow")
async def reconciliation_workflow_history(proposal_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    try:
        proposal_oid = ObjectId(str(proposal_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Reconciliation proposal not found")

    proposal = await db[ACTIONS_COLLECTION].find_one({"_id": proposal_oid, "action": "proposal"})
    if not proposal:
        raise HTTPException(status_code=404, detail="Reconciliation proposal not found")
    confirmation = await db[ACTIONS_COLLECTION].find_one({"action": "confirmation", "proposal_id": str(proposal_oid)})
    execution_claim = None
    execution_result = None
    if confirmation:
        confirmation_id = confirmation.get("_id")
        execution_claim = await db[ACTIONS_COLLECTION].find_one({
            "_id": _execution_claim_id(confirmation_id), "action": "execution_claim"
        })
        execution_result = await db[ACTIONS_COLLECTION].find_one({
            "_id": _execution_result_id(confirmation_id), "action": "execution_result"
        })

    return {
        "proposal": _workflow_record_snapshot(proposal),
        "confirmation": _workflow_record_snapshot(confirmation),
        "execution_claim": _workflow_record_snapshot(execution_claim),
        "execution_result": _workflow_record_snapshot(execution_result),
        "read_only": True,
    }


@router.post("/admin/payment-reconciliation/resolution-confirmations/{confirmation_id}/execute")
async def execute_confirmed_reconciliation(confirmation_id: str, request: Request):
    admin = await auth_admin(request)
    db = get_db()
    proposal, confirmation, confirmation_oid = await _load_confirmed_decision(db, confirmation_id)

    executor = _admin_identity(admin)
    if not executor["id"] and not executor["email"]:
        raise HTTPException(status_code=403, detail="Admin identity unavailable")
    proposer = proposal.get("proposer") or {}
    confirmer = confirmation.get("confirmer") or {}
    if _same_admin(executor, proposer) or _same_admin(executor, confirmer):
        raise HTTPException(status_code=403, detail="A third distinct admin must execute this decision")

    data = await request.json()
    if data.get("execute") is not True:
        raise HTTPException(status_code=400, detail="Explicit execution confirmation is required")
    supplied_digest = str(data.get("proposal_digest") or "").strip()
    expected_digest = str(proposal.get("proposal_digest") or "")
    if not supplied_digest or not hmac.compare_digest(supplied_digest, expected_digest):
        raise HTTPException(status_code=409, detail="Proposal digest mismatch")
    if str(data.get("expected_outcome") or "") != str(proposal.get("outcome") or ""):
        raise HTTPException(status_code=409, detail="Proposal outcome mismatch")

    readiness = await _readiness(db, proposal, confirmation)
    if not readiness.get("can_execute"):
        raise HTTPException(status_code=409, detail="Confirmed decision is not executable in the current state")

    outcome = str(proposal.get("outcome") or "")
    invoice = None
    before = None
    invoice_id = ""
    confirmed_amount_cents = 0

    if outcome == "provider_confirmed_paid":
        invoice = await _proven_invoice_for_execution(db, proposal)
        balance = _invoice_balance_snapshot(invoice)
        if not invoice or not balance:
            raise HTTPException(status_code=409, detail="Exact invoice linkage unavailable")
        invoice_id = str(invoice.get("_id") or "")
        requested_invoice_id = str(data.get("expected_invoice_id") or "").strip()
        if requested_invoice_id != invoice_id:
            raise HTTPException(status_code=409, detail="Invoice confirmation mismatch")
        confirmed_amount_cents = int(data.get("confirmed_amount_cents") or 0)
        if confirmed_amount_cents <= 0 or confirmed_amount_cents != balance["outstanding_cents"]:
            raise HTTPException(status_code=409, detail="Confirmed amount does not match current outstanding balance")
        before = balance["invoice"]

    claim_id = _execution_claim_id(confirmation_oid)
    now = datetime.now(timezone.utc)
    try:
        await db[ACTIONS_COLLECTION].insert_one({
            "_id": claim_id,
            "action": "execution_claim",
            "proposal_id": str(proposal.get("_id") or ""),
            "confirmation_id": str(confirmation_oid),
            "proposal_digest": expected_digest,
            "source": proposal.get("source"),
            "item_id": proposal.get("item_id"),
            "outcome": outcome,
            "executor": executor,
            "invoice_id": invoice_id,
            "confirmed_amount_cents": confirmed_amount_cents,
            "financial_effect": "local_accounting_pending" if outcome == "provider_confirmed_paid" else "none",
            "execution_status": "started",
            "created_at": now,
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This confirmed decision already has an execution attempt")

    result_name = "decision_recorded_no_financial_write"
    after = before
    financial_effect = "none"
    execution_status = "completed"

    if outcome == "provider_confirmed_paid":
        total_due = float(invoice.get("total_due") or (
            float(invoice.get("amount") or 0) + float(invoice.get("late_fee") or 0)
        ))
        receipt = _execution_receipt(str(confirmation_oid), invoice_id)
        update = await db.rental_payments.update_one(
            {
                "_id": invoice["_id"],
                "status": {"$in": list(CHARGEABLE_STATUSES)},
                "total_paid": invoice.get("total_paid", 0),
            },
            {"$set": {
                "status": "completed",
                "paid": True,
                "payment_method": "manual_reconciliation_verified",
                "payment_date": now,
                "total_paid": round(total_due, 2),
                "receipt_number": receipt,
                "reconciliation_source": proposal.get("source"),
                "reconciliation_reference_id": proposal.get("reference_id"),
                "reconciliation_confirmation_id": str(confirmation_oid),
                "reconciliation_evidence_reference": proposal.get("evidence_reference"),
                "updated_at": now,
            }},
        )
        if getattr(update, "modified_count", 0) != 1:
            result_name = "not_applied_concurrent_change"
            execution_status = "requires_review"
            financial_effect = "none"
            after_doc = await _find_by_id(db.rental_payments, invoice_id)
            after = _invoice_snapshot(after_doc)
        else:
            result_name = "local_invoice_completed"
            execution_status = "completed"
            financial_effect = "local_accounting"
            after_doc = await _find_by_id(db.rental_payments, invoice_id)
            after = _invoice_snapshot(after_doc)

    result_id = _execution_result_id(confirmation_oid)
    try:
        await db[ACTIONS_COLLECTION].insert_one({
            "_id": result_id,
            "action": "execution_result",
            "proposal_id": str(proposal.get("_id") or ""),
            "confirmation_id": str(confirmation_oid),
            "proposal_digest": expected_digest,
            "source": proposal.get("source"),
            "item_id": proposal.get("item_id"),
            "outcome": outcome,
            "executor": executor,
            "invoice_id": invoice_id,
            "confirmed_amount_cents": confirmed_amount_cents,
            "financial_effect": financial_effect,
            "execution_status": execution_status,
            "result": result_name,
            "before": before,
            "after": after,
            "executed_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        # The deterministic execution_claim already prevents duplicate financial
        # application. A duplicate result insert is an audit inconsistency and
        # must fail closed rather than retrying anything.
        raise HTTPException(status_code=409, detail="Execution result already recorded")

    if execution_status != "completed":
        raise HTTPException(status_code=409, detail="Local accounting state changed; review the execution trail")

    return {
        "executed": True,
        "proposal_id": str(proposal.get("_id") or ""),
        "confirmation_id": str(confirmation_oid),
        "execution_claim_id": str(claim_id),
        "execution_result_id": str(result_id),
        "outcome": outcome,
        "financial_effect": financial_effect,
        "execution_status": execution_status,
        "result": result_name,
        "provider_calls": False,
        "invoice": after,
    }
