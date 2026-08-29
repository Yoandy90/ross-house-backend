"""Guard tenant projection fields from becoming occupancy authority.

``current_property_id``, ``current_contract_id`` and ``current_unit_id`` are
projections maintained by the lease lifecycle. They must never be freely
writable from generic admin tenant create/update operations.

Tenant email/phone identity is normalized on every guarded write. Existing
legacy rows remain readable through bounded compatibility fallbacks in
``tenant_integrity`` while new and edited rows use indexed lookup fields.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.tenant_router import create_tenant as historical_create_tenant
from rental.tenant_integrity import (
    _norm_email,
    _norm_phone,
    find_unique_tenant_by_email,
    find_unique_tenant_by_phone,
)

router = APIRouter()

_ALLOWED_PROFILE_FIELDS = {
    "first_name", "last_name", "email", "phone", "address", "photo_url",
    "profile_photo_url", "ssn_last4", "id_type", "id_number",
    "emergency_contact", "emergency_phone", "employer", "monthly_income",
    "status", "notes",
}
_LIFECYCLE_PROJECTION_FIELDS = {"current_property_id", "current_contract_id", "current_unit_id"}


def _reject_projection_fields(data: dict) -> None:
    attempted_projection = _LIFECYCLE_PROJECTION_FIELDS.intersection(data)
    if attempted_projection:
        raise HTTPException(status_code=409, detail="tenant_occupancy_projection_lifecycle_managed")


async def _assert_identity_available(data: dict, *, current_tenant_id: str | None = None) -> None:
    email = _norm_email(data.get("email")) if "email" in data else ""
    phone = _norm_phone(data.get("phone")) if "phone" in data else ""

    if email:
        existing = await find_unique_tenant_by_email(
            email,
            ambiguity_detail="tenant_identity_ambiguous_email",
        )
        if existing and str(existing.get("_id") or "") != str(current_tenant_id or ""):
            raise HTTPException(status_code=409, detail="tenant_email_already_linked")

    if phone:
        existing = await find_unique_tenant_by_phone(
            phone,
            ambiguity_detail="tenant_identity_ambiguous_phone",
        )
        if existing and str(existing.get("_id") or "") != str(current_tenant_id or ""):
            raise HTTPException(status_code=409, detail="tenant_phone_already_linked")


@router.post('/admin/tenants')
async def secure_create_tenant(request: Request):
    """Compatibility guard around the historical tenant creation workflow.

    The historical handler still owns account creation and welcome-message
    compatibility. This first-match route removes occupancy authority and adds
    normalized identity fields around that established workflow.
    """
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="tenant_create_payload_invalid")
    _reject_projection_fields(data)
    await _assert_identity_available(data)

    result = await historical_create_tenant(request)
    tenant_id = str((result or {}).get("tenant_id") or "")
    if tenant_id and ObjectId.is_valid(tenant_id):
        normalized = {}
        email = _norm_email(data.get("email"))
        phone = _norm_phone(data.get("phone"))
        if email:
            normalized["email_normalized"] = email
        if phone:
            normalized["phone_normalized"] = phone
        if normalized:
            normalized["identity_normalized_at"] = datetime.utcnow()
            write = await get_db().tenants.update_one(
                {"_id": ObjectId(tenant_id)},
                {"$set": normalized},
            )
            if write.matched_count != 1:
                raise HTTPException(status_code=409, detail="tenant_identity_normalization_missing")
    return result


@router.put('/admin/tenants/{tenant_id}')
async def secure_update_tenant(tenant_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="tenant_id_invalid")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="tenant_update_payload_invalid")

    _reject_projection_fields(data)

    tenant_oid = ObjectId(tenant_id)
    tenant = await get_db().tenants.find_one({"_id": tenant_oid})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    unknown = set(data).difference(_ALLOWED_PROFILE_FIELDS)
    if unknown:
        raise HTTPException(status_code=400, detail="tenant_update_field_invalid")

    await _assert_identity_available(data, current_tenant_id=tenant_id)

    update_fields = {}
    for field in _ALLOWED_PROFILE_FIELDS:
        if field not in data:
            continue
        if field == "monthly_income":
            try:
                value = float(data[field] or 0)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="tenant_monthly_income_invalid")
            if value < 0:
                raise HTTPException(status_code=400, detail="tenant_monthly_income_invalid")
            update_fields[field] = value
        elif field == "email":
            value = _norm_email(data[field])
            update_fields["email"] = value
            update_fields["email_normalized"] = value
            update_fields["identity_normalized_at"] = datetime.utcnow()
        elif field == "phone":
            raw_phone = str(data[field] or "").strip()
            update_fields["phone"] = raw_phone
            update_fields["phone_normalized"] = _norm_phone(raw_phone)
            update_fields["identity_normalized_at"] = datetime.utcnow()
        else:
            update_fields[field] = data[field]

    if "first_name" in data or "last_name" in data:
        first = str(data.get("first_name", tenant.get("first_name", "")) or "").strip()
        last = str(data.get("last_name", tenant.get("last_name", "")) or "").strip()
        update_fields["name"] = f"{first} {last}".strip()

    update_fields["updated_at"] = datetime.utcnow()
    result = await get_db().tenants.update_one(
        {"_id": tenant_oid},
        {"$set": update_fields},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="tenant_update_concurrent_missing")
    return {"success": True, "message": "Inquilino actualizado"}
