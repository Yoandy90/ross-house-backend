"""Security boundary for admin property mutations tied to lease occupancy.

Property ``rented`` state is a projection of the canonical lease lifecycle, not
an administrator-editable flag. These first-match routes preserve normal
property profile editing while preventing manual occupancy creation/release and
force deletion of records still participating in rental relationships.
"""
from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from rental.properties_router import create_property as historical_create_property
from rental.properties_router import update_property as historical_update_property
from rental.shared import auth_admin, get_db

router = APIRouter(tags=["property-lifecycle-security"])

_NONTERMINAL_CONTRACT_STATES = {
    "draft",
    "pending",
    "pending_signature",
    "pending_signatures",
    "pending_tenant",
    "pending_landlord",
    "pending_activation",
    "active",
}
_SAFE_MANUAL_PROPERTY_STATES = {"available", "maintenance"}


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail="property_id_invalid")
    return ObjectId(str(value))


def _has_claim(prop: dict) -> bool:
    return bool(str(prop.get("current_contract_id") or "").strip() or str(prop.get("current_tenant_id") or "").strip())


@router.post('/admin/properties')
async def secure_create_property(request: Request, background_tasks: BackgroundTasks):
    """Forbid creating an already-rented property outside lease activation."""
    await auth_admin(request)
    data = await request.json()
    requested_status = str(data.get("status") or "available").strip().lower()
    if requested_status == "rented":
        raise HTTPException(status_code=409, detail="property_rented_status_lifecycle_managed")
    if requested_status not in _SAFE_MANUAL_PROPERTY_STATES:
        raise HTTPException(status_code=400, detail="property_status_invalid")
    return await historical_create_property(request, background_tasks)


@router.put('/admin/properties/{property_id}')
async def secure_update_property(property_id: str, request: Request, background_tasks: BackgroundTasks):
    """Allow profile edits, but keep occupancy status under lease authority."""
    await auth_admin(request)
    object_id = _oid(property_id)
    db = get_db()
    prop = await db.properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    data = await request.json()
    if "status" in data:
        requested_status = str(data.get("status") or "").strip().lower()
        current_status = str(prop.get("status") or "available").strip().lower()

        if requested_status == "rented":
            raise HTTPException(status_code=409, detail="property_rented_status_lifecycle_managed")
        if requested_status not in _SAFE_MANUAL_PROPERTY_STATES:
            raise HTTPException(status_code=400, detail="property_status_invalid")

        if requested_status != current_status:
            if _has_claim(prop):
                raise HTTPException(status_code=409, detail="property_occupancy_claimed")
            active_contract = await db.rental_contracts.find_one({
                "property_id": str(prop["_id"]),
                "status": "active",
            })
            if active_contract:
                raise HTTPException(status_code=409, detail="property_active_lease_conflict")

    # The historical handler owns the broad non-lifecycle profile schema and
    # background announcements. It is safe to delegate only after the status
    # authority checks above have closed the occupancy escape hatch.
    return await historical_update_property(property_id, request, background_tasks)


@router.delete('/admin/properties/{property_id}')
async def secure_delete_property(property_id: str, request: Request):
    """Delete only a relationship-free property; ``?force=true`` is inert."""
    await auth_admin(request)
    object_id = _oid(property_id)
    db = get_db()
    prop = await db.properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    if _has_claim(prop):
        raise HTTPException(status_code=409, detail="property_delete_occupancy_claimed")

    linked_contract = await db.rental_contracts.find_one({
        "property_id": str(prop["_id"]),
        "status": {"$in": sorted(_NONTERMINAL_CONTRACT_STATES)},
    })
    if linked_contract:
        raise HTTPException(status_code=409, detail="property_delete_contract_exists")

    linked_unit = await db.property_units.find_one({"property_id": str(prop["_id"])})
    if linked_unit:
        raise HTTPException(status_code=409, detail="property_delete_units_exist")

    # CAS-style final filter rechecks that no occupancy pointers appeared after
    # validation. A concurrent lifecycle claim therefore prevents deletion.
    result = await db.properties.delete_one({
        "_id": object_id,
        "$and": [
            {"$or": [{"current_contract_id": {"$exists": False}}, {"current_contract_id": None}, {"current_contract_id": ""}]},
            {"$or": [{"current_tenant_id": {"$exists": False}}, {"current_tenant_id": None}, {"current_tenant_id": ""}]},
        ],
    })
    if getattr(result, "deleted_count", 0) != 1:
        raise HTTPException(status_code=409, detail="property_delete_state_changed")
    return {"success": True, "message": f"Propiedad {prop.get('property_number', '')} eliminada"}
