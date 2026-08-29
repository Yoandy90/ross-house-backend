"""Serialized boundary for property-unit topology and status mutations.

Lease authority binds a contract to either one exact ``unit_id`` or to the
whole property. Unit create/update/delete operations share the property
mutation lock with canonical lease creation and property updates, so a new
lease cannot race a topology or operational unit write across collections.
Archived properties freeze topology until explicitly restored.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.property_mutation_lock import (
    acquire_property_mutation_lock,
    assert_property_lifecycle_recovery_clear,
    release_property_mutation_lock,
)
from rental.shared import auth_admin, get_db
from rental.units_router import create_units as historical_create_units, delete_unit as historical_delete_unit, sync_property_from_units

router = APIRouter(tags=["unit-topology-security"])
_SAFE_UNIT_STATUSES = {"available", "maintenance"}
_PROFILE_FIELDS = {"unit_name", "notes", "bedrooms", "square_feet", "bathrooms", "rent_amount", "deposit_amount"}
_TERMINAL_CONTRACT_STATUSES = ["terminated", "expired"]


def _oid(value: str, detail: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(str(value))


def _profile_update(data: dict) -> dict:
    update = {}
    try:
        for field in _PROFILE_FIELDS:
            if field not in data:
                continue
            value = data[field]
            if field in {"unit_name", "notes"}:
                update[field] = str(value).strip()
            elif field in {"bedrooms", "square_feet"}:
                update[field] = int(value or 0)
            else:
                update[field] = float(value or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="unit_profile_value_invalid")
    return update


async def _acquire_topology_lock(property_id: str, admin: dict, operation: str) -> str:
    return await acquire_property_mutation_lock(property_id, operation, str(admin.get("email") or admin.get("_id") or "admin"))


async def _assert_property_not_archived(property_id: str) -> dict:
    prop = await get_db().properties.find_one({"_id": _oid(property_id, "property_id_invalid")})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    if prop.get("archived_at"):
        raise HTTPException(status_code=409, detail="property_archived")
    return prop


@router.post('/admin/properties/{property_id}/units')
async def secure_create_units(property_id: str, request: Request):
    admin = await auth_admin(request)
    _oid(property_id, "property_id_invalid")
    token = await _acquire_topology_lock(property_id, admin, "unit_topology_create")
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        db = get_db()
        prop = await _assert_property_not_archived(property_id)
        if str(prop.get("status") or "available").strip().lower() != "available":
            raise HTTPException(status_code=409, detail="unit_topology_property_not_available")
        if prop.get("current_contract_id") or prop.get("current_tenant_id"):
            raise HTTPException(status_code=409, detail="unit_topology_property_claimed")
        whole_property_contract = await db.rental_contracts.find_one({"property_id": property_id, "$and": [{"$or": [{"unit_id": {"$exists": False}}, {"unit_id": None}, {"unit_id": ""}]}, {"status": {"$nin": _TERMINAL_CONTRACT_STATUSES}}]})
        if whole_property_contract:
            raise HTTPException(status_code=409, detail="unit_topology_whole_property_contract_conflict")
        return await historical_create_units(property_id, request)
    finally:
        await release_property_mutation_lock(property_id, token)


@router.put('/admin/units/{unit_id}')
async def secure_update_unit(unit_id: str, request: Request):
    admin = await auth_admin(request)
    object_id = _oid(unit_id, "unit_id_invalid")
    db = get_db()
    initial = await db.property_units.find_one({"_id": object_id})
    if not initial:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")
    property_id = str(initial.get("property_id") or "")
    _oid(property_id, "property_id_invalid")
    token = await _acquire_topology_lock(property_id, admin, "unit_update")
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        await _assert_property_not_archived(property_id)
        return await _secure_update_unit_under_lock(object_id, property_id, request)
    finally:
        await release_property_mutation_lock(property_id, token)


async def _secure_update_unit_under_lock(object_id: ObjectId, property_id: str, request: Request):
    db = get_db()
    unit = await db.property_units.find_one({"_id": object_id, "property_id": property_id})
    if not unit:
        raise HTTPException(status_code=409, detail="unit_topology_state_changed")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="unit_payload_invalid")
    update = _profile_update(data)
    if update.get("unit_name"):
        duplicate = await db.property_units.find_one({"property_id": property_id, "unit_name": update["unit_name"], "_id": {"$ne": object_id}})
        if duplicate:
            raise HTTPException(status_code=400, detail="Nombre de unidad duplicado")
    status_requested = "status" in data
    if status_requested:
        requested_status = str(data.get("status") or "").strip().lower()
        if requested_status == "rented":
            raise HTTPException(status_code=409, detail="unit_rented_status_lifecycle_managed")
        if requested_status not in _SAFE_UNIT_STATUSES:
            raise HTTPException(status_code=400, detail="unit_status_invalid")
        if unit.get("current_contract_id") or unit.get("current_tenant_id"):
            raise HTTPException(status_code=409, detail="unit_occupancy_claimed")
        if requested_status == "maintenance":
            pending_contract = await db.rental_contracts.find_one({"property_id": property_id, "unit_id": str(object_id), "status": {"$nin": _TERMINAL_CONTRACT_STATUSES}})
            if pending_contract:
                raise HTTPException(status_code=409, detail="unit_contract_pending_activation")
        update["status"] = requested_status
    if not update:
        raise HTTPException(status_code=400, detail="unit_no_changes")
    update["updated_at"] = datetime.utcnow()
    write_filter = {"_id": object_id, "property_id": property_id}
    if status_requested:
        write_filter["status"] = unit.get("status", "available")
        write_filter["$and"] = [{"$or": [{"current_contract_id": {"$exists": False}}, {"current_contract_id": None}, {"current_contract_id": ""}]}, {"$or": [{"current_tenant_id": {"$exists": False}}, {"current_tenant_id": None}, {"current_tenant_id": ""}]}]
    result = await db.property_units.update_one(write_filter, {"$set": update})
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="unit_state_changed")
    await sync_property_from_units(property_id)
    return {"success": True}


@router.delete('/admin/units/{unit_id}')
async def secure_delete_unit(unit_id: str, request: Request):
    admin = await auth_admin(request)
    object_id = _oid(unit_id, "unit_id_invalid")
    db = get_db()
    initial = await db.property_units.find_one({"_id": object_id})
    if not initial:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")
    property_id = str(initial.get("property_id") or "")
    _oid(property_id, "property_id_invalid")
    token = await _acquire_topology_lock(property_id, admin, "unit_topology_delete")
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        await _assert_property_not_archived(property_id)
        unit = await db.property_units.find_one({"_id": object_id, "property_id": property_id})
        if not unit:
            raise HTTPException(status_code=409, detail="unit_topology_state_changed")
        if unit.get("status") == "rented" or unit.get("current_contract_id") or unit.get("current_tenant_id"):
            raise HTTPException(status_code=409, detail="unit_topology_unit_claimed")
        contract = await db.rental_contracts.find_one({"property_id": property_id, "unit_id": unit_id})
        if contract:
            raise HTTPException(status_code=409, detail="unit_topology_contract_conflict")
        return await historical_delete_unit(unit_id, request)
    finally:
        await release_property_mutation_lock(property_id, token)
