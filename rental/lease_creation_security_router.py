"""Canonical admin lease creation boundary.

The historical create endpoint accepted client-supplied relationship metadata.
This first-match compatibility route derives property/tenant/unit identity from
server records and forbids creating an already-active lease. Activation remains
a separate guarded lifecycle transition.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db

router = APIRouter()
_ALLOWED_INITIAL_STATUSES = {
    "draft", "pending", "pending_signature", "pending_tenant", "pending_signatures"
}


def _oid(value, detail: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(str(value))


@router.post('/admin/rental-contracts')
async def secure_create_rental_contract(request: Request):
    admin = await auth_admin(request)
    data = await request.json()
    db = get_db()

    property_id = str(data.get("property_id") or "").strip()
    tenant_id = str(data.get("tenant_id") or "").strip()
    prop = await db.properties.find_one({"_id": _oid(property_id, "lease_property_invalid")})
    if not prop:
        raise HTTPException(status_code=404, detail="lease_property_not_found")
    tenant = await db.tenants.find_one({"_id": _oid(tenant_id, "lease_tenant_invalid")})
    if not tenant:
        raise HTTPException(status_code=404, detail="lease_tenant_not_found")

    requested_status = str(data.get("status") or "draft").strip().lower()
    if requested_status == "active":
        raise HTTPException(status_code=409, detail="lease_creation_cannot_bypass_activation")
    if requested_status not in _ALLOWED_INITIAL_STATUSES:
        raise HTTPException(status_code=400, detail="lease_initial_status_invalid")

    unit_id = str(data.get("unit_id") or "").strip() or None
    unit = None
    if unit_id:
        unit = await db.property_units.find_one({"_id": _oid(unit_id, "lease_unit_invalid")})
        if not unit:
            raise HTTPException(status_code=404, detail="lease_unit_not_found")
        if str(unit.get("property_id") or "") != property_id:
            raise HTTPException(status_code=409, detail="lease_unit_property_mismatch")
        if unit.get("status") == "maintenance":
            raise HTTPException(status_code=409, detail="lease_unit_in_maintenance")
        if unit.get("current_contract_id"):
            raise HTTPException(status_code=409, detail="lease_unit_already_claimed")

    property_owner = str(prop.get("owner_id") or "").strip()
    supplied_landlord = str(data.get("landlord_id") or "").strip()
    if property_owner and supplied_landlord and supplied_landlord != property_owner:
        raise HTTPException(status_code=409, detail="lease_landlord_property_owner_mismatch")
    landlord_id = property_owner or supplied_landlord

    now = datetime.utcnow()
    count = await db.rental_contracts.count_documents({})
    contract_number = f"CONT-{now.year}-{str(count + 1).zfill(3)}"
    authority = unit or prop

    try:
        rent_amount = float(data.get("rent_amount", authority.get("rent_amount", 0)) or 0)
        deposit_amount = float(data.get("deposit_amount", authority.get("deposit_amount", 0)) or 0)
        due_day = int(data.get("payment_due_day", 1) or 1)
        late_fee = float(data.get("late_fee_amount", 50) or 0)
        grace_days = int(data.get("late_fee_grace_days", 5) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lease_financial_terms_invalid")
    if rent_amount < 0 or deposit_amount < 0 or late_fee < 0 or not 1 <= due_day <= 31 or grace_days < 0:
        raise HTTPException(status_code=400, detail="lease_financial_terms_invalid")

    address = str(prop.get("address") or "").strip()
    if unit and unit.get("unit_name"):
        address = f"{address} — {unit.get('unit_name')}"

    contract_doc = {
        "contract_number": contract_number,
        "property_id": property_id,
        "property_address": address,
        "property_number": prop.get("property_number", ""),
        "unit_id": unit_id,
        "unit_name": unit.get("unit_name", "") if unit else "",
        "tenant_id": tenant_id,
        "tenant_name": tenant.get("name", ""),
        "tenant_phone": tenant.get("phone", ""),
        "tenant_email": tenant.get("email", ""),
        "landlord_id": landlord_id,
        "start_date": data.get("start_date", now.strftime("%Y-%m-%d")),
        "end_date": data.get("end_date", ""),
        "rent_amount": rent_amount,
        "deposit_amount": deposit_amount,
        "payment_due_day": due_day,
        "late_fee_amount": late_fee,
        "late_fee_grace_days": grace_days,
        "terms": data.get("terms", ""),
        "special_conditions": data.get("special_conditions", ""),
        "payment_method_type": data.get("payment_method_type", "cash"),
        "addendums": data.get("addendums", {}),
        "status": requested_status,
        "signature": None,
        "signature_status": "pending",
        "tenant_signature": None,
        "tenant_signed_at": None,
        "landlord_signature": None,
        "landlord_signed_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": admin.get("email", "admin"),
        "relationship_source": "canonical_records",
    }
    result = await db.rental_contracts.insert_one(contract_doc)
    return {
        "success": True,
        "message": f"Contrato {contract_number} creado",
        "contract_id": str(result.inserted_id),
        "contract_number": contract_number,
    }
