"""Canonical property archival boundary.

Archival is a reversible, non-destructive lifecycle operation. It shares the
property mutation lock with lease creation, lease lifecycle and unit topology,
so historical records remain intact while new rental authority is fenced off.
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

router = APIRouter(tags=["property-archival-security"])
_TERMINAL_CONTRACT_STATUSES = ["terminated", "expired"]


def _oid(value: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail="property_id_invalid")
    return ObjectId(str(value))


async def _actor_and_lock(property_id: str, request: Request, operation: str):
    admin = await auth_admin(request)
    actor = str(admin.get("email") or admin.get("_id") or "admin")
    token = await acquire_property_mutation_lock(property_id, operation, actor)
    return actor, token


@router.delete('/admin/properties/{property_id}')
async def archive_property(property_id: str, request: Request):
    object_id = _oid(property_id)
    actor, token = await _actor_and_lock(property_id, request, "property_archive")
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        db = get_db()
        prop = await db.properties.find_one({"_id": object_id})
        if not prop:
            raise HTTPException(status_code=404, detail="Propiedad no encontrada")
        if prop.get("archived_at"):
            return {"success": True, "archived": True, "property_id": property_id}
        if prop.get("current_contract_id") or prop.get("current_tenant_id") or str(prop.get("status") or "").lower() == "rented":
            raise HTTPException(status_code=409, detail="property_archive_occupancy_conflict")
        contract = await db.rental_contracts.find_one({
            "property_id": property_id,
            "status": {"$nin": _TERMINAL_CONTRACT_STATUSES},
        })
        if contract:
            raise HTTPException(status_code=409, detail="property_archive_contract_conflict")
        claimed_unit = await db.property_units.find_one({
            "property_id": property_id,
            "$or": [
                {"current_contract_id": {"$nin": [None, ""]}},
                {"current_tenant_id": {"$nin": [None, ""]}},
                {"status": "rented"},
            ],
        })
        if claimed_unit:
            raise HTTPException(status_code=409, detail="property_archive_unit_occupancy_conflict")
        now = datetime.utcnow()
        result = await db.properties.update_one(
            {
                "_id": object_id,
                "mutation_lock.token": token,
                "$and": [
                    {"$or": [{"archived_at": {"$exists": False}}, {"archived_at": None}]},
                    {"$or": [{"current_contract_id": {"$exists": False}}, {"current_contract_id": None}, {"current_contract_id": ""}]},
                    {"$or": [{"current_tenant_id": {"$exists": False}}, {"current_tenant_id": None}, {"current_tenant_id": ""}]},
                ],
            },
            {"$set": {"archived_at": now, "archived_by": actor, "updated_at": now}},
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="property_archive_state_changed")
        return {"success": True, "archived": True, "property_id": property_id}
    finally:
        await release_property_mutation_lock(property_id, token)


@router.post('/admin/properties/{property_id}/restore')
async def restore_property(property_id: str, request: Request):
    object_id = _oid(property_id)
    actor, token = await _actor_and_lock(property_id, request, "property_restore")
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        db = get_db()
        prop = await db.properties.find_one({"_id": object_id})
        if not prop:
            raise HTTPException(status_code=404, detail="Propiedad no encontrada")
        if not prop.get("archived_at"):
            return {"success": True, "archived": False, "property_id": property_id}
        if prop.get("current_contract_id") or prop.get("current_tenant_id") or str(prop.get("status") or "").lower() == "rented":
            raise HTTPException(status_code=409, detail="property_restore_occupancy_conflict")
        contract = await db.rental_contracts.find_one({
            "property_id": property_id,
            "status": {"$nin": _TERMINAL_CONTRACT_STATUSES},
        })
        if contract:
            raise HTTPException(status_code=409, detail="property_restore_contract_conflict")
        now = datetime.utcnow()
        result = await db.properties.update_one(
            {"_id": object_id, "mutation_lock.token": token, "archived_at": prop.get("archived_at")},
            {
                "$unset": {"archived_at": "", "archived_by": ""},
                "$set": {"restored_at": now, "restored_by": actor, "updated_at": now},
            },
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="property_restore_state_changed")
        return {"success": True, "archived": False, "property_id": property_id}
    finally:
        await release_property_mutation_lock(property_id, token)
