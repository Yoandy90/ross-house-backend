"""Canonical tenant identity helpers.

Fail closed when an authenticated marketplace account maps ambiguously to more
than one tenant record. Direct id / app_user_id links are authoritative;
normalized email and phone fields are indexed compatibility lookups. Legacy
raw fields remain a bounded fallback while historical records are migrated by
normal application writes.
"""
from bson import ObjectId
from fastapi import HTTPException

from rental.shared import get_db


def _norm_email(value) -> str:
    return str(value or "").strip().lower()


def _norm_phone(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


async def ensure_tenant_identity_indexes() -> None:
    """Create non-unique lookup indexes without changing identity semantics.

    These indexes intentionally remain non-unique because legacy data may
    already contain duplicates. Runtime resolution still detects duplicates and
    fails closed instead of trusting whichever record MongoDB returns first.
    """
    tenants = get_db().tenants
    await tenants.create_index("email_normalized", sparse=True, name="tenant_email_normalized_lookup")
    await tenants.create_index("phone_normalized", sparse=True, name="tenant_phone_normalized_lookup")
    await tenants.create_index("app_user_id", sparse=True, name="tenant_app_user_lookup")


async def _unique_tenant(query: dict, *, ambiguity_detail: str):
    matches = await get_db().tenants.find(query).limit(2).to_list(2)
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=ambiguity_detail)
    return matches[0] if matches else None


async def find_unique_tenant_by_email(email_value, *, ambiguity_detail: str):
    """Resolve a unique tenant by normalized email, with bounded legacy fallback."""
    email = _norm_email(email_value)
    if not email:
        return None

    indexed = await _unique_tenant(
        {"email_normalized": email},
        ambiguity_detail=ambiguity_detail,
    )
    if indexed:
        return indexed

    candidates = await get_db().tenants.find(
        {"email": {"$exists": True, "$ne": ""}}
    ).limit(1001).to_list(1001)
    if len(candidates) > 1000:
        raise HTTPException(status_code=409, detail="tenant_identity_legacy_email_unbounded")
    matches = [t for t in candidates if _norm_email(t.get("email")) == email]
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=ambiguity_detail)
    return matches[0] if matches else None


async def find_unique_tenant_by_phone(phone_value, *, ambiguity_detail: str):
    """Resolve a unique tenant by normalized phone, with bounded legacy fallback."""
    phone = _norm_phone(phone_value)
    if not phone:
        return None

    indexed = await _unique_tenant(
        {"phone_normalized": phone},
        ambiguity_detail=ambiguity_detail,
    )
    if indexed:
        return indexed

    candidates = await get_db().tenants.find(
        {"phone": {"$exists": True, "$ne": ""}}
    ).limit(1001).to_list(1001)
    if len(candidates) > 1000:
        raise HTTPException(status_code=409, detail="tenant_identity_legacy_phone_unbounded")
    matches = [t for t in candidates if _norm_phone(t.get("phone")) == phone]
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=ambiguity_detail)
    return matches[0] if matches else None


async def resolve_authenticated_tenant(user: dict):
    """Resolve one tenant record for an already-authenticated marketplace user."""
    db = get_db()
    user_id = str(user.get("_id") or user.get("id") or "").strip()

    if user.get("role") == "tenant" and ObjectId.is_valid(user_id):
        direct = await db.tenants.find_one({"_id": ObjectId(user_id)})
        if direct:
            return direct

    if user_id:
        linked = await _unique_tenant(
            {"app_user_id": user_id},
            ambiguity_detail="tenant_identity_ambiguous_app_user",
        )
        if linked:
            return linked

    email = _norm_email(user.get("email"))
    if email:
        matched_email = await find_unique_tenant_by_email(
            email,
            ambiguity_detail="tenant_identity_ambiguous_email",
        )
        if matched_email:
            return matched_email

    phone = _norm_phone(user.get("phone"))
    if phone:
        matched_phone = await find_unique_tenant_by_phone(
            phone,
            ambiguity_detail="tenant_identity_ambiguous_phone",
        )
        if matched_phone:
            return matched_phone

    return None


async def find_active_contract_for_tenant(tenant: dict):
    """Return the tenant's single active contract; ambiguity is an integrity error."""
    tenant_id = str(tenant["_id"])
    ids = [tenant_id]
    if ObjectId.is_valid(tenant_id):
        ids.append(ObjectId(tenant_id))
    matches = await get_db().rental_contracts.find({
        "tenant_id": {"$in": ids},
        "status": "active",
    }).limit(2).to_list(2)
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="tenant_multiple_active_contracts")
    return matches[0] if matches else None
