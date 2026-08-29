"""Security shadows for legacy admin contract mutation surfaces.

The historical generic PUT accepted ``status`` directly, while legacy admin
signature endpoints could activate a lease and write occupancy projections.
These first-match routes preserve compatible admin editing/signing behavior but
keep lifecycle state and occupancy authority exclusively behind the guarded
PATCH lifecycle endpoint.
"""
from datetime import datetime
import hashlib

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db
from rental.contracts_router import update_contract as historical_update_contract

router = APIRouter()

_LIFECYCLE_FIELDS = {
    "status",
    "current_contract_id",
    "current_property_id",
    "current_unit_id",
    "current_tenant_id",
    "lifecycle_claim_id",
    "lifecycle_claim_target",
    "lifecycle_claimed_at",
}
_RELATIONSHIP_FIELDS = {"property_id", "tenant_id", "unit_id", "landlord_id"}
_SIGNABLE_STATES = {
    "draft",
    "pending",
    "pending_signature",
    "pending_tenant",
    "pending_landlord",
    "pending_signatures",
}
_MAX_SIGNATURE_CHARS = 5_000_000


def _contract_oid(contract_id: str) -> ObjectId:
    if not ObjectId.is_valid(contract_id):
        raise HTTPException(status_code=400, detail="lease_contract_invalid")
    return ObjectId(contract_id)


def _signature_text(value, detail: str) -> str:
    text = str(value or "")
    if not text or len(text) > _MAX_SIGNATURE_CHARS:
        raise HTTPException(status_code=400, detail=detail)
    return text


def _signature_record(*, signature_data: str, sig_type: str, signer_name: str,
                      signer_role: str, admin: dict, request: Request, method: str) -> dict:
    now = datetime.utcnow()
    return {
        "type": sig_type,
        "image_data": signature_data,
        "hash": hashlib.sha256(signature_data.encode("utf-8")).hexdigest(),
        "signed_at": now,
        "signed_by_admin": admin.get("email", "admin"),
        "signer_name": signer_name,
        "signer_role": signer_role,
        "client_ip": request.client.host if request.client else "unknown",
        "method": method,
    }


def _next_signature_status(contract: dict, *, tenant_signed: bool, admin_signed: bool) -> str:
    if tenant_signed and admin_signed:
        return "pending_activation"
    if tenant_signed:
        return "pending_signature"
    return "pending_tenant"


@router.put('/admin/rental-contracts/{contract_id}')
async def secure_update_contract(contract_id: str, request: Request):
    """Keep generic editing separate from lifecycle and occupancy authority."""
    await auth_admin(request)
    contract_oid = _contract_oid(contract_id)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="lease_update_payload_invalid")

    attempted_lifecycle = sorted(_LIFECYCLE_FIELDS.intersection(data))
    if attempted_lifecycle:
        raise HTTPException(status_code=409, detail="lease_status_lifecycle_managed")

    contract = await get_db().rental_contracts.find_one({"_id": contract_oid})
    if not contract:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")

    if str(contract.get("status") or "").strip().lower() != "draft":
        attempted_relationship = sorted(_RELATIONSHIP_FIELDS.intersection(data))
        if attempted_relationship:
            raise HTTPException(status_code=409, detail="lease_relationship_locked_after_draft")

    # Delegate the remaining legacy-compatible editable fields. The body is
    # unchanged except that lifecycle/forbidden relationship writes have already
    # been rejected by this first-match boundary.
    return await historical_update_contract(contract_id, request)


@router.post('/admin/rental-contracts/{contract_id}/sign')
async def secure_admin_contract_sign(contract_id: str, request: Request):
    """Compatibility admin signature: evidence only, never occupancy mutation."""
    admin = await auth_admin(request)
    contract_oid = _contract_oid(contract_id)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="lease_signature_payload_invalid")

    db = get_db()
    contract = await db.rental_contracts.find_one({"_id": contract_oid})
    if not contract:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")
    expected_status = str(contract.get("status") or "").strip().lower()
    if expected_status not in _SIGNABLE_STATES:
        raise HTTPException(status_code=409, detail="lease_signature_state_invalid")

    image_data = str(data.get("image_data") or "")
    biometric_data = str(data.get("biometric_data") or "")
    if image_data:
        image_data = _signature_text(image_data, "lease_signature_invalid")
        if not image_data.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="lease_signature_invalid")
        evidence = image_data
    elif biometric_data:
        evidence = _signature_text(biometric_data, "lease_signature_invalid")
    else:
        raise HTTPException(status_code=400, detail="lease_signature_invalid")

    now = datetime.utcnow()
    signer_name = str(admin.get("name") or admin.get("email") or "Administrator")
    record = _signature_record(
        signature_data=evidence,
        sig_type=str(data.get("type") or "canvas")[:32],
        signer_name=signer_name,
        signer_role="admin",
        admin=admin,
        request=request,
        method="legacy_admin",
    )
    tenant_signed = bool(contract.get("tenant_signature"))
    new_status = _next_signature_status(contract, tenant_signed=tenant_signed, admin_signed=True)
    update = {
        "signature": record,
        "signature_status": "signed",
        "admin_signature": evidence,
        "admin_signed_at": now,
        "admin_signer_name": signer_name,
        "updated_at": now,
        "status": new_status,
    }
    result = await db.rental_contracts.update_one(
        {"_id": contract_oid, "status": contract.get("status")},
        {"$set": update},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="lease_signature_state_changed")
    return {
        "success": True,
        "message": "Contrato firmado exitosamente",
        "hash": record["hash"],
        "new_status": new_status,
        "fully_signed": bool(tenant_signed),
    }


