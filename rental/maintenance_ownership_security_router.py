"""Fail-closed ownership guards for maintenance entry points.

Tenant-created maintenance must derive tenant/contract/property/unit from the
canonical authenticated tenant and its exact active lease.  Client-supplied
location or ownership fields are never authoritative.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_marketplace, get_db
from rental.tenant_integrity import find_active_contract_for_tenant, resolve_authenticated_tenant
from rental.maintenance_security_router import _canonical_lease_location

router = APIRouter()

_ALLOWED_PRIORITIES = {"low", "medium", "high", "urgent"}
_ALLOWED_CONTACT = {"phone", "email", "whatsapp"}


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
        "source": "tenant_directory",
        "relationship_source": "active_contract",
        "preferred_provider_id": preferred_provider_id,
        "preferred_provider_name": preferred_provider_name,
        "contact_preference": contact,
        "created_at": now,
        "updated_at": now,
    }
    result = await get_db().maintenance_requests.insert_one(record)
    return {"success": True, "request_id": str(result.inserted_id)}
