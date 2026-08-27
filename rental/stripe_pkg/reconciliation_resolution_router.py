"""Dual-control, append-only decisions for payment reconciliation exceptions.

This module records human decisions only. It deliberately cannot retry, refund,
credit, release claims, call providers, or mutate financial source records.
A second *different* admin must confirm the exact immutable proposal digest.
"""
from __future__ import annotations

import hashlib
import hmac
import json
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
    _autopay_item,
    _find_by_id,
    _hosted_item,
    _stripe_item,
)

router = APIRouter()
ACTIONS_COLLECTION = "payment_reconciliation_actions"

ALLOWED_OUTCOMES = (
    "provider_confirmed_paid",
    "provider_confirmed_not_paid",
    "needs_refund_review",
    "needs_manual_credit_review",
    "dismiss_non_financial",
)


def _admin_identity(admin: dict) -> dict:
    return {
        "id": str(admin.get("_id") or ""),
        "email": str(admin.get("email") or "").strip().lower(),
    }


def _clean_text(value: Any, *, max_len: int) -> str:
    return " ".join(str(value or "").strip().split())[:max_len]


def _proposal_digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _deterministic_object_id(prefix: str, *parts: Any) -> ObjectId:
    raw = ":".join([prefix, *(str(part or "") for part in parts)])
    return ObjectId(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24])


async def _active_exception(db, source: str, item_id: str) -> dict | None:
    if source == "hosted_checkout":
        doc = await _find_by_id(db.rental_payments, item_id)
        if doc and str(doc.get("status") or "") in HOSTED_RECONCILIATION_STATUSES:
            return _hosted_item(doc)
        return None

    if source == "stripe_webhook":
        doc = await _find_by_id(db.stripe_webhook_events, item_id)
        if doc and str(doc.get("reconciliation_status") or "") in STRIPE_RECONCILIATION_STATUSES:
            return _stripe_item(doc)
        return None

    if source == "autopay":
        doc = await _find_by_id(db.autopay_config, item_id)
        if doc and str(doc.get("last_attempt_status") or "") in AUTOPAY_RECONCILIATION_STATUSES:
            return _autopay_item(doc)
        return None

    return None


def _immutable_proposal_payload(
    *,
    source: str,
    item_id: str,
    exception: dict,
    outcome: str,
    reason: str,
    evidence_reference: str,
    proposer: dict,
) -> dict:
    return {
        "source": source,
        "item_id": item_id,
        "exception_status": str(exception.get("status") or ""),
        "exception_updated_at": str(exception.get("updated_at") or ""),
        "reference_id": str(exception.get("reference_id") or ""),
        "outcome": outcome,
        "reason": reason,
        "evidence_reference": evidence_reference,
        "proposer": proposer,
        "financial_effect": "none",
        "execution_status": "not_executed",
    }


@router.post("/admin/payment-reconciliation/{source}/{item_id}/resolution-proposals")
async def propose_reconciliation_resolution(source: str, item_id: str, request: Request):
    admin = await auth_admin(request)
    db = get_db()
    exception = await _active_exception(db, source, item_id)
    if exception is None:
        raise HTTPException(status_code=404, detail="Reconciliation item not found")

    data = await request.json()
    outcome = str(data.get("outcome") or "").strip()
    if outcome not in ALLOWED_OUTCOMES:
        raise HTTPException(status_code=400, detail="Invalid reconciliation outcome")

    reason = _clean_text(data.get("reason"), max_len=1000)
    if len(reason) < 12:
        raise HTTPException(status_code=400, detail="A detailed reason is required")
    evidence_reference = _clean_text(data.get("evidence_reference"), max_len=240)
    if outcome != "dismiss_non_financial" and len(evidence_reference) < 3:
        raise HTTPException(status_code=400, detail="Evidence reference is required for financial decisions")

    proposer = _admin_identity(admin)
    if not proposer["id"] and not proposer["email"]:
        raise HTTPException(status_code=403, detail="Admin identity unavailable")

    now = datetime.now(timezone.utc)
    immutable = _immutable_proposal_payload(
        source=source,
        item_id=item_id,
        exception=exception,
        outcome=outcome,
        reason=reason,
        evidence_reference=evidence_reference,
        proposer=proposer,
    )
    digest = _proposal_digest(immutable)
    # The same exact proposal is idempotent, but a revised proposal may coexist.
    # Only one proposal can ultimately be confirmed for an exception version.
    proposal_id = _deterministic_object_id(
        "recon-proposal",
        source,
        item_id,
        immutable["exception_status"],
        immutable["exception_updated_at"],
        digest,
    )
    record = {
        "_id": proposal_id,
        "action": "proposal",
        **immutable,
        "proposal_digest": digest,
        "created_at": now,
    }
    try:
        await db[ACTIONS_COLLECTION].insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This exact proposal already exists")

    return {
        "proposal_id": str(proposal_id),
        "proposal_digest": digest,
        "outcome": outcome,
        "requires_second_admin": True,
        "financial_effect": "none",
        "execution_status": "not_executed",
    }


