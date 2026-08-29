"""Security boundary for admin property mutations tied to lease occupancy.

Property ``rented`` state is a projection of the canonical lease lifecycle, not
an administrator-editable flag. These first-match routes preserve normal
property profile editing while preventing manual occupancy creation/release.
Archived properties are immutable until an explicit restore workflow reopens
operational mutation authority.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from rental.property_mutation_lock import (
    acquire_property_mutation_lock,
    assert_property_lifecycle_recovery_clear,
    release_property_mutation_lock,
)
from rental.properties_router import create_property as historical_create_property
from rental.shared import auth_admin, get_db

router = APIRouter(tags=["property-lifecycle-security"])

_SAFE_MANUAL_PROPERTY_STATES = {"available", "maintenance"}
_PROFILE_FIELDS = {
    "name", "address", "city", "state", "zip_code", "type", "bedrooms", "bathrooms",
    "square_feet", "rent_amount", "deposit_amount", "features", "notes", "description",
    "section8_accepted", "section8_pha", "section8_pha_contact",
    "section8_last_inspection", "section8_next_inspection", "section8_notes",
    "tax_account_id", "tax_annual_estimate",
}


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail="property_id_invalid")
    return ObjectId(str(value))


def _has_claim(prop: dict) -> bool:
    return bool(str(prop.get("current_contract_id") or "").strip() or str(prop.get("current_tenant_id") or "").strip())


def _profile_update(data: dict) -> dict:
    normalized = dict(data)
    if "zip" in normalized and "zip_code" not in normalized:
        normalized["zip_code"] = normalized["zip"]
    if "sqft" in normalized and "square_feet" not in normalized:
        normalized["square_feet"] = normalized["sqft"]

    update = {}
    try:
        for field in _PROFILE_FIELDS:
            if field not in normalized:
                continue
            value = normalized[field]
            if field in {"bedrooms", "square_feet"}:
                update[field] = int(value)
            elif field in {"bathrooms", "rent_amount", "deposit_amount", "tax_annual_estimate"}:
                update[field] = float(value)
            elif field == "section8_accepted":
                update[field] = bool(value)
            else:
                update[field] = value
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="property_profile_value_invalid")
    return update


@router.post('/admin/properties')
async def secure_create_property(request: Request, background_tasks: BackgroundTasks):
    """Forbid creating non-canonical or already-rented property state."""
    await auth_admin(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="property_payload_invalid")

    status_supplied = "status" in data
    raw_status = data.get("status") if status_supplied else "available"
    requested_status = str(raw_status or "available").strip().lower()
    if requested_status == "rented":
        raise HTTPException(status_code=409, detail="property_rented_status_lifecycle_managed")
    if requested_status not in _SAFE_MANUAL_PROPERTY_STATES:
        raise HTTPException(status_code=400, detail="property_status_invalid")
    if status_supplied and str(raw_status) != requested_status:
        raise HTTPException(status_code=400, detail="property_status_not_canonical")
    return await historical_create_property(request, background_tasks)


@router.put('/admin/properties/{property_id}')
async def secure_update_property(property_id: str, request: Request, background_tasks: BackgroundTasks):
    """Serialize profile/status writes with lease creation and topology changes."""
    admin = await auth_admin(request)
    _oid(property_id)
    token = await acquire_property_mutation_lock(
        property_id,
        "property_update",
        str(admin.get("email") or admin.get("_id") or "admin"),
    )
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        return await _secure_update_property_under_lock(property_id, request, background_tasks)
    finally:
        await release_property_mutation_lock(property_id, token)


async def _secure_update_property_under_lock(property_id: str, request: Request, background_tasks: BackgroundTasks):
    object_id = _oid(property_id)
    db = get_db()
    prop = await db.properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    if prop.get("archived_at"):
        raise HTTPException(status_code=409, detail="property_archived")

    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="property_payload_invalid")
    update_fields = _profile_update(data)
    now = datetime.utcnow()
    update_fields["updated_at"] = now

    status_requested = "status" in data
    requested_status = None
    status_changed = False
    current_status = str(prop.get("status") or "available").strip().lower()
    if status_requested:
        requested_status = str(data.get("status") or "").strip().lower()
        if requested_status == "rented":
            raise HTTPException(status_code=409, detail="property_rented_status_lifecycle_managed")
        if requested_status not in _SAFE_MANUAL_PROPERTY_STATES:
            raise HTTPException(status_code=400, detail="property_status_invalid")
        status_changed = requested_status != current_status

        if status_changed:
            if _has_claim(prop):
                raise HTTPException(status_code=409, detail="property_occupancy_claimed")
            active_contract = await db.rental_contracts.find_one({
                "property_id": str(prop["_id"]),
                "status": "active",
            })
            if active_contract:
                raise HTTPException(status_code=409, detail="property_active_lease_conflict")
        update_fields["status"] = requested_status
        update_fields["status_manually_set"] = False

    write_filter = {"_id": object_id, "$or": [{"archived_at": {"$exists": False}}, {"archived_at": None}]}
    update_doc = {"$set": update_fields}
    if status_requested:
        write_filter["status"] = prop.get("status", "available")
        write_filter["$and"] = [
            {"$or": [{"current_contract_id": {"$exists": False}}, {"current_contract_id": None}, {"current_contract_id": ""}]},
            {"$or": [{"current_tenant_id": {"$exists": False}}, {"current_tenant_id": None}, {"current_tenant_id": ""}]},
        ]
        update_doc["$unset"] = {
            "status_manually_set_at": "",
            "status_manually_set_by": "",
        }

    result = await db.properties.update_one(write_filter, update_doc)
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="property_state_changed")

    if status_changed and requested_status == "available":
        from rental.newsletter_router import announce_property_available
        from rental.social_poster_router import auto_generate_property_post
        background_tasks.add_task(announce_property_available, property_id)
        background_tasks.add_task(auto_generate_property_post, property_id)

    return {"success": True, "message": "Propiedad actualizada"}


@router.delete('/admin/properties/{property_id}')
async def secure_delete_property(property_id: str, request: Request):
    """Compatibility fallback; archival security route wins first-match."""
    await auth_admin(request)
    object_id = _oid(property_id)
    prop = await get_db().properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    raise HTTPException(status_code=409, detail="property_delete_requires_archival")
