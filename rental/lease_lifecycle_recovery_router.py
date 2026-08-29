"""Read-only inspection for interrupted lease lifecycle transitions.

This module never mutates contracts or occupancy projections.  Recovery is
bound to the exact contract_id + lifecycle claim_id and reports only observed
state so an administrator cannot accidentally retry or release occupancy by
inference.
"""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db

router = APIRouter()
_RELEASE_TARGETS = {"terminated", "expired", "draft", "pending_signature", "pending"}


def _oid(value: str, detail: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(str(value))


def _owner_state(current_contract_id, contract_id: str) -> str:
    current = str(current_contract_id or "")
    if current == contract_id:
        return "exact_contract"
    if not current:
        return "unclaimed"
    return "other_contract"


def _tenant_state(tenant: dict, contract: dict) -> str:
    contract_id = str(contract.get("_id") or "")
    expected_property = str(contract.get("property_id") or "")
    expected_unit = str(contract.get("unit_id") or "")
    current_contract = str(tenant.get("current_contract_id") or "")
    current_property = str(tenant.get("current_property_id") or "")
    current_unit = str(tenant.get("current_unit_id") or "")

    if current_contract and current_contract != contract_id:
        return "different_projection"
    if current_property == expected_property and current_unit == expected_unit:
        # Missing current_contract_id is accepted only as a legacy projection
        # shape. New lifecycle activations always set the exact contract claim.
        return "exact_contract_projection"
    if not current_contract and not current_property and not current_unit:
        return "cleared"
    return "different_projection"


async def _observe(contract: dict) -> dict:
    db = get_db()
    contract_id = str(contract["_id"])
    property_id = str(contract.get("property_id") or "")
    tenant_id = str(contract.get("tenant_id") or "")
    unit_id = str(contract.get("unit_id") or "")

    if not ObjectId.is_valid(property_id) or not ObjectId.is_valid(tenant_id):
        return {"valid": False, "reason": "invalid_contract_relationship_ids"}

    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
    if not prop or not tenant:
        return {"valid": False, "reason": "missing_contract_relationship"}

    observed = {
        "valid": True,
        "property": {
            "exists": True,
            "ownership": _owner_state(prop.get("current_contract_id"), contract_id),
            "status": str(prop.get("status") or ""),
        },
        "tenant": {
            "exists": True,
            "ownership": _owner_state(tenant.get("current_contract_id"), contract_id),
            "projection": _tenant_state(tenant, contract),
        },
        "unit": None,
    }
    if unit_id:
        if not ObjectId.is_valid(unit_id):
            return {"valid": False, "reason": "invalid_contract_unit_id"}
        unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
        if not unit:
            return {"valid": False, "reason": "missing_contract_unit"}
        if str(unit.get("property_id") or "") != property_id:
            return {"valid": False, "reason": "unit_property_mismatch"}
        observed["unit"] = {
            "exists": True,
            "ownership": _owner_state(unit.get("current_contract_id"), contract_id),
            "tenant_matches": str(unit.get("current_tenant_id") or "") in ("", tenant_id),
            "status": str(unit.get("status") or ""),
        }
    return observed


def _classify(contract: dict, target: str, observed: dict) -> str:
    if not observed.get("valid"):
        return "ambiguous_state"

    prop_owner = observed["property"]["ownership"]
    tenant_owner = observed["tenant"]["ownership"]
    tenant_state = observed["tenant"]["projection"]
    unit = observed.get("unit")
    unit_owner = unit["ownership"] if unit else None

    if prop_owner == "other_contract" or tenant_owner == "other_contract" or tenant_state == "different_projection":
        return "ambiguous_state"
    if unit and (unit_owner == "other_contract" or not unit["tenant_matches"]):
        return "ambiguous_state"

    contract_status = str(contract.get("status") or "")
    if contract_status == target:
        return "result_recorded"

    if target == "active":
        occupancy_applied = unit_owner == "exact_contract" if unit else prop_owner == "exact_contract"
        tenant_applied = tenant_state == "exact_contract_projection" and tenant_owner in ("exact_contract", "unclaimed")
        if occupancy_applied and tenant_applied:
            return "projection_applied_status_missing"
        occupancy_absent = unit_owner == "unclaimed" if unit else prop_owner == "unclaimed"
        tenant_absent = tenant_state == "cleared" and tenant_owner == "unclaimed"
        if occupancy_absent and tenant_absent:
            return "no_projection_detected"
        return "partial_projection"

    if target in _RELEASE_TARGETS:
        occupancy_cleared = unit_owner == "unclaimed" if unit else prop_owner == "unclaimed"
        tenant_cleared = tenant_state == "cleared" and tenant_owner == "unclaimed"
        if occupancy_cleared and tenant_cleared:
            return "projection_applied_status_missing"
        occupancy_still_exact = unit_owner == "exact_contract" if unit else prop_owner == "exact_contract"
        tenant_still_exact = tenant_state == "exact_contract_projection" and tenant_owner in ("exact_contract", "unclaimed")
        if occupancy_still_exact and tenant_still_exact:
            return "no_projection_detected"
        return "partial_projection"

    return "ambiguous_state"


@router.get('/admin/rental-contracts/{contract_id}/lifecycle-recovery/{claim_id}')
async def inspect_lifecycle_recovery(contract_id: str, claim_id: str, request: Request):
    await auth_admin(request)
    contract_oid = _oid(contract_id, "lease_contract_invalid")
    if not claim_id or len(claim_id) > 128:
        raise HTTPException(status_code=400, detail="lease_lifecycle_claim_invalid")

    contract = await get_db().rental_contracts.find_one({"_id": contract_oid})
    if not contract:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")
    stored_claim = str(contract.get("lifecycle_claim_id") or "")
    if not stored_claim:
        raise HTTPException(status_code=409, detail="lease_lifecycle_no_recovery_claim")
    if stored_claim != claim_id:
        raise HTTPException(status_code=409, detail="lease_lifecycle_claim_mismatch")

    target = str(contract.get("lifecycle_claim_target") or "")
    if not target:
        raise HTTPException(status_code=409, detail="lease_lifecycle_claim_target_missing")
    observed = await _observe(contract)
    classification = _classify(contract, target, observed)

    return {
        "success": True,
        "read_only": True,
        "automatic_retry_allowed": False,
        "contract_id": contract_id,
        "claim_id": claim_id,
        "current_status": str(contract.get("status") or ""),
        "target_status": target,
        "claimed_at": contract.get("lifecycle_claimed_at"),
        "classification": classification,
        "observed": observed,
    }