@router.post("/admin/payment-reconciliation/resolution-proposals/{proposal_id}/confirm")
async def confirm_reconciliation_resolution(proposal_id: str, request: Request):
    admin = await auth_admin(request)
    db = get_db()
    try:
        proposal_oid = ObjectId(proposal_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Reconciliation proposal not found")

    proposal = await db[ACTIONS_COLLECTION].find_one({
        "_id": proposal_oid,
        "action": "proposal",
    })
    if proposal is None:
        raise HTTPException(status_code=404, detail="Reconciliation proposal not found")

    confirmer = _admin_identity(admin)
    if not confirmer["id"] and not confirmer["email"]:
        raise HTTPException(status_code=403, detail="Admin identity unavailable")
    proposer = proposal.get("proposer") or {}
    same_id = bool(confirmer["id"] and confirmer["id"] == str(proposer.get("id") or ""))
    same_email = bool(confirmer["email"] and confirmer["email"] == str(proposer.get("email") or "").lower())
    if same_id or same_email:
        raise HTTPException(status_code=403, detail="A different admin must confirm this proposal")

    data = await request.json()
    if data.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required")
    supplied_digest = str(data.get("proposal_digest") or "").strip()
    expected_digest = str(proposal.get("proposal_digest") or "")
    if not supplied_digest or not hmac.compare_digest(supplied_digest, expected_digest):
        raise HTTPException(status_code=409, detail="Proposal digest mismatch")
    if str(data.get("expected_outcome") or "") != str(proposal.get("outcome") or ""):
        raise HTTPException(status_code=409, detail="Proposal outcome mismatch")

    exception = await _active_exception(db, str(proposal.get("source") or ""), str(proposal.get("item_id") or ""))
    if (
        exception is None
        or str(exception.get("status") or "") != str(proposal.get("exception_status") or "")
        or str(exception.get("updated_at") or "") != str(proposal.get("exception_updated_at") or "")
    ):
        raise HTTPException(status_code=409, detail="Reconciliation item changed; create a new proposal")

    # One atomic confirmation lock per exception version, regardless of which
    # competing proposal won the second-admin approval.
    confirmation_id = _deterministic_object_id(
        "recon-confirmation",
        proposal.get("source"),
        proposal.get("item_id"),
        proposal.get("exception_status"),
        proposal.get("exception_updated_at"),
    )
    try:
        await db[ACTIONS_COLLECTION].insert_one({
            "_id": confirmation_id,
            "action": "confirmation",
            "proposal_id": str(proposal_oid),
            "proposal_digest": supplied_digest,
            "source": proposal.get("source"),
            "item_id": proposal.get("item_id"),
            "exception_status": proposal.get("exception_status"),
            "exception_updated_at": proposal.get("exception_updated_at"),
            "outcome": proposal.get("outcome"),
            "proposer": proposer,
            "confirmer": confirmer,
            "financial_effect": "none",
            "execution_status": "not_executed",
            "confirmed_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This exception version already has a confirmed decision")

    return {
        "proposal_id": str(proposal_oid),
        "confirmation_id": str(confirmation_id),
        "confirmed": True,
        "dual_control": True,
        "financial_effect": "none",
        "execution_status": "not_executed",
    }
