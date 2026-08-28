"""Actor-bound tenant maintenance routes.

Compatibility shim mounted ahead of historical tenant_router routes via
``auth_metrics.router``.  Tenant-submitted maintenance records derive tenant,
contract, property and unit identity exclusively from authenticated server-side
state.  Client payloads never choose those relationships.
"""
import logging
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import (
    auth_marketplace,
    get_db,
    send_rental_push_to_admins,
    send_rental_push_to_user,
)
from rental.tenant_integrity import (
    find_active_contract_for_tenant,
    resolve_authenticated_tenant,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_ALLOWED_CATEGORIES = {
    "plumbing", "electrical", "hvac", "appliance", "general",
    "structural", "pest", "other",
}
_ALLOWED_PRIORITIES = {"low", "normal", "medium", "high", "urgent"}
_MAX_PHOTOS = 5
_MAX_PHOTO_CHARS = 1_500_000


async def _canonical_lease_location(contract: dict) -> dict:
    """Resolve and validate the exact property/unit represented by a lease."""
    db = get_db()
    property_id = str(contract.get("property_id") or "")
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=409, detail="maintenance_contract_property_invalid")

    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=409, detail="maintenance_contract_property_missing")

    unit_id = str(contract.get("unit_id") or "")
    unit = None
    if unit_id:
        if not ObjectId.is_valid(unit_id):
            raise HTTPException(status_code=409, detail="maintenance_contract_unit_invalid")
        unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
        if not unit:
            raise HTTPException(status_code=409, detail="maintenance_contract_unit_missing")
        if str(unit.get("property_id") or "") != property_id:
            raise HTTPException(status_code=409, detail="maintenance_unit_property_mismatch")
        if str(unit.get("current_contract_id") or "") not in ("", str(contract["_id"])):
            raise HTTPException(status_code=409, detail="maintenance_unit_contract_mismatch")

    address = prop.get("address", "")
    if unit and unit.get("unit_name"):
        address = f"{address} · {unit.get('unit_name')}"

    return {
        "property_id": property_id,
        "property_address": address or contract.get("property_address", ""),
        "unit_id": unit_id or None,
        "property": prop,
    }


def _validated_photos(value) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="maintenance_photos_invalid")
    if len(value) > _MAX_PHOTOS:
        raise HTTPException(status_code=400, detail="maintenance_photos_too_many")
    out = []
    for photo in value:
        if not isinstance(photo, str):
            raise HTTPException(status_code=400, detail="maintenance_photo_invalid")
        if len(photo) > _MAX_PHOTO_CHARS:
            raise HTTPException(status_code=400, detail="maintenance_photo_too_large")
        if not (photo.startswith("data:image/") or photo.startswith("https://")):
            raise HTTPException(status_code=400, detail="maintenance_photo_invalid")
        out.append(photo)
    return out


@router.post('/tenant/maintenance-request')
async def secure_create_maintenance_request(request: Request):
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        raise HTTPException(status_code=403, detail="maintenance_tenant_not_linked")

    contract = await find_active_contract_for_tenant(tenant)
    if not contract:
        raise HTTPException(status_code=403, detail="maintenance_active_lease_required")

    location = await _canonical_lease_location(contract)
    data = await request.json()
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    if not title or not description:
        raise HTTPException(status_code=400, detail="Título y descripción son requeridos")
    if len(title) > 160 or len(description) > 6000:
        raise HTTPException(status_code=400, detail="maintenance_text_too_long")

    category = str(data.get("category") or "general").strip().lower()
    priority = str(data.get("priority") or "normal").strip().lower()
    if category not in _ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400, detail="maintenance_category_invalid")
    if priority not in _ALLOWED_PRIORITIES:
        raise HTTPException(status_code=400, detail="maintenance_priority_invalid")
    photos = _validated_photos(data.get("photos", []))

    now = datetime.utcnow()
    tenant_id = str(tenant["_id"])
    contract_id = str(contract["_id"])
    maintenance = {
        "tenant_id": tenant_id,
        "tenant_name": tenant.get("name", ""),
        "tenant_email": tenant.get("email", ""),
        "tenant_phone": tenant.get("phone", ""),
        "contract_id": contract_id,
        "property_id": location["property_id"],
        "unit_id": location["unit_id"],
        "property_address": location["property_address"],
        "title": title,
        "description": description,
        "category": category,
        "priority": priority,
        "status": "pending",
        "photos": photos,
        "relationship_source": "active_contract",
        "created_at": now,
        "updated_at": now,
    }
    result = await get_db().maintenance_requests.insert_one(maintenance)
    request_id = str(result.inserted_id)

    # Notifications are advisory only and may never roll back a valid ticket.
    try:
        await send_rental_push_to_admins(
            title="🔧 Nueva Solicitud de Mantenimiento",
            body=f"{tenant.get('name', 'Inquilino')}: {title}",
            data={"type": "maintenance_new", "request_id": request_id},
        )
        owner_id = location["property"].get("owner_id")
        if owner_id:
            await send_rental_push_to_user(
                user_id=str(owner_id),
                title="🔧 Solicitud de Mantenimiento",
                body=f"{tenant.get('name', 'Inquilino')}: {title}",
                data={"type": "maintenance_new", "request_id": request_id},
            )
    except Exception as exc:
        logger.warning("maintenance notification failed: %s", exc)

    return {"success": True, "message": "Solicitud de mantenimiento creada", "request_id": request_id}


@router.get('/tenant/maintenance-requests')
async def secure_list_tenant_maintenance_requests(request: Request):
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        return {"success": True, "requests": []}

    tenant_id = str(tenant["_id"])
    ids = [tenant_id]
    if ObjectId.is_valid(tenant_id):
        ids.append(ObjectId(tenant_id))

    cursor = get_db().maintenance_requests.find(
        {"tenant_id": {"$in": ids}}
    ).sort("created_at", -1).limit(50)
    items = []
    async for row in cursor:
        items.append({
            "id": str(row["_id"]),
            "title": row.get("title", ""),
            "description": row.get("description", ""),
            "category": row.get("category", ""),
            "priority": row.get("priority", ""),
            "status": "pending" if row.get("status") == "open" else row.get("status", ""),
            "property_id": row.get("property_id", ""),
            "unit_id": row.get("unit_id"),
            "property_address": row.get("property_address", ""),
            "created_at": row.get("created_at", "").isoformat() if row.get("created_at") else "",
            "updated_at": row.get("updated_at", "").isoformat() if row.get("updated_at") else "",
        })
    return {"success": True, "requests": items}
