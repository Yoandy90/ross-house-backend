"""Secure tenant dashboard route.

Registered before the historical tenant_router route so dashboard reads are
bound to one authenticated tenant identity and one active lease.  This is a
compatibility shim while the oversized tenant_router is decomposed.
"""
from calendar import monthrange
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Request

from rental.shared import auth_marketplace, get_db
from rental.tenant_integrity import (
    find_active_contract_for_tenant,
    resolve_authenticated_tenant,
)

router = APIRouter()


def _next_due(today: datetime, due_day_value) -> datetime:
    try:
        due_day = int(due_day_value or 1)
    except (TypeError, ValueError):
        due_day = 1
    due_day = max(1, min(due_day, 31))

    year, month = today.year, today.month
    if today.day > min(due_day, monthrange(year, month)[1]):
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    safe_day = min(due_day, monthrange(year, month)[1])
    return datetime(year, month, safe_day)


async def _contract_property(contract: dict):
    db = get_db()
    property_id = str(contract.get("property_id") or "")
    if not ObjectId.is_valid(property_id):
        return None
    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        return None

    result = {
        "id": property_id,
        "address": prop.get("address", ""),
        "city": prop.get("city", ""),
        "state": prop.get("state", ""),
        "bedrooms": prop.get("bedrooms", 0),
        "bathrooms": prop.get("bathrooms", 0),
        "unit": None,
    }

    unit_id = str(contract.get("unit_id") or "")
    if unit_id and ObjectId.is_valid(unit_id):
        unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
        if unit and str(unit.get("property_id") or "") == property_id:
            # Do not trust stale tenant pointers as authority.  Contract is the
            # canonical relationship; unit metadata is descriptive only.
            result["unit"] = {
                "id": unit_id,
                "name": unit.get("unit_name", ""),
                "bedrooms": unit.get("bedrooms", 0),
                "bathrooms": unit.get("bathrooms", 0),
            }
    return result


@router.get('/tenant/dashboard')
async def secure_tenant_dashboard(request: Request):
    user = await auth_marketplace(request)
    db = get_db()
    tenant = await resolve_authenticated_tenant(user)

    if not tenant:
        return {
            "success": True,
            "tenant": {
                "name": user.get("name", ""),
                "email": user.get("email", ""),
                "phone": user.get("phone", ""),
                "tenant_number": "",
            },
            "contract": None,
            "next_payment": None,
            "payments": [],
            "property": None,
        }

    tenant_id = str(tenant["_id"])
    contract = await find_active_contract_for_tenant(tenant)
    contract_data = None
    next_payment = None
    property_data = None

    if contract:
        contract_id = str(contract["_id"])
        contract_data = {
            "id": contract_id,
            "contract_number": contract.get("contract_number", ""),
            "property_address": contract.get("property_address", ""),
            "start_date": str(contract.get("start_date", "")),
            "end_date": str(contract.get("end_date", "")),
            "rent_amount": contract.get("rent_amount", 0),
            "deposit_amount": contract.get("deposit_amount", 0),
            "payment_due_day": contract.get("payment_due_day", 1),
            "late_fee_amount": contract.get("late_fee_amount", 0),
            "late_fee_grace_days": contract.get("late_fee_grace_days", 5),
            "status": "active",
        }

        today = datetime.utcnow()
        next_due = _next_due(today, contract.get("payment_due_day", 1))
        current_month_paid = await db.rental_payments.find_one({
            "contract_id": contract_id,
            "tenant_id": tenant_id,
            "period_month": today.strftime("%B").lower(),
            "period_year": today.year,
            "status": {"$in": ["completed", "paid"]},
        })
        next_payment = {
            "due_date": next_due.strftime("%Y-%m-%d"),
            "amount": contract.get("rent_amount", 0),
            "current_month_paid": bool(current_month_paid),
        }
        property_data = await _contract_property(contract)

    # Historical payment rows remain tenant-scoped.  They are not used as
    # authority for the active lease/property relation.
    payments = []
    cursor = db.rental_payments.find({"tenant_id": tenant_id}).sort("payment_date", -1).limit(24)
    async for payment in cursor:
        payments.append({
            "id": str(payment["_id"]),
            "receipt_number": payment.get("receipt_number", ""),
            "amount": payment.get("amount", 0),
            "late_fee": payment.get("late_fee", 0),
            "total_paid": payment.get("total_paid", 0),
            "payment_method": payment.get("payment_method", ""),
            "period_month": payment.get("period_month", ""),
            "period_year": payment.get("period_year", 0),
            "payment_date": str(payment.get("payment_date", "")),
            "status": payment.get("status", ""),
        })

    return {
        "success": True,
        "tenant": {
            "name": tenant.get("name", ""),
            "email": tenant.get("email", ""),
            "phone": tenant.get("phone", ""),
            "tenant_number": tenant.get("tenant_number", ""),
        },
        "contract": contract_data,
        "next_payment": next_payment,
        "payments": payments,
        "property": property_data,
    }
