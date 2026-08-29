"""Strict admin lease lifecycle state-machine guard.

This precedence route constrains the historical/admin status mutation surface
before the occupancy lifecycle implementation runs.  Signatures advance their
own evidence states; admin status mutation cannot skip directly into occupancy,
reopen terminal leases, or use the legacy ``force_activate`` escape hatch.
"""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.lease_lifecycle_security_router import secure_update_contract_status

router = APIRouter()

_TRANSITIONS = {
    "draft": {"pending", "pending_signature", "pending_tenant", "pending_signatures"},
    "pending": {"draft", "pending_signature", "pending_tenant", "pending_signatures"},
    "pending_signature": {"draft", "pending_tenant", "pending_landlord", "pending_signatures"},
    "pending_tenant": {"draft", "pending_landlord", "pending_signatures", "pending_activation"},
    "pending_landlord": {"draft", "pending_tenant", "pending_signatures", "pending_activation"},
    "pending_signatures": {"draft", "pending_tenant", "pending_landlord", "pending_activation"},
    "pending_activation": {"draft", "active"},
    "active": {"terminated", "expired"},
    "terminated": set(),
    "expired": set(),
}


@router.patch('/admin/rental-contracts/{contract_id}/status')
async def guarded_update_contract_status(contract_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(contract_id):
        raise HTTPException(status_code=400, detail="lease_contract_invalid")

    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="lease_status_payload_invalid")
    new_status = str(data.get("status") or "").strip().lower()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")
    old_status = str(contract.get("status") or "").strip().lower()

    if new_status == old_status:
        return await secure_update_contract_status(contract_id, request)
    if old_status not in _TRANSITIONS or new_status not in _TRANSITIONS[old_status]:
        raise HTTPException(status_code=409, detail="lease_status_transition_invalid")

    if new_status == "active":
        # The approved lifecycle is create -> signatures -> pending_activation ->
        # guarded activation -> occupancy.  Admin input never bypasses evidence.
        if old_status != "pending_activation":
            raise HTTPException(status_code=409, detail="lease_activation_state_invalid")
        if not contract.get("tenant_signature") or not (
            contract.get("landlord_signature") or contract.get("admin_signature")
        ):
            raise HTTPException(status_code=400, detail="lease_signatures_required")
        if data.get("force_activate"):
            raise HTTPException(status_code=400, detail="lease_force_activation_forbidden")

    return await secure_update_contract_status(contract_id, request)