@router.post('/admin/rental-contracts/{contract_id}/office-sign')
async def secure_office_sign_contract(contract_id: str, request: Request):
    """In-office evidence capture with CAS; activation remains a separate PATCH."""
    admin = await auth_admin(request)
    contract_oid = _contract_oid(contract_id)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="lease_signature_payload_invalid")

    db = get_db()
    contract = await db.rental_contracts.find_one({"_id": contract_oid})
    if not contract:
        raise HTTPException(status_code=404, detail="lease_contract_not_found")
    expected_status = str(contract.get("status") or "").strip().lower()
    if expected_status not in _SIGNABLE_STATES:
        raise HTTPException(status_code=409, detail="lease_signature_state_invalid")

    signer_role = str(data.get("signer_role") or "tenant").strip().lower()
    if signer_role not in {"tenant", "admin"}:
        raise HTTPException(status_code=400, detail="lease_office_signer_role_invalid")

    sig_type = str(data.get("type") or "canvas")[:32]
    signature_data = str(data.get("signature") or "")
    use_saved_admin = bool(data.get("use_saved_admin"))
    if signer_role == "admin" and use_saved_admin and not signature_data:
        saved = await db.admin_signatures.find_one({"type": "landlord_default"})
        if not saved or not saved.get("image_data"):
            raise HTTPException(status_code=400, detail="lease_saved_admin_signature_missing")
        signature_data = str(saved["image_data"])
        sig_type = "saved"

    signature_data = _signature_text(signature_data, "lease_signature_invalid")
    if sig_type != "topaz" and not signature_data.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="lease_signature_invalid")

    now = datetime.utcnow()
    update = {"updated_at": now}
    tenant_signed = bool(contract.get("tenant_signature"))
    admin_signed = bool(contract.get("admin_signature") or contract.get("landlord_signature"))

    if signer_role == "tenant":
        signer_name = str(contract.get("tenant_name") or "Tenant")
        record = _signature_record(
            signature_data=signature_data,
            sig_type=sig_type,
            signer_name=signer_name,
            signer_role="tenant",
            admin=admin,
            request=request,
            method="office",
        )
        update.update({
            "tenant_signature": record,
            "tenant_signed_at": now,
            "tenant_signer_name": signer_name,
        })
        tenant_signed = True

        if bool(data.get("auto_admin")) and not admin_signed:
            saved = await db.admin_signatures.find_one({"type": "landlord_default"})
            if saved and saved.get("image_data"):
                saved_data = _signature_text(saved["image_data"], "lease_signature_invalid")
                admin_name = str(admin.get("name") or admin.get("email") or "Administrator")
                admin_record = _signature_record(
                    signature_data=saved_data,
                    sig_type="saved",
                    signer_name=admin_name,
                    signer_role="admin",
                    admin=admin,
                    request=request,
                    method="office_saved",
                )
                update.update({
                    "admin_signature": admin_record,
                    "admin_signed_at": now,
                    "admin_signer_name": admin_name,
                })
                admin_signed = True
    else:
        signer_name = str(admin.get("name") or admin.get("email") or "Administrator")
        record = _signature_record(
            signature_data=signature_data,
            sig_type=sig_type,
            signer_name=signer_name,
            signer_role="admin",
            admin=admin,
            request=request,
            method="office",
        )
        update.update({
            "admin_signature": record,
            "admin_signed_at": now,
            "admin_signer_name": signer_name,
        })
        admin_signed = True

    new_status = _next_signature_status(contract, tenant_signed=tenant_signed, admin_signed=admin_signed)
    update["status"] = new_status
    if tenant_signed and admin_signed:
        update["signature_status"] = "signed"
        update["signed_at"] = now

    result = await db.rental_contracts.update_one(
        {"_id": contract_oid, "status": contract.get("status")},
        {"$set": update},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="lease_signature_state_changed")

    return {
        "success": True,
        "message": f"Firma de {signer_role} capturada exitosamente",
        "signer_name": signer_name,
        "signer_role": signer_role,
        "method": sig_type,
        "hash": record["hash"],
        "new_status": new_status,
        "fully_signed": bool(tenant_signed and admin_signed),
    }
