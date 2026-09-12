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
from pymongo import ReturnDocument

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
from rental.security_email import send_maintenance_received_email

router = APIRouter()
logger = logging.getLogger(__name__)

_ALLOWED_CATEGORIES = {
    "plumbing", "electrical", "hvac", "appliance", "general",
    "structural", "pest", "other",
}
_ALLOWED_PRIORITIES = {"low", "normal", "medium", "high", "urgent"}
_MAX_PHOTOS = 5
_MAX_PHOTO_CHARS = 1_500_000


def _normalize_locale(value) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    return "en" if raw == "en" or raw.startswith("en-") else "es"


async def _next_maintenance_number(db) -> tuple[int, str]:
    """Allocate a monotonic public number without count-then-insert races."""
    counter = await db.counters.find_one_and_update(
        {"_id": "maintenance_requests"},
        {
            "$inc": {"sequence": 1},
            "$setOnInsert": {"created_at": datetime.utcnow()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    sequence = int((counter or {}).get("sequence") or 0)
    if sequence < 1:
        raise RuntimeError("maintenance_number_allocation_failed")
    return sequence, f"{sequence:04d}"


def _iso(value) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def _public_maintenance(row: dict, *, include_detail: bool = False) -> dict:
    status = "pending" if row.get("status") == "open" else row.get("status", "")
    item = {
        "id": str(row["_id"]),
        "request_number": row.get("request_number", ""),
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "category": row.get("category", ""),
        "priority": row.get("priority", ""),
        "status": status,
        "property_id": row.get("property_id", ""),
        "unit_id": row.get("unit_id"),
        "property_address": row.get("property_address", ""),
        "assigned_to": row.get("assigned_to", ""),
        "assigned_provider_name": row.get("assigned_provider_name", ""),
        "assigned_provider_phone": row.get("assigned_provider_phone", ""),
        "scheduled_start": _iso(row.get("scheduled_start")),
        "scheduled_end": _iso(row.get("scheduled_end")),
        "tenant_presence_required": bool(row.get("tenant_presence_required", False)),
        "tenant_visible_note": row.get("tenant_visible_note", ""),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "completed_at": _iso(row.get("completed_at")),
    }
    if include_detail:
        item["photos"] = row.get("photos", []) or []
        item["photo_count"] = len(item["photos"])
        item["timeline"] = [
            {
                "status": event.get("status", ""),
                "at": _iso(event.get("at")),
                "note": event.get("note", ""),
            }
            for event in (row.get("timeline", []) or [])
            if isinstance(event, dict)
        ]
    return item


async def _canonical_lease_location(contract: dict) -> dict:
    """Resolve and validate the exact property/unit represented by an active lease."""
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
        # An active lease must already own its unit projection.  Allowing an
        # empty claim here would let maintenance proceed from an internally
        # inconsistent active contract after a partial lifecycle failure.
        if str(unit.get("current_contract_id") or "") != str(contract["_id"]):
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
    locale = _normalize_locale(
        data.get("locale")
        or tenant.get("preferred_language")
        or tenant.get("language")
    )

    now = datetime.utcnow()
    tenant_id = str(tenant["_id"])
    contract_id = str(contract["_id"])
    db = get_db()
    sequence, request_number = await _next_maintenance_number(db)
    maintenance = {
        "request_sequence": sequence,
        "request_number": request_number,
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
        "notification_language": locale,
        "timeline": [{"status": "pending", "at": now, "note": ""}],
        "relationship_source": "active_contract",
        "created_at": now,
        "updated_at": now,
    }
    result = await db.maintenance_requests.insert_one(maintenance)
    request_id = str(result.inserted_id)

    # Delivery is advisory: the ticket remains authoritative even when email is unavailable.
    tenant_email = str(tenant.get("email") or "").strip()
    email_confirmation_sent = False
    try:
        email_confirmation_sent = await send_maintenance_received_email(
            db,
            to_email=tenant_email,
            name=str(tenant.get("name") or tenant.get("full_name") or ""),
            request_id=request_number,
            title=title,
            property_address=location["property_address"],
            category=category,
            priority=priority,
            photo_count=len(photos),
            submitted_at=now,
            locale=locale,
        )
        receipt_status = (
            "sent" if email_confirmation_sent
            else ("failed" if tenant_email else "skipped_no_email")
        )
        receipt_record = {
            "status": receipt_status,
            "attempted_at": now,
        }
        if email_confirmation_sent:
            receipt_record["sent_at"] = now
        await db.maintenance_requests.update_one(
            {"_id": result.inserted_id},
            {"$set": {"tenant_receipt_email": receipt_record}},
        )
    except Exception as exc:
        logger.warning("maintenance receipt tracking failed: %s", exc)

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

    return {
        "success": True,
        "message": "Solicitud de mantenimiento creada",
        "request_id": request_id,
        "request_number": request_number,
        "email_confirmation_sent": email_confirmation_sent,
        "photo_count": len(photos),
    }


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
        items.append(_public_maintenance(row))
    return {"success": True, "requests": items}


@router.get('/tenant/maintenance-requests/{request_id}')
async def secure_get_tenant_maintenance_request(request_id: str, request: Request):
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        raise HTTPException(status_code=403, detail="maintenance_tenant_not_linked")
    if not ObjectId.is_valid(request_id):
        raise HTTPException(status_code=400, detail="maintenance_request_id_invalid")

    tenant_id = str(tenant["_id"])
    tenant_ids = [tenant_id]
    if ObjectId.is_valid(tenant_id):
        tenant_ids.append(ObjectId(tenant_id))
    row = await get_db().maintenance_requests.find_one({
        "_id": ObjectId(request_id),
        "tenant_id": {"$in": tenant_ids},
    })
    if not row:
        raise HTTPException(status_code=404, detail="maintenance_request_not_found")
    return {"success": True, "request": _public_maintenance(row, include_detail=True)}
