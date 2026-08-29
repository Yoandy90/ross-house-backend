"""Security boundary for the legacy ``POST /lease/{lease_id}/sign`` route.

Authorization is derived from the authenticated actor and bound to lease party
identifiers. Signatures never activate occupancy directly: once all required
parties sign, the lease becomes ``pending_activation`` and the guarded lifecycle
endpoint performs the authoritative occupancy transition.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, auth_marketplace, get_db
from rental.tenant_integrity import resolve_authenticated_tenant

router = APIRouter(tags=["lease-signature-security"])
_ALLOWED_ROLES = {"tenant", "landlord", "admin"}
_TENANT_ROLES = {"tenant", "client"}


def _norm(value) -> str:
    return str(value or "").strip()


def _email(value) -> str:
    return _norm(value).lower()


def _actor_name(actor: dict, role: str) -> str:
    name = _norm(actor.get("name") or actor.get("full_name"))
    if name:
        return name
    first = _norm(actor.get("first_name"))
    last = _norm(actor.get("last_name"))
    joined = f"{first} {last}".strip()
    return joined or _email(actor.get("email")) or role


async def _tenant_ids(actor: dict) -> set[str]:
    """Return only actor-owned canonical tenant identifiers.

    Historical code selected the first regex email match. The shared canonical
    resolver instead honors direct/app-user linkage and fails closed on legacy
    email/phone ambiguity.
    """
    ids = {_norm(actor.get("_id"))}
    ids.discard("")
    tenant = await resolve_authenticated_tenant(actor)
    if tenant:
        tenant_id = _norm(tenant.get("_id"))
        if tenant_id:
            ids.add(tenant_id)
    return ids


async def _authorize_actor(request: Request, lease: dict, signer_role: str) -> dict:
    if signer_role == "admin":
        return await auth_admin(request)
    actor = await auth_marketplace(request)
    actor_role = _norm(actor.get("role") or "tenant").lower()
    if signer_role == "tenant":
        if actor_role not in _TENANT_ROLES:
            raise HTTPException(status_code=403, detail="lease_signer_role_mismatch")
        lease_tenant_id = _norm(lease.get("tenant_id"))
        if lease_tenant_id:
            if lease_tenant_id not in await _tenant_ids(actor):
                raise HTTPException(status_code=403, detail="lease_tenant_mismatch")
        else:
            # Existing legacy leases without a tenant_id remain signable only if
            # the actor resolves to one unique canonical tenant whose email is
            # the same as the historical lease snapshot.
            tenant = await resolve_authenticated_tenant(actor)
            actor_email = _email((tenant or actor).get("email"))
            lease_email = _email(lease.get("tenant_email"))
            if not tenant or not actor_email or not lease_email or actor_email != lease_email:
                raise HTTPException(status_code=403, detail="lease_tenant_mismatch")
        return actor
    if signer_role == "landlord":
        if actor_role != "landlord":
            raise HTTPException(status_code=403, detail="lease_signer_role_mismatch")
        landlord_id = _norm(lease.get("landlord_id"))
        if not landlord_id or landlord_id != _norm(actor.get("_id")):
            raise HTTPException(status_code=403, detail="lease_landlord_mismatch")
        return actor
    raise HTTPException(status_code=400, detail="Rol de firmante inválido")


@router.post("/lease/{lease_id}/sign")
async def secure_legacy_lease_sign(lease_id: str, request: Request):
    data = await request.json()
    signature = data.get("signature", "")
    signer_role = _norm(data.get("role")).lower()
    if not isinstance(signature, str) or not signature.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Firma digital requerida")
    if signer_role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Rol de firmante inválido")
    try:
        object_id = ObjectId(lease_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID de contrato inválido")
    db = get_db()
    lease = await db.rental_contracts.find_one({"_id": object_id})
    if not lease:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")
    actor = await _authorize_actor(request, lease, signer_role)
    now = datetime.utcnow()
    update = {"updated_at": now}
    expected_status = lease.get("status")

    if signer_role == "tenant":
        if expected_status not in ["pending_tenant", "pending_signatures"]:
            raise HTTPException(status_code=400, detail="Este contrato no está pendiente de firma del inquilino")
        update.update({"tenant_signature": signature, "tenant_signed_at": now,
                       "tenant_signer_name": _actor_name(actor, signer_role)})
        if lease.get("landlord_id") and not lease.get("landlord_signature"):
            update["status"] = "pending_landlord"
        elif not lease.get("admin_signature") and not lease.get("landlord_signature"):
            update["status"] = "pending_signatures"
        else:
            update["status"] = "pending_activation"
    elif signer_role == "landlord":
        if expected_status not in ["pending_landlord", "pending_signatures"]:
            raise HTTPException(status_code=400, detail="Este contrato no está pendiente de firma del propietario")
        update.update({"landlord_signature": signature, "landlord_signed_at": now,
                       "landlord_signer_name": _actor_name(actor, signer_role)})
        update["status"] = "pending_activation" if lease.get("tenant_signature") else "pending_tenant"
    else:
        update.update({"admin_signature": signature, "admin_signed_at": now,
                       "admin_signer_name": _actor_name(actor, signer_role)})
        if lease.get("tenant_signature") and (lease.get("landlord_signature") or not lease.get("landlord_id")):
            update["status"] = "pending_activation"

    write_filter = {"_id": object_id, "status": expected_status}
    result = await db.rental_contracts.update_one(write_filter, {"$set": update})
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="lease_signature_state_changed")
    final_status = update.get("status", expected_status)
    return {"success": True, "new_status": final_status,
            "message": f"Firma de {signer_role} guardada exitosamente"}
