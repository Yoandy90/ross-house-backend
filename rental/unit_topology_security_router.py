"""Fail-closed boundary for property-unit topology mutations.

Lease authority binds a contract to either one exact ``unit_id`` or to the
whole property. Adding/removing units concurrently with lease creation can
change that topology across collections without a transactional serialization
point. Until the dedicated archival/topology workflow exists, keep reads and
unit profile/status edits available but reject topology-changing create/delete
operations instead of risking orphaned or whole-property leases.
"""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db

router = APIRouter(tags=["unit-topology-security"])


def _oid(value: str, detail: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(str(value))


@router.post('/admin/properties/{property_id}/units')
async def secure_create_units(property_id: str, request: Request):
    await auth_admin(request)
    object_id = _oid(property_id, "property_id_invalid")
    prop = await get_db().properties.find_one({"_id": object_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    raise HTTPException(status_code=409, detail="unit_topology_requires_managed_workflow")


@router.delete('/admin/units/{unit_id}')
async def secure_delete_unit(unit_id: str, request: Request):
    await auth_admin(request)
    object_id = _oid(unit_id, "unit_id_invalid")
    unit = await get_db().property_units.find_one({"_id": object_id})
    if not unit:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")
    raise HTTPException(status_code=409, detail="unit_topology_requires_managed_workflow")
