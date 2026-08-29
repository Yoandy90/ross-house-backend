"""Fail-closed boundary for property-unit topology and status mutations.

Lease authority binds a contract to either one exact ``unit_id`` or to the
whole property. Adding/removing units concurrently with lease creation can
change that topology across collections without a transactional serialization
point. Unit operational status also races lease activation if an admin edit can
clear a freshly-written occupancy pointer. Until dedicated topology/archive
workflows exist, topology-changing create/delete is disabled and unit status
changes use a no-claim CAS without ever clearing occupancy fields.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.units_router import sync_property_from_units

router = APIRouter(tags=["unit-topology-security"])
_SAFE_UNIT_STATUSES = {"available", "maintenance"}
_PROFILE_FIELDS = {"unit_name", "notes", "bedrooms", "square_feet", "bathrooms", "rent_amount", "deposit_amount"}


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


@router.post('/admin/properties/{property_id}/units')
async def secure_create_units(property_id: str, request: Request):
    await auth_admin(request)
    object_id = _oid(property_id, "property_id_invalid")
    prop = await get_db().properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    raise HTTPException(status_code=409, detail="unit_topology_requires_managed_workflow")


@router.put('/admin/units/{unit_id}')
async def secure_update_unit(unit_id: str, request: Request):
    await auth_admin(request)
    object_id = _oid(unit_id, "unit_id_invalid")
    db = get_db()
    unit = await db.property_units.find_one({"_id": object_id})
    if not unit:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")

    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="unit_payload_invalid")
    update = _profile_update(data)

    if update.get("unit_name"):
        duplicate = await db.property_units.find_one({
            "property_id": unit.get("property_id"),
            "unit_name": update["unit_name"],
            "_id": {"$ne": object_id},
        })
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
        update["status"] = requested_status

    if not update:
        raise HTTPException(status_code=400, detail="unit_no_changes")
    update["updated_at"] = datetime.utcnow()

    write_filter = {"_id": object_id}
    if status_requested:
        # The status and both occupancy pointers are checked in the same write.
        # If lifecycle activation claims the unit after our read, this CAS loses
        # instead of clearing or overwriting that authoritative claim.
        write_filter["status"] = unit.get("status", "available")
        write_filter["$and"] = [
            {"$or": [{"current_contract_id": {"$exists": False}}, {"current_contract_id": None}, {"current_contract_id": ""}]},
            {"$or": [{"current_tenant_id": {"$exists": False}}, {"current_tenant_id": None}, {"current_tenant_id": ""}]},
        ]

    result = await db.property_units.update_one(write_filter, {"$set": update})
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="unit_state_changed")
    await sync_property_from_units(str(unit.get("property_id") or ""))
    return {"success": True}


@router.delete('/admin/units/{unit_id}')
async def secure_delete_unit(unit_id: str, request: Request):
    await auth_admin(request)
    object_id = _oid(unit_id, "unit_id_invalid")
    unit = await get_db().property_units.find_one({"_id": object_id})
    if not unit:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")
    raise HTTPException(status_code=409, detail="unit_topology_requires_managed_workflow")
