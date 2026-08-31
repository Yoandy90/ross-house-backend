"""Generate a signable renewal lease from accepted, canonical tenant intent.

Generation creates exactly one ``pending_signatures`` contract. It deliberately
does not sign, activate, change occupancy, alter current rent, or terminate the
currently active lease.
"""
from __future__ import annotations

import calendar
import hashlib
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from .lease_renewal_security_router import _proposal, _rent
from .lease_renewal_tenant_response_router import _digest, _offer, _terms
from .property_mutation_lock import (
    acquire_property_mutation_lock,
    assert_property_lifecycle_recovery_clear,
    release_property_mutation_lock,
)
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewal Contract Generation"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _term_months() -> int:
    try:
        months = int(os.getenv("RENEWAL_TERM_MONTHS", "12"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="renewal_term_configuration_invalid") from exc
    if not 1 <= months <= 36:
        raise HTTPException(status_code=503, detail="renewal_term_configuration_invalid")
    return months


def _add_months(value: date, months: int) -> date:
    absolute = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(absolute, 12)
    month = month0 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _renewal_contract_id(proposal_id: str) -> ObjectId:
    return ObjectId(hashlib.sha256(f"renewal-contract:{proposal_id}".encode("ascii")).hexdigest()[:24])


def _bounded_int(value: Any, default: int, minimum: int, maximum: int, detail: str) -> int:
    if value is None:
        value = default
    if isinstance(value, bool):
        raise HTTPException(status_code=409, detail=detail)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc
    if not minimum <= result <= maximum:
        raise HTTPException(status_code=409, detail=detail)
    return result


async def _assert_existing_occupancy(db, old: Dict[str, Any], prop: Dict[str, Any]) -> None:
    old_id = str(old["_id"])
    tenant_id = str(old.get("tenant_id") or "")
    property_id = str(old.get("property_id") or "")
    unit_id = str(old.get("unit_id") or "").strip()
    if unit_id:
        if not ObjectId.is_valid(unit_id):
            raise HTTPException(status_code=409, detail="renewal_source_unit_invalid")
        unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
        if not unit or str(unit.get("property_id") or "") != property_id:
            raise HTTPException(status_code=409, detail="renewal_source_unit_binding_invalid")
        if str(unit.get("current_contract_id") or "") != old_id:
            raise HTTPException(status_code=409, detail="renewal_source_unit_occupancy_changed")
        if str(unit.get("current_tenant_id") or "") != tenant_id:
            raise HTTPException(status_code=409, detail="renewal_source_unit_tenant_changed")
    else:
        existing_unit = await db.property_units.find_one({"property_id": property_id})
        if prop.get("is_multi_unit") or existing_unit:
            raise HTTPException(status_code=409, detail="renewal_source_unit_required")
        if str(prop.get("current_contract_id") or "") != old_id:
            raise HTTPException(status_code=409, detail="renewal_source_property_occupancy_changed")
        if str(prop.get("current_tenant_id") or "") != tenant_id:
            raise HTTPException(status_code=409, detail="renewal_source_property_tenant_changed")


async def _assert_no_competing_future_contract(db, old: Dict[str, Any], generated_id: ObjectId) -> None:
    blocked = [
        "draft", "pending", "pending_signature", "pending_tenant", "pending_landlord",
        "pending_signatures", "pending_activation", "active",
    ]
    excluded = [old["_id"], generated_id]
    tenant_conflict = await db.rental_contracts.find({
        "tenant_id": str(old.get("tenant_id") or ""),
        "status": {"$in": blocked},
        "_id": {"$nin": excluded},
    }).limit(1).to_list(1)
    if tenant_conflict:
        raise HTTPException(status_code=409, detail="renewal_tenant_contract_conflict")

    unit_id = str(old.get("unit_id") or "").strip()
    resource = {"unit_id": unit_id} if unit_id else {"property_id": str(old.get("property_id") or "")}
    resource_conflict = await db.rental_contracts.find({
        **resource,
        "status": {"$in": blocked},
        "_id": {"$nin": excluded},
    }).limit(1).to_list(1)
    if resource_conflict:
        raise HTTPException(status_code=409, detail="renewal_resource_contract_conflict")


def _existing_view(contract: Dict[str, Any], idempotent: bool):
    return {
        "ok": True,
        "idempotent": idempotent,
        "contract_id": str(contract["_id"]),
        "contract_number": contract.get("contract_number"),
        "status": contract.get("status"),
        "start_date": contract.get("start_date"),
        "end_date": contract.get("end_date"),
    }


async def _generate_under_lock(db, proposal_id: str, actor: str):
    proposal = await _proposal(db, proposal_id)
    tenant_id = str(proposal.get("tenant_id") or "")
    canonical, canonical_terms, terms_digest = await _offer(db, proposal, tenant_id)
    response = await db.lease_renewal_responses.find_one({"_id": proposal["_id"], "proposal_id": proposal_id})
    if not response or response.get("decision") != "accept":
        raise HTTPException(status_code=409, detail="renewal_response_not_accepted")
    exact = {
        "tenant_id": tenant_id,
        "lease_id": str(proposal.get("lease_id") or ""),
        "property_id": str(proposal.get("property_id") or ""),
        "terms_digest": terms_digest,
    }
    if any(str(response.get(key) or "") != value for key, value in exact.items()):
        raise HTTPException(status_code=409, detail="renewal_response_binding_changed")
    if response.get("terms") != canonical_terms or _digest(response.get("terms") or {}) != terms_digest:
        raise HTTPException(status_code=409, detail="renewal_response_terms_invalid")

    old = canonical
    prop = old.get("_property") or {}
    await _assert_existing_occupancy(db, old, prop)
    contract_oid = _renewal_contract_id(proposal_id)
    await _assert_no_competing_future_contract(db, old, contract_oid)
    existing = await db.rental_contracts.find_one({"_id": contract_oid})
    if existing:
        source = existing.get("renewal_source") or {}
        if source.get("proposal_id") != proposal_id or source.get("terms_digest") != terms_digest:
            raise HTTPException(status_code=409, detail="renewal_contract_identity_conflict")
        return _existing_view(existing, True)

    months = _term_months()
    start = old["_canonical_end"].date() + timedelta(days=1)
    end = _add_months(start, months) - timedelta(days=1)
    now = _now()
    rent = _rent(proposal.get("proposed_rent"))
    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=409, detail="renewal_canonical_tenant_missing")
    old_landlord = str(old.get("landlord_id") or "").strip()
    property_owner = str(prop.get("owner_id") or "").strip()
    if old_landlord and property_owner and old_landlord != property_owner:
        raise HTTPException(status_code=409, detail="renewal_landlord_owner_changed")
    landlord_id = property_owner or old_landlord
    due_day = _bounded_int(old.get("payment_due_day"), 1, 1, 31, "renewal_source_due_day_invalid")
    grace_days = _bounded_int(old.get("late_fee_grace_days"), 5, 0, 31, "renewal_source_grace_days_invalid")
    deposit = _rent(old.get("deposit_amount", 0), "renewal_source_deposit_invalid")
    late_fee = _rent(old.get("late_fee_amount", 50), "renewal_source_late_fee_invalid")
    contract_number = f"REN-{start.year}-{proposal_id[-8:].upper()}"
    doc = {
        "_id": contract_oid,
        "contract_number": contract_number,
        "property_id": str(old.get("property_id") or ""),
        "property_address": str(old.get("property_address") or prop.get("address") or ""),
        "property_number": old.get("property_number", prop.get("property_number", "")),
        "unit_id": old.get("unit_id"),
        "unit_name": old.get("unit_name", ""),
        "tenant_id": tenant_id,
        "tenant_name": tenant.get("name", ""),
        "tenant_phone": tenant.get("phone", ""),
        "tenant_email": tenant.get("email", ""),
        "landlord_id": landlord_id,
        "lease_type": old.get("lease_type", "residential"),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "rent_amount": rent,
        "deposit_amount": deposit,
        "payment_due_day": due_day,
        "late_fee_amount": late_fee,
        "late_fee_grace_days": grace_days,
        "terms": old.get("terms", ""),
        "clauses": old.get("clauses", []),
        "special_conditions": old.get("special_conditions", ""),
        "payment_method_type": old.get("payment_method_type", "cash"),
        "addendums": old.get("addendums", {}),
        "status": "pending_signatures",
        "signature": None,
        "signature_status": "pending",
        "tenant_signature": None,
        "tenant_signed_at": None,
        "landlord_signature": None,
        "landlord_signed_at": None,
        "admin_signature": None,
        "admin_signed_at": None,
        "renewal_source": {
            "proposal_id": proposal_id,
            "response_id": str(response["_id"]),
            "prior_contract_id": str(old["_id"]),
            "terms_digest": terms_digest,
            "term_months": months,
        },
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "relationship_source": "accepted_renewal_canonical_records",
        "activation_authority": "lease_lifecycle_only",
    }
    try:
        await db.rental_contracts.insert_one(doc)
    except DuplicateKeyError:
        raced = await db.rental_contracts.find_one({"_id": contract_oid})
        source = (raced or {}).get("renewal_source") or {}
        if raced and source.get("proposal_id") == proposal_id and source.get("terms_digest") == terms_digest:
            return _existing_view(raced, True)
        raise HTTPException(status_code=409, detail="renewal_contract_identity_conflict")
    return _existing_view(doc, False)


@router.post("/{proposal_id}/generate-contract")
async def generate_renewal_contract(
    proposal_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    if body not in (None, {}):
        raise HTTPException(status_code=400, detail="renewal_generation_payload_must_be_empty")
    proposal = await _proposal(db, proposal_id)
    property_id = str(proposal.get("property_id") or "").strip()
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=409, detail="renewal_property_invalid")
    actor = str(admin.get("_id") or admin.get("email") or "admin") if isinstance(admin, dict) else str(admin)
    lock = await acquire_property_mutation_lock(property_id, "renewal_contract_generation", actor)
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        return await _generate_under_lock(db, proposal_id, actor)
    finally:
        await release_property_mutation_lock(property_id, lock)


async def ensure_indexes(db) -> None:
    await db.rental_contracts.create_index("renewal_source.proposal_id", unique=True, sparse=True)
