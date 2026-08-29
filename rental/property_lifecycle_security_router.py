"""Security boundary for admin property mutations tied to lease occupancy.

Property ``rented`` state is a projection of the canonical lease lifecycle, not
an administrator-editable flag. These first-match routes preserve normal
property profile editing while preventing manual occupancy creation/release.
Hard deletion is deliberately disabled until an archival workflow can serialize
against concurrent lease creation without cross-collection races.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

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
    """Profile edits are allowed; status changes use a no-claim CAS."""
    admin = await auth_admin(request)
    object_id = _oid(property_id)
    db = get_db()
    prop = await db.properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

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
        # Historical manual locks can make lifecycle release skip a projection.
        # Safe operational status edits therefore clear that legacy lock instead
        # of creating a second source of occupancy authority.
        update_fields["status_manually_set"] = False

    write_filter = {"_id": object_id}
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
    """Fail closed: hard-delete cannot be serialized against lease creation."""
    await auth_admin(request)
    object_id = _oid(property_id)
    prop = await get_db().properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    # A legacy ?force=true query is intentionally ignored. Archival must be a
    # separate state machine so contracts/units can never be orphaned by TOCTOU.
    raise HTTPException(status_code=409, detail="property_delete_requires_archival")
