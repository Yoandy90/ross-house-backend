"""Strict admin lease lifecycle state-machine guard.

This precedence route constrains the historical/admin status mutation surface
before the occupancy lifecycle implementation runs. It also owns the shared
per-property mutation claim for the full guarded lifecycle call, serializing
activation/release with lease creation, property edits, and unit topology.
"""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.lease_lifecycle_security_router import secure_update_contract_status
from rental.property_mutation_lock import (
    acquire_property_mutation_lock,
    assert_property_lifecycle_recovery_clear,
    release_property_mutation_lock,
)
from rental.shared import auth_admin, get_db

router = APIRouter()

_TRANSITIONS = {
    "draft": {"pending_tenant", "pending_signatures"},
    "pending": {"pending_tenant", "pending_signatures"},
    "pending_signature": {"pending_tenant", "pending_landlord", "pending_signatures"},
    "pending_tenant": {"pending_landlord", "pending_signatures", "pending_activation"},
    "pending_landlord": {"pending_tenant", "pending_signatures", "pending_activation"},
    "pending_signatures": {"pending_tenant", "pending_landlord", "pending_activation"},
    "pending_activation": {"active"},
    "active": {"terminated", "expired"},
    "terminated": set(),
    "expired": set(),
}


@router.patch('/admin/rental-contracts/{contract_id}/status')
async def guarded_update_contract_status(contract_id: str, request: Request):
    admin = await auth_admin(request)
    if not ObjectId.is_valid(contract_id):
        raise HTTPException(status_code=400, detail="lease_contract_invalid")

    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="lease_status_payload_invalid")
    new_status = str(data.get("status") or "").strip().lower()

    db = get_db()
    contract_oid = ObjectId(contract_id)
    initial = await db.rental_contracts.find_one({"_id": contract_oid})
    if not initial:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")
    property_id = str(initial.get("property_id") or "").strip()

    token = await acquire_property_mutation_lock(
        property_id,
        "lease_lifecycle",
        str(admin.get("email") or admin.get("_id") or "admin"),
    )
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        contract = await db.rental_contracts.find_one({"_id": contract_oid})
        if not contract:
            raise HTTPException(status_code=409, detail="lease_contract_changed")
        if str(contract.get("property_id") or "").strip() != property_id:
            raise HTTPException(status_code=409, detail="lease_property_changed")
        old_status = str(contract.get("status") or "").strip().lower()

        if new_status == old_status:
            return await secure_update_contract_status(contract_id, request)
        if old_status not in _TRANSITIONS or new_status not in _TRANSITIONS[old_status]:
            raise HTTPException(status_code=409, detail="lease_status_transition_invalid")

        if new_status == "active":
            if old_status != "pending_activation":
                raise HTTPException(status_code=409, detail="lease_activation_state_invalid")
            if not contract.get("tenant_signature") or not (
                contract.get("landlord_signature") or contract.get("admin_signature")
            ):
                raise HTTPException(status_code=400, detail="lease_signatures_required")
            if data.get("force_activate"):
                raise HTTPException(status_code=400, detail="lease_force_activation_forbidden")

        return await secure_update_contract_status(contract_id, request)
    finally:
        await release_property_mutation_lock(property_id, token)
