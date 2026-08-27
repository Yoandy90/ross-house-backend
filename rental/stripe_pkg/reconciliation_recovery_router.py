"""Read-only diagnostics for reconciliation executions that started but may not have finished."""
from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.stripe_pkg.reconciliation_execution_router import (
    _execution_result_id,
    _workflow_record_snapshot,
)
from rental.stripe_pkg.reconciliation_queue_router import _find_by_id, _invoice_snapshot
from rental.stripe_pkg.reconciliation_resolution_router import ACTIONS_COLLECTION

router = APIRouter()

RECOVERY_CLASSES = (
    "result_recorded",
    "financial_write_applied_result_missing",
    "no_financial_write_detected",
    "record_only_result_missing",
    "ambiguous_state",
)


def _safe_before(doc: dict | None) -> dict | None:
    if not isinstance(doc, dict):
        return None
    allowed = {
        "id", "status", "contract_id", "tenant_id", "period", "amount", "late_fee",
        "total_due", "total_paid", "payment_method", "receipt_number", "reference_number", "updated_at",
    }
    return {k: doc.get(k) for k in allowed if k in doc}


def _same_financial_snapshot(before: dict | None, current: dict | None) -> bool:
    if not before or not current:
        return False
    for key in ("id", "status", "amount", "late_fee", "total_due", "total_paid", "updated_at"):
        if before.get(key) != current.get(key):
            return False
    return True


def _classify_recovery(claim: dict, result: dict | None, invoice: dict | None) -> tuple[str, str]:
    if result:
        return "result_recorded", "Execution result already exists; no recovery action is needed."

    financial_pending = str(claim.get("financial_effect") or "") == "local_accounting_pending"
    if not financial_pending:
        return "record_only_result_missing", "No financial write was authorized; investigate the missing result record only."

    claim_id = str(claim.get("_id") or "")
    if invoice and str(invoice.get("reconciliation_execution_claim_id") or "") == claim_id:
        if str(invoice.get("status") or "").lower() in {"paid", "completed"}:
            return (
                "financial_write_applied_result_missing",
                "The invoice records this execution claim as applied. Do not retry; reconstruct the audit result manually.",
            )

    before = _safe_before(claim.get("before"))
    current = _invoice_snapshot(invoice)
    if before and current and _same_financial_snapshot(before, current):
        return (
            "no_financial_write_detected",
            "The current invoice still matches the pre-execution snapshot. Do not retry automatically; review why execution stopped.",
        )

    return (
        "ambiguous_state",
        "The execution result is missing and invoice state cannot be proven. Do not retry or change balances until investigated.",
    )


@router.get("/admin/payment-reconciliation/execution-claims/{claim_id}/recovery")
async def reconciliation_execution_recovery(claim_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    try:
        claim_oid = ObjectId(str(claim_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Execution claim not found")

    claim = await db[ACTIONS_COLLECTION].find_one({"_id": claim_oid, "action": "execution_claim"})
    if not claim:
        raise HTTPException(status_code=404, detail="Execution claim not found")

    try:
        confirmation_oid = ObjectId(str(claim.get("confirmation_id") or ""))
    except Exception:
        raise HTTPException(status_code=409, detail="Execution claim is incomplete")

    result = await db[ACTIONS_COLLECTION].find_one({
        "_id": _execution_result_id(confirmation_oid),
        "action": "execution_result",
    })

    invoice = None
    invoice_id = str(claim.get("invoice_id") or "")
    if invoice_id:
        invoice = await _find_by_id(db.rental_payments, invoice_id)

    classification, guidance = _classify_recovery(claim, result, invoice)
    return {
        "classification": classification,
        "guidance": guidance,
        "recovery_required": classification != "result_recorded",
        "automatic_retry_allowed": False,
        "provider_calls": False,
        "execution_claim": _workflow_record_snapshot(claim),
        "execution_result": _workflow_record_snapshot(result),
        "invoice": _invoice_snapshot(invoice),
        "read_only": True,
    }
