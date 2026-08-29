"""Fail-closed compatibility guard for the historical tenant login route.

Legacy login historically accepted only the last four phone digits and selected
the first case-insensitive email match. This precedence route requires a full
normalized phone match and refuses ambiguous tenant identities.
"""
from fastapi import APIRouter, HTTPException, Request

from rental.shared import create_tenant_token
from rental.tenant_integrity import (
    _norm_email,
    _norm_phone,
    find_unique_tenant_by_email,
)

router = APIRouter()


@router.post('/tenant/login')
async def secure_tenant_login(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="tenant_login_payload_invalid")

    email = _norm_email(data.get("email"))
    phone = _norm_phone(data.get("phone"))
    if not email or not phone:
        raise HTTPException(status_code=400, detail="Email y teléfono son requeridos")
    # A full NANP number is the minimum compatibility credential. The historical
    # last-four fallback is intentionally not accepted.
    if len(phone) < 10 or len(phone) > 15:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    tenant = await find_unique_tenant_by_email(
        email,
        ambiguity_detail="tenant_login_identity_ambiguous",
    )
    if not tenant:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if _norm_phone(tenant.get("phone_normalized") or tenant.get("phone")) != phone:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    tenant_id = str(tenant.get("_id") or "")
    if not tenant_id:
        raise HTTPException(status_code=409, detail="tenant_login_identity_invalid")

    token = create_tenant_token(tenant_id, email)
    return {
        "success": True,
        "token": token,
        "tenant": {
            "id": tenant_id,
            "name": tenant.get("name", ""),
            "email": tenant.get("email", ""),
            "tenant_number": tenant.get("tenant_number", ""),
        },
    }
