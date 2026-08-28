"""Fail-closed lease lifecycle transitions for occupancy mutation.

A per-contract lifecycle claim serializes the multi-document projection writes.
The contract is the authority; unit/property/tenant occupancy fields are guarded
projections and must never be overwritten by a stale lease transition.

Once a lifecycle claim is acquired it is deliberately retained on any failed
multi-document transition.  A failure may have happened after one projection
was already written, so clearing the claim would permit an unsafe blind retry.
Recovery is explicit and separately inspected.
"""
from datetime import datetime
import secrets

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.units_router import mark_unit_rented, sync_property_from_units

router = APIRouter()
_ALLOWED = {"active", "terminated", "expired", "draft", "pending_signature", "pending",
            "pending_tenant", "pending_landlord", "pending_signatures", "pending_activation"}
_RELEASE = {"terminated", "expired", "draft", "pending_signature", "pending"}


def _oid(value: str, detail: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(str(value))


async def _release_unit(contract: dict, contract_id: str, now: datetime) -> None:
    db = get_db()
    unit_id = str(contract.get("unit_id") or "")
    if not unit_id:
        return
    unit_oid = _oid(unit_id, "lease_unit_invalid")
    unit = await db.property_units.find_one({"_id": unit_oid})
    if not unit:
        raise HTTPException(status_code=409, detail="lease_unit_missing")
    if str(unit.get("property_id") or "") != str(contract.get("property_id") or ""):
        raise HTTPException(status_code=409, detail="lease_unit_property_mismatch")
    current = str(unit.get("current_contract_id") or "")
    if current and current != contract_id:
        raise HTTPException(status_code=409, detail="lease_unit_owned_by_other_contract")
    if not current:
        return
    tenant_id = str(contract.get("tenant_id") or "")
    if str(unit.get("current_tenant_id") or "") not in ("", tenant_id):
        raise HTTPException(status_code=409, detail="lease_unit_tenant_mismatch")
    result = await db.property_units.update_one(
        {"_id": unit_oid, "current_contract_id": contract_id},
        {"$set": {"status": "available", "current_contract_id": None,
                  "current_tenant_id": None, "updated_at": now}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="lease_unit_release_changed")
    await sync_property_from_units(str(unit.get("property_id") or ""))


async def _release_property(contract: dict, contract_id: str, now: datetime) -> None:
    db = get_db()
    property_id = str(contract.get("property_id") or "")
    prop_oid = _oid(property_id, "lease_property_invalid")
    prop = await db.properties.find_one({"_id": prop_oid})
    if not prop:
        raise HTTPException(status_code=409, detail="lease_property_missing")
    current = str(prop.get("current_contract_id") or "")
    if current and current != contract_id:
        raise HTTPException(status_code=409, detail="lease_property_owned_by_other_contract")
    if prop.get("status_manually_set") or not current:
        return
    result = await db.properties.update_one(
        {"_id": prop_oid, "current_contract_id": contract_id},
        {"$set": {"status": "available", "current_contract_id": None,
                  "current_tenant_id": None, "updated_at": now}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="lease_property_release_changed")


async def _release_tenant(contract: dict, contract_id: str, now: datetime) -> None:
    db = get_db()
    tenant_id = str(contract.get("tenant_id") or "")
    tenant_oid = _oid(tenant_id, "lease_tenant_invalid")
    tenant = await db.tenants.find_one({"_id": tenant_oid})
    if not tenant:
        raise HTTPException(status_code=409, detail="lease_tenant_missing")
    other = await db.rental_contracts.find_one({
        "tenant_id": tenant_id, "status": "active", "_id": {"$ne": ObjectId(contract_id)}
    })
    if other:
        return
    expected_property = str(contract.get("property_id") or "")
    current_property = str(tenant.get("current_property_id") or "")
    if current_property and current_property != expected_property:
        raise HTTPException(status_code=409, detail="lease_tenant_property_changed")
    result = await db.tenants.update_one(
        {"_id": tenant_oid, "$or": [
            {"current_property_id": expected_property}, {"current_property_id": None},
            {"current_property_id": ""}, {"current_property_id": {"$exists": False}},
        ]},
        {"$set": {"current_property_id": None, "current_unit_id": None, "updated_at": now},
         "$push": {"rental_history": {"contract_id": contract_id,
             "property_id": expected_property, "property_address": contract.get("property_address", ""),
             "start_date": contract.get("start_date"), "end_date": now.strftime("%Y-%m-%d"),
             "rent_amount": contract.get("rent_amount", 0)}}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="lease_tenant_release_changed")


async def _claim_lifecycle(contract_oid: ObjectId, old_status: str, new_status: str) -> str:
    db = get_db()
    claim_id = secrets.token_hex(16)
    now = datetime.utcnow()
    result = await db.rental_contracts.update_one(
        {"_id": contract_oid, "status": old_status,
         "$or": [{"lifecycle_claim_id": {"$exists": False}}, {"lifecycle_claim_id": None}]},
        {"$set": {"lifecycle_claim_id": claim_id, "lifecycle_claim_target": new_status,
                  "lifecycle_claimed_at": now}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="lease_lifecycle_busy_or_changed")
    return claim_id


@router.patch('/admin/rental-contracts/{contract_id}/status')
async def secure_update_contract_status(contract_id: str, request: Request):
    await auth_admin(request)
    contract_oid = _oid(contract_id, "lease_contract_invalid")
    db = get_db()
    data = await request.json()
    new_status = str(data.get("status") or "").strip()
    if new_status not in _ALLOWED:
        raise HTTPException(status_code=400, detail="lease_status_invalid")
    contract = await db.rental_contracts.find_one({"_id": contract_oid})
    if not contract:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")
    old_status = str(contract.get("status") or "")
    if new_status == old_status:
        return {"success": True, "message": f"Contrato ya está: {new_status}"}

    if new_status == "active":
        if not contract.get("tenant_signature") or not (
            contract.get("landlord_signature") or contract.get("admin_signature")
        ):
            if not bool(data.get("force_activate", False)):
                raise HTTPException(status_code=400, detail="lease_signatures_required")
        tenant_id = str(contract.get("tenant_id") or "")
        other_tenant_lease = await db.rental_contracts.find_one({
            "tenant_id": tenant_id, "status": "active", "_id": {"$ne": contract_oid}
        })
        if other_tenant_lease:
            raise HTTPException(status_code=409, detail="lease_tenant_already_active_elsewhere")

    claim_id = await _claim_lifecycle(contract_oid, old_status, new_status)
    now = datetime.utcnow()
    try:
        if new_status == "active":
            tenant_id = str(contract.get("tenant_id") or "")
            tenant_oid = _oid(tenant_id, "lease_tenant_invalid")
            tenant = await db.tenants.find_one({"_id": tenant_oid})
            if not tenant:
                raise HTTPException(status_code=409, detail="lease_tenant_missing")
            expected_property = str(contract.get("property_id") or "")
            current_property = str(tenant.get("current_property_id") or "")
            if current_property and current_property != expected_property:
                raise HTTPException(status_code=409, detail="lease_tenant_property_changed")
            if contract.get("unit_id"):
                await mark_unit_rented(str(contract["unit_id"]), tenant_id, contract_id)
            else:
                prop_oid = _oid(expected_property, "lease_property_invalid")
                claim = await db.properties.update_one(
                    {"_id": prop_oid, "$or": [
                        {"current_contract_id": contract_id}, {"current_contract_id": None},
                        {"current_contract_id": ""}, {"current_contract_id": {"$exists": False}},
                    ]},
                    {"$set": {"status": "rented", "current_contract_id": contract_id,
                              "current_tenant_id": tenant_id, "updated_at": now}},
                )
                if claim.matched_count != 1:
                    raise HTTPException(status_code=409, detail="lease_property_occupancy_changed")
            tenant_write = await db.tenants.update_one(
                {"_id": tenant_oid, "$or": [
                    {"current_property_id": expected_property}, {"current_property_id": None},
                    {"current_property_id": ""}, {"current_property_id": {"$exists": False}},
                ]},
                {"$set": {"current_property_id": expected_property,
                          "current_unit_id": contract.get("unit_id"), "updated_at": now}},
            )
            if tenant_write.matched_count != 1:
                raise HTTPException(status_code=409, detail="lease_tenant_occupancy_changed")
        elif new_status in _RELEASE:
            if contract.get("unit_id"):
                await _release_unit(contract, contract_id, now)
            else:
                await _release_property(contract, contract_id, now)
            await _release_tenant(contract, contract_id, now)

        result = await db.rental_contracts.update_one(
            {"_id": contract_oid, "status": old_status, "lifecycle_claim_id": claim_id},
            {"$set": {"status": new_status, "updated_at": now},
             "$unset": {"lifecycle_claim_id": "", "lifecycle_claim_target": "", "lifecycle_claimed_at": ""}},
        )
        if result.matched_count != 1:
            raise HTTPException(status_code=409, detail="lease_status_changed")
    except Exception:
        # Fail closed.  We cannot know whether a prior projection write committed.
        # Retain the exact claim so another transition cannot retry or release by
        # inference.  The read-only recovery inspector classifies observed state.
        raise
    return {"success": True, "message": f"Contrato actualizado a: {new_status}"}
