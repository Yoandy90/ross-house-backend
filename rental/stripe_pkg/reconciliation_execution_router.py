"""Third-admin, local-only execution for dual-confirmed reconciliation decisions.

No provider calls, refunds, new charges, retries, or claim releases live here.
Only ``provider_confirmed_paid`` may change a canonical local rent invoice, and
only when BOTH conditions are proven from trusted local state:
- an exact invoice linkage already exists; and
- the original server-recorded charge amount equals the invoice's current
  outstanding balance.

The third admin must echo the exact invoice id and amount, but cannot choose
those values: they are resolved from the canonical invoice and original source
record first.

Execution is deliberately fail-closed:
- proposal + confirmation are revalidated;
- executor must be distinct from proposer and confirmer;
- source exception version must still match;
- deterministic execution_claim is appended BEFORE any accounting write;
- canonical invoice update uses the full approved financial snapshot as guard;
- deterministic execution_result is appended after the attempt.

If the process dies after the execution_claim, automatic re-execution is
blocked. Human investigation is safer than risking a second local credit.
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


def _invoice_financial_snapshot(invoice: dict | None) -> dict | None:
    if not invoice:
        return None
    amount = float(invoice.get("amount") or 0)
    late_fee = float(invoice.get("late_fee") or 0)
    total_due = float(invoice.get("total_due") or (amount + late_fee))
    total_paid = float(invoice.get("total_paid") or 0)
    outstanding = max(total_due - total_paid, 0)
    return {
        "invoice": _invoice_snapshot(invoice),
        "amount": amount,
        "late_fee": late_fee,
        "total_due": total_due,
        "total_paid": total_paid,
        "total_due_cents": _money_cents(total_due),
        "total_paid_cents": _money_cents(total_paid),
        "outstanding_cents": _money_cents(outstanding),
    }


def _invoice_update_guard(invoice: dict) -> dict:
    """Guard every financial/version field used to approve settlement."""
    guard = {
        "_id": invoice["_id"],
        "status": {"$in": list(CHARGEABLE_STATUSES)},
        "amount": invoice.get("amount", 0),
        "late_fee": invoice.get("late_fee", 0),
        "total_paid": invoice.get("total_paid", 0),
    }
    if "total_due" in invoice:
        guard["total_due"] = invoice.get("total_due")
    if invoice.get("updated_at") is not None:
        guard["updated_at"] = invoice.get("updated_at")
    return guard


def _execution_receipt(confirmation_id: str, invoice_id: str) -> str:
    digest = hashlib.sha256(f"manual-recon:{confirmation_id}:{invoice_id}".encode()).hexdigest()[:12].upper()
    return f"MAN-RECON-{digest}"


def _execution_claim_id(confirmation_id: ObjectId) -> ObjectId:
    return _deterministic_object_id("recon-execution-claim", confirmation_id)


def _execution_result_id(confirmation_id: ObjectId) -> ObjectId:
    return _deterministic_object_id("recon-execution-result", confirmation_id)


async def _load_confirmed_decision(db, confirmation_id: str) -> tuple[dict, dict, ObjectId]:
    try:
        confirmation_oid = ObjectId(str(confirmation_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Confirmed reconciliation decision not found")

    confirmation = await db[ACTIONS_COLLECTION].find_one({"_id": confirmation_oid, "action": "confirmation"})
    if not confirmation:
        raise HTTPException(status_code=404, detail="Confirmed reconciliation decision not found")

    try:
        proposal_oid = ObjectId(str(confirmation.get("proposal_id") or ""))
    except Exception:
        raise HTTPException(status_code=409, detail="Confirmed reconciliation decision is incomplete")
    proposal = await db[ACTIONS_COLLECTION].find_one({"_id": proposal_oid, "action": "proposal"})
    if not proposal:
        raise HTTPException(status_code=409, detail="Confirmed reconciliation decision is incomplete")

    proposal_digest = str(proposal.get("proposal_digest") or "")
    confirmation_digest = str(confirmation.get("proposal_digest") or "")
    if not proposal_digest or not hmac.compare_digest(proposal_digest, confirmation_digest):
        raise HTTPException(status_code=409, detail="Confirmed reconciliation digest mismatch")
    if str(proposal.get("outcome") or "") != str(confirmation.get("outcome") or ""):
        raise HTTPException(status_code=409, detail="Confirmed reconciliation outcome mismatch")
    for field in ("source", "item_id", "exception_status", "exception_updated_at"):
        if str(proposal.get(field) or "") != str(confirmation.get(field) or ""):
            raise HTTPException(status_code=409, detail="Confirmed reconciliation version mismatch")
    return proposal, confirmation, confirmation_oid


async def _recheck_exception_version(db, proposal: dict) -> dict:
    exception = await _active_exception(db, str(proposal.get("source") or ""), str(proposal.get("item_id") or ""))
    if (
        exception is None
        or str(exception.get("status") or "") != str(proposal.get("exception_status") or "")
        or str(exception.get("updated_at") or "") != str(proposal.get("exception_updated_at") or "")
    ):
        raise HTTPException(status_code=409, detail="Reconciliation item changed after approval")
    return exception


async def _proven_invoice_for_execution(db, proposal: dict) -> dict | None:
    """Resolve only invoice links already present in trusted local state."""
    source = str(proposal.get("source") or "")
    item_id = str(proposal.get("item_id") or "")

    if source == "hosted_checkout":
        source_doc = await _find_by_id(db.rental_payments, item_id)
        if not source_doc or str(source_doc.get("status") or "") not in HOSTED_RECONCILIATION_STATUSES:
            return None
        invoice_id = str(source_doc.get("invoice_id") or "")
        return await _find_by_id(db.rental_payments, invoice_id) if invoice_id else None

    if source == "autopay":
        source_doc = await _find_by_id(db.autopay_config, item_id)
        if not source_doc or str(source_doc.get("last_attempt_status") or "") not in AUTOPAY_RECONCILIATION_STATUSES:
            return None
        invoice_id = str(source_doc.get("last_attempt_invoice_id") or "")
        if invoice_id:
            return await _find_by_id(db.rental_payments, invoice_id)
        # Safe compatibility for Stripe autopay attempts that already persisted a
        # PI identity; never infer by tenant/month/amount.
        pi_id = str(source_doc.get("last_attempt_intent_id") or "")
        return await db.rental_payments.find_one(stripe_payment_identity_query(pi_id)) if pi_id else None

    if source == "stripe_webhook":
        source_doc = await _find_by_id(db.stripe_webhook_events, item_id)
        if not source_doc or str(source_doc.get("reconciliation_status") or "") not in STRIPE_RECONCILIATION_STATUSES:
            return None
        pi_id = str(source_doc.get("account_id") or "")
        return await db.rental_payments.find_one(stripe_payment_identity_query(pi_id)) if pi_id else None

    return None


async def _trusted_source_amount_cents(db, proposal: dict) -> int | None:
    """Return the original amount recorded by trusted server-side source state."""
    source = str(proposal.get("source") or "")
    item_id = str(proposal.get("item_id") or "")

    if source == "hosted_checkout":
        source_doc = await _find_by_id(db.rental_payments, item_id)
        if not source_doc or str(source_doc.get("status") or "") not in HOSTED_RECONCILIATION_STATUSES:
            return None
        amount = float(source_doc.get("total_paid") or 0)
        return _money_cents(amount) if amount > 0 else None

    if source == "autopay":
        source_doc = await _find_by_id(db.autopay_config, item_id)
        if not source_doc or str(source_doc.get("last_attempt_status") or "") not in AUTOPAY_RECONCILIATION_STATUSES:
            return None
        amount = float(source_doc.get("last_attempt_amount") or 0)
        return _money_cents(amount) if amount > 0 else None

    # Hardened Stripe reconciliation logs intentionally do not persist raw event
    # amounts/metadata. Without a trusted amount source, manual paid execution is
    # not allowed; investigation remains available read-only.
    return None


def _workflow_record_snapshot(doc: dict | None) -> dict | None:
    if not doc:
        return None
    action = str(doc.get("action") or "")
    item = {
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
        item.update({
            "proposer": doc.get("proposer") or {},
            "reason": str(doc.get("reason") or ""),
            "evidence_reference": str(doc.get("evidence_reference") or ""),
            "exception_status": str(doc.get("exception_status") or ""),
            "exception_updated_at": str(doc.get("exception_updated_at") or ""),
        })
    elif action == "confirmation":
        item.update({"proposer": doc.get("proposer") or {}, "confirmer": doc.get("confirmer") or {}})
    elif action in {"execution_claim", "execution_result"}:
        item.update({
            "executor": doc.get("executor") or {},
            "invoice_id": str(doc.get("invoice_id") or ""),
            "trusted_source_amount_cents": int(doc.get("trusted_source_amount_cents") or 0),
            "confirmed_amount_cents": int(doc.get("confirmed_amount_cents") or 0),
        })
        if action == "execution_result":
            item.update({"result": str(doc.get("result") or ""), "before": doc.get("before"), "after": doc.get("after")})
    return item


async def _readiness(db, proposal: dict, confirmation: dict, confirmation_oid: ObjectId) -> dict:
    outcome = str(proposal.get("outcome") or "")
    result = {
        "outcome": outcome,
        "requires_third_admin": True,
        "provider_calls": False,
        "can_execute": True,
        "financial_write": outcome == "provider_confirmed_paid",
        "invoice": None,
        "outstanding_cents": 0,
        "trusted_source_amount_cents": 0,
        "reason": "ready",
    }
    if outcome not in EXECUTABLE_OUTCOMES:
        result.update({"can_execute": False, "reason": "unsupported_outcome"})
        return result
    if await db[ACTIONS_COLLECTION].find_one({"_id": _execution_claim_id(confirmation_oid), "action": "execution_claim"}):
        result.update({"can_execute": False, "reason": "execution_already_started"})
        return result

    await _recheck_exception_version(db, proposal)
    if outcome != "provider_confirmed_paid":
        return result

    invoice = await _proven_invoice_for_execution(db, proposal)
    snapshot = _invoice_financial_snapshot(invoice)
    if not invoice or not snapshot:
        result.update({"can_execute": False, "reason": "exact_invoice_link_unavailable"})
        return result
    if str(invoice.get("status") or "").lower() not in set(CHARGEABLE_STATUSES):
        result.update({"can_execute": False, "reason": "invoice_not_chargeable", "invoice": snapshot["invoice"]})
        return result
    if snapshot["outstanding_cents"] <= 0:
        result.update({"can_execute": False, "reason": "invoice_has_no_outstanding", "invoice": snapshot["invoice"]})
        return result

    trusted_amount = await _trusted_source_amount_cents(db, proposal)
    if trusted_amount is None:
        result.update({
            "can_execute": False,
            "reason": "trusted_source_amount_unavailable",
            "invoice": snapshot["invoice"],
            "outstanding_cents": snapshot["outstanding_cents"],
        })
        return result
    if trusted_amount != snapshot["outstanding_cents"]:
        result.update({
            "can_execute": False,
            "reason": "trusted_source_amount_mismatch",
            "invoice": snapshot["invoice"],
            "outstanding_cents": snapshot["outstanding_cents"],
            "trusted_source_amount_cents": trusted_amount,
        })
        return result

    result.update({
        "invoice": snapshot["invoice"],
        "outstanding_cents": snapshot["outstanding_cents"],
        "trusted_source_amount_cents": trusted_amount,
    })
    return result


@router.get("/admin/payment-reconciliation/resolution-confirmations/{confirmation_id}/execution-readiness")
async def reconciliation_execution_readiness(confirmation_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    proposal, confirmation, confirmation_oid = await _load_confirmed_decision(db, confirmation_id)
    readiness = await _readiness(db, proposal, confirmation, confirmation_oid)
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
    claim = result = None
    if confirmation:
        confirmation_oid = confirmation.get("_id")
        claim = await db[ACTIONS_COLLECTION].find_one({"_id": _execution_claim_id(confirmation_oid), "action": "execution_claim"})
        result = await db[ACTIONS_COLLECTION].find_one({"_id": _execution_result_id(confirmation_oid), "action": "execution_result"})
    return {
        "proposal": _workflow_record_snapshot(proposal),
        "confirmation": _workflow_record_snapshot(confirmation),
        "execution_claim": _workflow_record_snapshot(claim),
        "execution_result": _workflow_record_snapshot(result),
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
    if _same_admin(executor, proposal.get("proposer") or {}) or _same_admin(executor, confirmation.get("confirmer") or {}):
        raise HTTPException(status_code=403, detail="A third distinct admin must execute this decision")

    data = await request.json()
    if data.get("execute") is not True:
        raise HTTPException(status_code=400, detail="Explicit execution confirmation is required")
    expected_digest = str(proposal.get("proposal_digest") or "")
    supplied_digest = str(data.get("proposal_digest") or "").strip()
    if not supplied_digest or not hmac.compare_digest(supplied_digest, expected_digest):
        raise HTTPException(status_code=409, detail="Proposal digest mismatch")
    outcome = str(proposal.get("outcome") or "")
    if str(data.get("expected_outcome") or "") != outcome:
        raise HTTPException(status_code=409, detail="Proposal outcome mismatch")

    readiness = await _readiness(db, proposal, confirmation, confirmation_oid)
    if not readiness.get("can_execute"):
        raise HTTPException(status_code=409, detail="Confirmed decision is not executable in the current state")

    invoice = None
    snapshot = None
    invoice_id = ""
    trusted_source_amount_cents = 0
    confirmed_amount_cents = 0
    before = None
    if outcome == "provider_confirmed_paid":
        invoice = await _proven_invoice_for_execution(db, proposal)
        snapshot = _invoice_financial_snapshot(invoice)
        trusted_source_amount = await _trusted_source_amount_cents(db, proposal)
        if not invoice or not snapshot or trusted_source_amount is None:
            raise HTTPException(status_code=409, detail="Exact invoice or trusted source amount unavailable")
        invoice_id = str(invoice.get("_id") or "")
        if str(data.get("expected_invoice_id") or "").strip() != invoice_id:
            raise HTTPException(status_code=409, detail="Invoice confirmation mismatch")
        trusted_source_amount_cents = int(trusted_source_amount)
        confirmed_amount_cents = int(data.get("confirmed_amount_cents") or 0)
        if (
            confirmed_amount_cents <= 0
            or confirmed_amount_cents != snapshot["outstanding_cents"]
            or confirmed_amount_cents != trusted_source_amount_cents
        ):
            raise HTTPException(status_code=409, detail="Confirmed amount does not match trusted source and current balance")
        before = snapshot["invoice"]

    now = datetime.now(timezone.utc)
    claim_id = _execution_claim_id(confirmation_oid)
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
            "trusted_source_amount_cents": trusted_source_amount_cents,
            "confirmed_amount_cents": confirmed_amount_cents,
            "before": before,
            "financial_effect": "local_accounting_pending" if outcome == "provider_confirmed_paid" else "none",
            "execution_status": "started",
            "created_at": now,
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This confirmed decision already has an execution attempt")

    execution_status = "completed"
    financial_effect = "none"
    result_name = "decision_recorded_no_financial_write"
    after = before

    if outcome == "provider_confirmed_paid":
        receipt = _execution_receipt(str(confirmation_oid), invoice_id)
        update = await db.rental_payments.update_one(
            _invoice_update_guard(invoice),
            {"$set": {
                "status": "completed",
                "paid": True,
                "payment_method": "manual_reconciliation_verified",
                "payment_date": now,
                "total_paid": round(snapshot["total_due"], 2),
                "receipt_number": receipt,
                "reconciliation_source": proposal.get("source"),
                "reconciliation_reference_id": proposal.get("reference_id"),
                "reconciliation_confirmation_id": str(confirmation_oid),
                "reconciliation_execution_claim_id": str(claim_id),
                "reconciliation_evidence_reference": proposal.get("evidence_reference"),
                "updated_at": now,
            }},
        )
        after_doc = await _find_by_id(db.rental_payments, invoice_id)
        after = _invoice_snapshot(after_doc)
        if getattr(update, "modified_count", 0) != 1:
            execution_status = "requires_review"
            financial_effect = "none"
            result_name = "not_applied_concurrent_change"
        else:
            financial_effect = "local_accounting"
            result_name = "local_invoice_completed"
    elif outcome == "provider_confirmed_not_paid":
        result_name = "provider_not_paid_decision_recorded"
    elif outcome == "needs_refund_review":
        result_name = "refund_review_required"
    elif outcome == "needs_manual_credit_review":
        result_name = "manual_credit_review_required"
    elif outcome == "dismiss_non_financial":
        result_name = "non_financial_exception_dismissal_recorded"

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
            "trusted_source_amount_cents": trusted_source_amount_cents,
            "confirmed_amount_cents": confirmed_amount_cents,
            "financial_effect": financial_effect,
            "execution_status": execution_status,
            "result": result_name,
            "before": before,
            "after": after,
            "executed_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
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
