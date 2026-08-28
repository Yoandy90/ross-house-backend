"""Canonical tenant receipt authorization.

Receipt access is read-only but financially sensitive.  The authenticated
marketplace identity must resolve to exactly one tenant, and the payment must
belong to both that tenant and a contract bound to the same tenant.
"""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_marketplace, get_db, serialize
from rental.tenant_integrity import resolve_authenticated_tenant

router = APIRouter()


@router.get('/tenant/payment/{payment_id}/receipt')
async def secure_tenant_payment_receipt(payment_id: str, request: Request):
    user = await auth_marketplace(request)
    tenant = await resolve_authenticated_tenant(user)
    if not tenant:
        raise HTTPException(status_code=403, detail="receipt_tenant_not_linked")

    if not ObjectId.is_valid(payment_id):
        raise HTTPException(status_code=400, detail="receipt_payment_id_invalid")

    db = get_db()
    payment = await db.rental_payments.find_one({"_id": ObjectId(payment_id)})
    if not payment:
        raise HTTPException(status_code=404, detail="receipt_payment_not_found")

    tenant_id = str(tenant["_id"])
    if str(payment.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=403, detail="receipt_payment_tenant_mismatch")

    contract_id = str(payment.get("contract_id") or "")
    if not ObjectId.is_valid(contract_id):
        raise HTTPException(status_code=409, detail="receipt_contract_invalid")
    contract = await db.rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=409, detail="receipt_contract_missing")
    if str(contract.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=403, detail="receipt_contract_tenant_mismatch")

    payment_property_id = str(payment.get("property_id") or "")
    contract_property_id = str(contract.get("property_id") or "")
    if payment_property_id and payment_property_id != contract_property_id:
        raise HTTPException(status_code=409, detail="receipt_payment_property_mismatch")

    from rental_pdf_service import generate_rental_receipt_pdf
    pdf_b64 = generate_rental_receipt_pdf(
        payment=serialize(payment),
        contract=serialize(contract),
        tenant=serialize(tenant),
    )
    receipt_num = payment.get("receipt_number") or payment_id
    return {
        "success": True,
        "pdf_base64": pdf_b64,
        "filename": f"Receipt_{receipt_num}.pdf",
    }
