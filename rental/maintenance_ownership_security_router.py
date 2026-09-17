"""Fail-closed ownership guards for maintenance entry points.

Tenant-created maintenance must derive tenant/contract/property/unit from the
canonical authenticated tenant and its exact active lease.  Admin workflow
mutations may change workflow fields, but never the ticket's lease ownership.
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, auth_marketplace, get_db, send_rental_push_to_user
from rental.tenant_integrity import find_active_contract_for_tenant, resolve_authenticated_tenant
from rental.maintenance_security_router import _canonical_lease_location, _next_maintenance_number, _normalize_locale
from rental.security_email import send_maintenance_updated_email
from rental.maintenance_workflow import (
    ALLOWED_STATUSES as _ALLOWED_STATUSES, STATUS_TRANSITIONS as _STATUS_TRANSITIONS,
    canonical_status as _canonical_status,
)

router = APIRouter()

_ALLOWED_PRIORITIES = {"low", "medium", "high", "urgent"}
_ALLOWED_CONTACT = {"phone", "email", "whatsapp"}
_IMMUTABLE_OWNERSHIP_FIELDS = {
    "tenant_id", "contract_id", "property_id", "unit_id", "relationship_source",
    "tenant_name", "tenant_email", "tenant_phone", "property_address",
}


def _parse_optional_datetime(value, field: str):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"maintenance_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"maintenance_{field}_invalid") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def _active_maintenance_context(request: Request) -> tuple[dict, dict, dict]:
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        raise HTTPException(status_code=403, detail="maintenance_tenant_not_linked")
    contract = await find_active_contract_for_tenant(tenant)
    if not contract:
        raise HTTPException(status_code=403, detail="maintenance_active_lease_required")
    if str(contract.get("tenant_id") or "") != str(tenant.get("_id") or ""):
        raise HTTPException(status_code=409, detail="maintenance_contract_tenant_mismatch")
    location = await _canonical_lease_location(contract)
    return tenant, contract, location


async def _load_bound_maintenance_request(request_id: str) -> dict:
    """Load a ticket and verify its immutable tenant/lease/property/unit binding."""
    if not ObjectId.is_valid(request_id):
        raise HTTPException(status_code=400, detail="maintenance_request_id_invalid")

    db = get_db()
    ticket = await db.maintenance_requests.find_one({"_id": ObjectId(request_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="maintenance_request_not_found")

    tenant_id = str(ticket.get("tenant_id") or "")
    contract_id = str(ticket.get("contract_id") or "")
    property_id = str(ticket.get("property_id") or "")
    unit_id = str(ticket.get("unit_id") or "")
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=409, detail="maintenance_request_tenant_invalid")
    if not ObjectId.is_valid(contract_id):
        raise HTTPException(status_code=409, detail="maintenance_request_contract_invalid")
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=409, detail="maintenance_request_property_invalid")

    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
    contract = await db.rental_contracts.find_one({"_id": ObjectId(contract_id)})
    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if not tenant:
        raise HTTPException(status_code=409, detail="maintenance_request_tenant_missing")
    if not contract:
        raise HTTPException(status_code=409, detail="maintenance_request_contract_missing")
    if not prop:
        raise HTTPException(status_code=409, detail="maintenance_request_property_missing")

    if str(contract.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=409, detail="maintenance_request_contract_tenant_mismatch")
    if str(contract.get("property_id") or "") != property_id:
        raise HTTPException(status_code=409, detail="maintenance_request_contract_property_mismatch")

    contract_unit_id = str(contract.get("unit_id") or "")
    if unit_id != contract_unit_id:
        raise HTTPException(status_code=409, detail="maintenance_request_contract_unit_mismatch")
    if unit_id:
        if not ObjectId.is_valid(unit_id):
            raise HTTPException(status_code=409, detail="maintenance_request_unit_invalid")
        unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
        if not unit:
            raise HTTPException(status_code=409, detail="maintenance_request_unit_missing")
        if str(unit.get("property_id") or "") != property_id:
            raise HTTPException(status_code=409, detail="maintenance_request_unit_property_mismatch")

    return ticket


@router.post('/tenant/service-providers/request-help')
async def secure_tenant_request_help(request: Request):
    """Secure compatibility replacement for the historical provider-help route."""
    tenant, contract, location = await _active_maintenance_context(request)
    data = await request.json()

    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    if len(title) < 4 or len(title) > 140:
        raise HTTPException(status_code=400, detail="maintenance_title_invalid")
    if len(description) < 10 or len(description) > 2000:
        raise HTTPException(status_code=400, detail="maintenance_description_invalid")

    priority = str(data.get("priority") or "medium").strip().lower()
    if priority not in _ALLOWED_PRIORITIES:
        raise HTTPException(status_code=400, detail="maintenance_priority_invalid")
    contact = str(data.get("contact_preference") or "phone").strip().lower()
    if contact not in _ALLOWED_CONTACT:
        raise HTTPException(status_code=400, detail="maintenance_contact_preference_invalid")

    preferred_provider_id = str(data.get("provider_id") or "").strip() or None
    preferred_provider_name = None
    if preferred_provider_id:
        db = get_db()
        provider_query = {"_id": ObjectId(preferred_provider_id)} if ObjectId.is_valid(preferred_provider_id) else {"_id": preferred_provider_id}
        provider = await db.service_providers.find_one(provider_query)
        if not provider or str(provider.get("status") or "") != "active":
            raise HTTPException(status_code=400, detail="maintenance_provider_invalid")
        preferred_provider_name = provider.get("name") or provider.get("company_name") or ""

    now = datetime.utcnow()
    locale = _normalize_locale(
        data.get("locale") or tenant.get("preferred_language") or tenant.get("language") or "es"
    )
    request_sequence, request_number = await _next_maintenance_number(get_db())
    record = {
        "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name") or tenant.get("full_name") or "",
        "tenant_phone": tenant.get("phone") or "",
        "tenant_email": tenant.get("email") or "",
        "contract_id": str(contract["_id"]),
        "property_id": location["property_id"],
        "unit_id": location["unit_id"],
        "property_address": location["property_address"],
        "title": title,
        "description": description,
        "priority": priority,
        "category": str(data.get("service") or "general").strip().lower()[:80],
        "status": "pending",
        "request_sequence": request_sequence,
        "request_number": request_number,
        "notification_language": locale,
        "source": "tenant_directory",
        "relationship_source": "active_contract",
        "preferred_provider_id": preferred_provider_id,
        "preferred_provider_name": preferred_provider_name,
        "contact_preference": contact,
        "created_at": now,
        "updated_at": now,
        "timeline": [{"status": "pending", "at": now, "note": ""}],
    }
    result = await get_db().maintenance_requests.insert_one(record)
    return {"success": True, "request_id": str(result.inserted_id), "request_number": request_number}


@router.put('/admin/maintenance-requests/{request_id}')
async def secure_update_maintenance_request(request_id: str, request: Request):
    """Admin workflow mutation with immutable ownership and status CAS."""
    await auth_admin(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="maintenance_update_invalid")

    attempted_ownership = sorted(_IMMUTABLE_OWNERSHIP_FIELDS.intersection(data.keys()))
    if attempted_ownership:
        raise HTTPException(status_code=400, detail="maintenance_ownership_immutable")

    ticket = await _load_bound_maintenance_request(request_id)
    old_raw_status = str(ticket.get("status") or "pending").strip().lower()
    old_status = _canonical_status(old_raw_status)
    if old_status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=409, detail="maintenance_status_unknown")

    now = datetime.utcnow()
    update_fields = {"updated_at": now}
    requested_status = None
    if "status" in data:
        requested_status = _canonical_status(data.get("status"))
        if requested_status not in _ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="maintenance_status_invalid")
        if requested_status != old_status and requested_status not in _STATUS_TRANSITIONS[old_status]:
            raise HTTPException(status_code=409, detail="maintenance_status_transition_invalid")
        update_fields["status"] = requested_status
        if requested_status in {"completed", "resolved"}:
            update_fields["completed_at"] = now
        elif requested_status in {"pending", "reviewing", "assigned", "scheduled", "in_progress", "waiting_parts"}:
            update_fields["completed_at"] = None

    if "admin_notes" in data:
        notes = str(data.get("admin_notes") or "")
        if len(notes) > 8000:
            raise HTTPException(status_code=400, detail="maintenance_admin_notes_too_long")
        update_fields["admin_notes"] = notes

    if "assigned_to" in data:
        assigned_to = str(data.get("assigned_to") or "").strip()
        if len(assigned_to) > 160:
            raise HTTPException(status_code=400, detail="maintenance_assignment_invalid")
        update_fields["assigned_to"] = assigned_to

    if "scheduled_start" in data:
        update_fields["scheduled_start"] = _parse_optional_datetime(data.get("scheduled_start"), "scheduled_start")
    if "scheduled_end" in data:
        update_fields["scheduled_end"] = _parse_optional_datetime(data.get("scheduled_end"), "scheduled_end")
    start = update_fields.get("scheduled_start", ticket.get("scheduled_start"))
    end = update_fields.get("scheduled_end", ticket.get("scheduled_end"))
    if start and end and end <= start:
        raise HTTPException(status_code=400, detail="maintenance_schedule_window_invalid")

    if "tenant_presence_required" in data:
        if not isinstance(data.get("tenant_presence_required"), bool):
            raise HTTPException(status_code=400, detail="maintenance_tenant_presence_invalid")
        update_fields["tenant_presence_required"] = data["tenant_presence_required"]

    if "tenant_visible_note" in data:
        tenant_note = str(data.get("tenant_visible_note") or "").strip()
        if len(tenant_note) > 2000:
            raise HTTPException(status_code=400, detail="maintenance_tenant_note_too_long")
        update_fields["tenant_visible_note"] = tenant_note

    if len(update_fields) == 1:
        raise HTTPException(status_code=400, detail="maintenance_no_mutable_fields")

    meaningful_fields = {
        "status", "assigned_to", "scheduled_start", "scheduled_end",
        "tenant_presence_required", "tenant_visible_note",
    }
    changed_meaningful = any(
        field in update_fields and update_fields[field] != ticket.get(field)
        for field in meaningful_fields
    )
    update_doc = {"$set": update_fields}
    if changed_meaningful:
        event_status = requested_status or old_status
        update_doc["$push"] = {"timeline": {
            "status": event_status,
            "at": now,
            "note": update_fields.get("tenant_visible_note", ""),
        }}

    db = get_db()
    result = await db.maintenance_requests.update_one(
        {"_id": ticket["_id"], "status": ticket.get("status")},
        update_doc,
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="maintenance_concurrent_update")

    # Notifications are advisory; the authoritative workflow mutation is the CAS above.
    if changed_meaningful:
        locale = "en" if str(ticket.get("notification_language") or "es").startswith("en") else "es"
        resulting_status = requested_status or old_status
        assigned_to = update_fields.get("assigned_to", ticket.get("assigned_to") or ticket.get("assigned_provider_name") or "")
        scheduled_start = update_fields.get("scheduled_start", ticket.get("scheduled_start"))
        scheduled_end = update_fields.get("scheduled_end", ticket.get("scheduled_end"))
        visible_note = update_fields.get("tenant_visible_note", ticket.get("tenant_visible_note") or "")
        try:
            status_label = resulting_status.replace("_", " ")
            push_title = "📋 Maintenance Update" if locale == "en" else "📋 Actualización de Mantenimiento"
            push_body = (
                f"Request #{ticket.get('request_number') or request_id}: {status_label}"
                if locale == "en"
                else f"Solicitud #{ticket.get('request_number') or request_id}: {status_label}"
            )
            await send_rental_push_to_user(
                user_id=str(ticket.get("tenant_id") or ""),
                title=push_title,
                body=push_body,
                data={"type": "maintenance_update", "request_id": request_id, "status": resulting_status},
            )
            property_id = str(ticket.get("property_id") or "")
            prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
            if prop and prop.get("owner_id"):
                await send_rental_push_to_user(
                    user_id=str(prop["owner_id"]),
                    title="📋 Mantenimiento Actualizado",
                    body=f"'{ticket.get('title', '')}' → {resulting_status}",
                    data={"type": "maintenance_update", "request_id": request_id, "status": resulting_status},
                )
        except Exception:
            pass

        try:
            await send_maintenance_updated_email(
                db,
                to_email=ticket.get("tenant_email") or "",
                name=ticket.get("tenant_name") or "",
                request_number=ticket.get("request_number") or request_id,
                title=ticket.get("title") or "",
                status=resulting_status,
                changed_at=now,
                locale=locale,
                assigned_to=assigned_to,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                tenant_visible_note=visible_note,
            )
        except Exception:
            pass

    return {
        "success": True,
        "message": "Solicitud actualizada",
        "status": requested_status or old_status,
        "request_number": ticket.get("request_number") or "",
        "assigned_to": update_fields.get("assigned_to", ticket.get("assigned_to") or ""),
        "scheduled_start": update_fields.get("scheduled_start", ticket.get("scheduled_start")),
        "scheduled_end": update_fields.get("scheduled_end", ticket.get("scheduled_end")),
        "tenant_visible_note": update_fields.get("tenant_visible_note", ticket.get("tenant_visible_note") or ""),
    }
