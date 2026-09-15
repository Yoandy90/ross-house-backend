"""Secure tenant dashboard route.

Registered before the historical tenant_router route so dashboard reads are
bound to one authenticated tenant identity and one active lease.  This is a
compatibility shim while the oversized tenant_router is decomposed.
"""
from calendar import monthrange
from datetime import datetime, timezone

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


async def _next_unpaid_payment(db, contract: dict, today: datetime) -> dict | None:
    """Return the first uncovered lease month, including an overdue current month."""
    from rental.rent_charge_policy import preview_period_rent_charge
    from rental.rent_payment_cron import _parse_contract_date

    try:
        due_day = int(contract.get("payment_due_day") or 1)
    except (TypeError, ValueError):
        due_day = 1
    due_day = max(1, min(due_day, 31))
    if today.tzinfo is None:
        today = today.replace(tzinfo=timezone.utc)
    cursor = today.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end_dt = await _parse_contract_date(contract.get("end_date"))
    current_month_paid = False
    for index in range(12):
        if end_dt and cursor > end_dt:
            break
        try:
            charge = await preview_period_rent_charge(db, contract, cursor)
        except ValueError:
            charge = None
        if charge:
            paid = charge["status"] in {"paid", "completed"}
            if index == 0:
                current_month_paid = paid
            if not paid:
                invoice = charge.get("invoice") or {}
                attempt = invoice.get("charge_attempt") or {}
                safe_day = min(due_day, monthrange(cursor.year, cursor.month)[1])
                return {
                    "due_date": cursor.replace(day=safe_day).strftime("%Y-%m-%d"),
                    "period": cursor.strftime("%Y-%m"),
                    "amount": charge["outstanding"],
                    "current_month_paid": current_month_paid,
                    "in_flight": attempt.get("status") in {"processing", "unknown"},
                }
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return None


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
        next_payment = await _next_unpaid_payment(db, contract, today)
        property_data = await _contract_property(contract)

    # Historical payment rows remain tenant-scoped.  They are not used as
    # authority for the active lease/property relation.
    payments = []
    cursor = db.rental_payments.find({
        "tenant_id": tenant_id,
        "record_type": {"$ne": "checkout_attempt"},
        "status": {"$in": ["completed", "paid"]},
    }).sort("payment_date", -1).limit(48)
    seen_periods = set()
    async for payment in cursor:
        period_key = payment.get("period") or (
            f"{int(payment.get('period_year') or 0):04d}-"
            f"{int(payment.get('period_month_num') or 0):02d}"
        )
        if period_key == "0000-00":
            period_key = str(payment["_id"])
        if period_key in seen_periods:
            continue
        seen_periods.add(period_key)
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
        if len(payments) >= 24:
            break

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
