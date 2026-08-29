"""Fail-closed public visibility boundary for archived Ross House properties."""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.properties_router import (
    public_get_property as historical_public_get_property,
    public_list_properties as historical_public_list_properties,
    public_list_property_photos as historical_public_list_property_photos,
    public_section8_welcome as historical_public_section8_welcome,
)
from rental.shared import get_db

router = APIRouter(tags=["property-visibility-security"])


async def _archived_property_ids() -> set[str]:
    ids: set[str] = set()
    cursor = get_db().properties.find({"archived_at": {"$exists": True, "$ne": None}}, {"_id": 1})
    async for doc in cursor:
        ids.add(str(doc.get("_id") or ""))
    return ids


async def _raise_if_archived(property_id: str) -> None:
    if not ObjectId.is_valid(str(property_id or "")):
        return
    archived = await get_db().properties.find_one(
        {"_id": ObjectId(property_id), "archived_at": {"$exists": True, "$ne": None}},
        {"_id": 1},
    )
    if archived:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")


@router.get('/public/properties')
async def secure_public_list_properties(request: Request):
    response = await historical_public_list_properties(request)
    archived = await _archived_property_ids()
    properties = [p for p in response.get("properties", []) if str(p.get("id") or "") not in archived]
    return {**response, "properties": properties, "count": len(properties)}


@router.get('/public/section8-welcome')
async def secure_public_section8_welcome():
    response = await historical_public_section8_welcome()
    archived = await _archived_property_ids()
    properties = [p for p in response.get("properties", []) if str(p.get("id") or "") not in archived]
    return {**response, "properties": properties, "count": len(properties)}


@router.get('/public/property-photos/{property_id}')
async def secure_public_list_property_photos(property_id: str):
    await _raise_if_archived(property_id)
    return await historical_public_list_property_photos(property_id)


@router.get('/public/properties/{property_id}')
async def secure_public_get_property(property_id: str, request: Request):
    await _raise_if_archived(property_id)
    return await historical_public_get_property(property_id, request)
