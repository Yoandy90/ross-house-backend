"""Canonical Section 8 declaration identity binding.

A declaration may be made before a formal tenant record exists, so the
authenticated app user remains a valid self-owned storage target.  If a tenant
record exists, it must resolve uniquely and is updated by exact _id only; email
or phone matching is never used as write authority.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_marketplace, get_db, send_rental_push_to_admins
from rental.tenant_integrity import resolve_authenticated_tenant

router = APIRouter()


def _user_filter(user: dict) -> dict:
    user_id = str(user.get("_id") or user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="section8_identity_missing")
    return {"_id": ObjectId(user_id)} if ObjectId.is_valid(user_id) else {"_id": user_id}


def _bounded_text(value, *, max_chars: int, detail: str) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        raise HTTPException(status_code=400, detail=detail)
    return text


@router.post('/tenant/section8/declare')
async def secure_tenant_declare_section8(request: Request):
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="section8_payload_invalid")

    try:
        bedrooms = int(data.get("voucher_bedrooms") or 0)
        amount = float(data.get("voucher_amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="section8_numeric_fields_invalid")
    if bedrooms < 0 or bedrooms > 20:
        raise HTTPException(status_code=400, detail="section8_bedrooms_invalid")
    if amount < 0 or amount > 100000:
        raise HTTPException(status_code=400, detail="section8_amount_invalid")

    now = datetime.utcnow()
    section8_data = {
        "is_section8": bool(data.get("has_voucher", False)),
        "section8_voucher": _bounded_text(data.get("voucher_number"), max_chars=120, detail="section8_voucher_number_too_long"),
        "section8_pha": _bounded_text(data.get("pha"), max_chars=200, detail="section8_pha_too_long"),
        "section8_voucher_bedrooms": bedrooms,
        "section8_voucher_amount": amount,
        "section8_voucher_expiration": _bounded_text(data.get("voucher_expiration"), max_chars=32, detail="section8_expiration_invalid") or None,
        "section8_notes": _bounded_text(data.get("notes"), max_chars=4000, detail="section8_notes_too_long"),
        "section8_declared_at": now,
    }

    photo = data.get("photo_base64") or ""
    if photo:
        if not isinstance(photo, str) or len(photo) > 5_000_000 or not photo.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="section8_document_invalid")
        section8_data["section8_voucher_photo"] = photo

    db = get_db()
    user_filter = _user_filter(user)
    app_result = await db.app_users.update_one(user_filter, {"$set": section8_data})

    tenant_id = None
    if tenant:
        tenant_id = str(tenant["_id"])
        tenant_result = await db.tenants.update_one({"_id": tenant["_id"]}, {"$set": section8_data})
        if tenant_result.matched_count != 1:
            raise HTTPException(status_code=409, detail="section8_tenant_concurrent_missing")
    elif app_result.matched_count != 1:
        # An authenticated legacy/self record that cannot be persisted is an
        # identity integrity error, never a reason to fall back to email writes.
        raise HTTPException(status_code=409, detail="section8_app_user_missing")

    try:
        await send_rental_push_to_admins(
            title="Nueva declaración Section 8",
            body="Se recibió una declaración de vivienda para revisión.",
            data={"type": "section8_declaration", "tenant_id": tenant_id or ""},
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": "Información de Section 8 guardada. El admin la revisará.",
        "data": {
            "is_section8": section8_data["is_section8"],
            "voucher_number": section8_data["section8_voucher"],
            "pha": section8_data["section8_pha"],
            "voucher_amount": section8_data["section8_voucher_amount"],
        },
    }


@router.get('/tenant/section8/status')
async def secure_tenant_section8_status(request: Request):
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    source = tenant or user
    return {
        "success": True,
        "is_section8": bool(source.get("is_section8", False)),
        "voucher_number": source.get("section8_voucher", ""),
        "pha": source.get("section8_pha", ""),
        "voucher_bedrooms": source.get("section8_voucher_bedrooms", 0),
        "voucher_amount": source.get("section8_voucher_amount", 0),
        "voucher_expiration": source.get("section8_voucher_expiration", ""),
        "notes": source.get("section8_notes", ""),
        "declared_at": (
            source.get("section8_declared_at").isoformat()
            if hasattr(source.get("section8_declared_at"), "isoformat")
            else (source.get("section8_declared_at") or None)
        ),
    }
