"""Admin-only NOAA/NCEI historical climate endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from bson import ObjectId

from .shared import auth_admin, get_db
from . import ncei_history


router = APIRouter(tags=["climate-history"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackfillYear(StrictModel):
    year: int = Field(ge=1900, le=2100)


def _actor(admin: dict) -> str:
    return str(admin.get("email") or admin.get("_id") or admin.get("id") or "admin")


@router.get("/admin/climate-history/ncei/status")
async def ncei_status(request: Request):
    await auth_admin(request)
    return await ncei_history.status(get_db())


@router.post("/admin/climate-history/ncei/test")
async def ncei_test(request: Request):
    await auth_admin(request)
    if not ncei_history.configured():
        raise HTTPException(503, "ncei_token_not_configured")
    result = await ncei_history.test_connection()
    return {"configured": True, **result}


@router.get("/admin/climate-history/ncei/properties")
async def ncei_properties(request: Request):
    await auth_admin(request)
    db = get_db()
    properties = await db.properties.find(
        {},
        {"address": 1, "city": 1, "state": 1, "zip": 1, "zip_code": 1, "name": 1},
    ).limit(500).to_list(500)
    items = []
    for prop in properties:
        pid = str(prop.get("_id"))
        summary = await ncei_history.property_summary(db, pid)
        items.append({
            "id": pid,
            "name": str(prop.get("name") or prop.get("address") or pid),
            "address": str(prop.get("address") or ""),
            "city": str(prop.get("city") or ""),
            "state": str(prop.get("state") or ""),
            "zip": str(prop.get("zip") or prop.get("zip_code") or ""),
            "history": summary,
        })
    return {"configured": ncei_history.configured(), "properties": items}


@router.get("/admin/climate-history/ncei/properties/{property_id}")
async def ncei_property_summary(property_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    values = [property_id]
    if ObjectId.is_valid(property_id):
        values.append(ObjectId(property_id))
    exists = await db.properties.find_one({"_id": {"$in": values}}, {"_id": 1})
    if not exists:
        raise HTTPException(404, "climate_property_invalid")
    return await ncei_history.property_summary(db, property_id)


@router.post("/admin/climate-history/ncei/properties/{property_id}/backfill")
async def ncei_property_backfill(
    property_id: str,
    body: BackfillYear,
    request: Request,
):
    admin = await auth_admin(request)
    return await ncei_history.backfill_year(
        get_db(), property_id, body.year, actor=_actor(admin)
    )
