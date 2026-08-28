"""Canonical tenant identity helpers.

Fail closed when an authenticated marketplace account maps ambiguously to more
than one tenant record.  Direct id / app_user_id links are authoritative;
email and phone are compatibility fallbacks only when unique.
"""
from bson import ObjectId
from fastapi import HTTPException

from rental.shared import get_db


def _norm_email(value) -> str:
    return str(value or "").strip().lower()


def _norm_phone(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


async def _unique_tenant(query: dict, *, ambiguity_detail: str):
    matches = await get_db().tenants.find(query).limit(2).to_list(2)
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
        # Avoid regex identity matching.  Read the small candidate set and
        # compare normalized values in application code for legacy casing.
        candidates = await db.tenants.find(
            {"email": {"$exists": True, "$ne": ""}}
        ).to_list(1000)
        matches = [t for t in candidates if _norm_email(t.get("email")) == email]
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="tenant_identity_ambiguous_email")
        if matches:
            return matches[0]

    phone = _norm_phone(user.get("phone"))
    if phone:
        candidates = await db.tenants.find(
            {"phone": {"$exists": True, "$ne": ""}}
        ).to_list(1000)
        matches = [t for t in candidates if _norm_phone(t.get("phone")) == phone]
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="tenant_identity_ambiguous_phone")
        if matches:
            return matches[0]

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
