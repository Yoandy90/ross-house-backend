"""
Rental Owner Router
====================
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

router = APIRouter()

@router.get('/owner/dashboard')
async def owner_dashboard(request: Request):
    """Owner: Full financial dashboard with income, expenses, alerts"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    owner_id = user.get("_id")
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Get all approved listings
    listings = await get_db().marketplace_listings.find(
        {"owner_id": owner_id, "status": "approved"}
    ).to_list(100)
    listing_ids = [str(l["_id"]) for l in listings]

    # Income: payments collected for owner's properties
    all_payments = await get_db().owner_payments.find(
        {"owner_id": owner_id}
    ).sort("created_at", -1).to_list(500)

    total_income = sum(p.get("gross_amount", 0) for p in all_payments)
    total_commission = sum(p.get("commission_amount", 0) for p in all_payments)
    total_net = sum(p.get("net_amount", 0) for p in all_payments)

    month_payments = [p for p in all_payments if p.get("created_at", datetime.min) >= month_start]
    month_income = sum(p.get("gross_amount", 0) for p in month_payments)
    month_commission = sum(p.get("commission_amount", 0) for p in month_payments)
    month_net = sum(p.get("net_amount", 0) for p in month_payments)

    # Expenses: maintenance and repair costs
    all_expenses = await get_db().owner_expenses.find(
        {"owner_id": owner_id}
    ).sort("created_at", -1).to_list(500)

    total_expenses = sum(e.get("amount", 0) for e in all_expenses)
    month_expenses_list = [e for e in all_expenses if e.get("created_at", datetime.min) >= month_start]
    month_expenses = sum(e.get("amount", 0) for e in month_expenses_list)

    # Maintenance alerts for owner's properties
    maintenance_alerts = []
    for lid in listing_ids:
        alerts_cursor = get_db().owner_maintenance_alerts.find(
            {"listing_id": lid, "status": {"$ne": "resolved"}}
        ).sort("created_at", -1)
        async for a in alerts_cursor:
            maintenance_alerts.append(serialize(a))

    # Payouts (money sent to owner)
    payouts = await get_db().owner_payouts.find(
        {"owner_id": owner_id}
    ).sort("created_at", -1).to_list(50)
    total_paid_out = sum(p.get("amount", 0) for p in payouts if p.get("status") == "completed")
    pending_payout = total_net - total_paid_out - total_expenses

    # Recent transactions (last 10)
    recent = []
    for p in all_payments[:5]:
        recent.append({
            "type": "income",
            "amount": p.get("net_amount", 0),
            "description": f"Renta - {p.get('property_address', 'Propiedad')}",
            "date": str(p.get("created_at", "")),
        })
    for e in all_expenses[:5]:
        recent.append({
            "type": "expense",
            "amount": e.get("amount", 0),
            "description": e.get("description", "Gasto"),
            "date": str(e.get("created_at", "")),
        })
    recent.sort(key=lambda x: x["date"], reverse=True)

    return {
        "success": True,
        "properties_count": len(listings),
        "financials": {
            "total_income": total_income,
            "total_commission": total_commission,
            "total_net": total_net,
            "total_expenses": total_expenses,
            "total_paid_out": total_paid_out,
            "pending_payout": max(pending_payout, 0),
            "month_income": month_income,
            "month_commission": month_commission,
            "month_net": month_net,
            "month_expenses": month_expenses,
        },
        "maintenance_alerts": maintenance_alerts[:10],
        "recent_transactions": recent[:10],
        "payouts": [serialize(p) for p in payouts[:10]],
    }


@router.get('/owner/banking')
async def get_owner_banking(request: Request):
    """Owner: Get saved banking details"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    banking = await get_db().owner_banking.find_one({"owner_id": user.get("_id")})
    if not banking:
        return {"success": True, "banking": None}

    b = serialize(banking)
    routing = b.get("routing_number", "")
    account = b.get("account_number", "")
    return {
        "success": True,
        "banking": {
            "bank_name": b.get("bank_name", ""),
            "account_holder": b.get("account_holder", ""),
            "routing_masked": f"****{routing[-4:]}" if len(routing) >= 4 else "",
            "account_masked": f"****{account[-4:]}" if len(account) >= 4 else "",
            "has_banking": True,
        }
    }


@router.post('/owner/banking')
async def save_owner_banking(request: Request):
    """Owner: Save banking details for payouts"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    data = await request.json()
    banking = {
        "owner_id": user.get("_id"),
        "bank_name": data.get("bank_name", ""),
        "account_holder": data.get("account_holder", ""),
        "routing_number": data.get("routing_number", ""),
        "account_number": data.get("account_number", ""),
        "account_type": data.get("account_type", "checking"),
        "updated_at": datetime.utcnow(),
    }

    await get_db().owner_banking.update_one(
        {"owner_id": user.get("_id")},
        {"$set": banking, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True
    )

    return {"success": True, "message": "Datos bancarios guardados"}


# ── Admin: Payout & Expense Management ──

@router.post('/admin/owner-payment')
async def admin_record_payment(request: Request):
    """Admin: Record a rent payment collected for a property (creates income for owner)"""
    await auth_admin(request)
    data = await request.json()

    listing_id = data.get("listing_id", "")
    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    gross = float(data.get("amount", 0))
    rate = listing.get("commission_rate", 10) / 100
    commission = round(gross * rate, 2)
    net = round(gross - commission, 2)

    payment = {
        "owner_id": listing.get("owner_id"),
        "listing_id": listing_id,
        "property_address": listing.get("address", ""),
        "tenant_name": data.get("tenant_name", ""),
        "gross_amount": gross,
        "commission_rate": listing.get("commission_rate", 10),
        "commission_amount": commission,
        "net_amount": net,
        "period": data.get("period", datetime.utcnow().strftime("%B %Y")),
        "payment_method": data.get("payment_method", ""),
        "notes": data.get("notes", ""),
        "created_at": datetime.utcnow(),
    }
    result = await get_db().owner_payments.insert_one(payment)

    return {
        "success": True,
        "payment_id": str(result.inserted_id),
        "gross": gross,
        "commission": commission,
        "net": net,
    }


@router.post('/admin/owner-expense')
async def admin_record_expense(request: Request):
    """Admin: Record an expense against an owner's property (maintenance, repair)"""
    await auth_admin(request)
    data = await request.json()

    expense = {
        "owner_id": data.get("owner_id", ""),
        "listing_id": data.get("listing_id", ""),
        "property_address": data.get("property_address", ""),
        "amount": float(data.get("amount", 0)),
        "category": data.get("category", "maintenance"),
        "description": data.get("description", ""),
        "notes": data.get("notes", ""),
        "created_at": datetime.utcnow(),
    }
    result = await get_db().owner_expenses.insert_one(expense)
    return {"success": True, "expense_id": str(result.inserted_id)}


@router.post('/admin/owner-payout')
async def admin_record_payout(request: Request):
    """Admin: Record a payout sent to a property owner"""
    await auth_admin(request)
    data = await request.json()

    payout = {
        "owner_id": data.get("owner_id", ""),
        "amount": float(data.get("amount", 0)),
        "method": data.get("method", "bank_transfer"),
        "reference": data.get("reference", ""),
        "notes": data.get("notes", ""),
        "status": "completed",
        "created_at": datetime.utcnow(),
    }
    result = await get_db().owner_payouts.insert_one(payout)
    return {"success": True, "payout_id": str(result.inserted_id)}


@router.post('/admin/maintenance-alert')
async def admin_create_maintenance_alert(request: Request):
    """Admin: Forward a maintenance issue to property owner"""
    await auth_admin(request)
    data = await request.json()

    alert = {
        "owner_id": data.get("owner_id", ""),
        "listing_id": data.get("listing_id", ""),
        "property_address": data.get("property_address", ""),
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "category": data.get("category", "general"),
        "priority": data.get("priority", "medium"),
        "photos": data.get("photos", []),
        "tenant_name": data.get("tenant_name", ""),
        "status": "new",
        "created_at": datetime.utcnow(),
    }
    result = await get_db().owner_maintenance_alerts.insert_one(alert)
    
    # ── Send push notification to property owner ──
    owner_id = data.get("owner_id", "")
    if owner_id:
        try:
            priority_labels = {"low": "🟢 Baja", "medium": "🟡 Media", "high": "🔴 Alta", "urgent": "🚨 Urgente"}
            priority_label = priority_labels.get(data.get("priority", "medium"), "🟡 Media")
            await send_rental_push_to_user(
                user_id=owner_id,
                title=f"🏠 Alerta de Mantenimiento ({priority_label})",
                body=f"{data.get('property_address', 'Propiedad')}: {data.get('title', '')}",
                data={"type": "maintenance_alert", "alert_id": str(result.inserted_id)}
            )
        except Exception as e:
            logging.warning(f"⚠️ Push notification error (maintenance alert): {e}")
    
    return {"success": True, "alert_id": str(result.inserted_id)}


@router.get('/admin/owner-financials')
async def admin_owner_financials(request: Request):
    """Admin: Get financial overview of all managed properties"""
    await auth_admin(request)

    # All owners
    owners = await get_db().app_users.find({"role": "landlord"}).to_list(200)
    
    owner_summaries = []
    total_commission = 0
    total_managed = 0
    
    for owner in owners:
        oid = str(owner["_id"])
        listings_count = await get_db().marketplace_listings.count_documents({"owner_id": oid, "status": "approved"})
        if listings_count == 0:
            continue
            
        payments = await get_db().owner_payments.find({"owner_id": oid}).to_list(500)
        expenses = await get_db().owner_expenses.find({"owner_id": oid}).to_list(500)
        payouts = await get_db().owner_payouts.find({"owner_id": oid, "status": "completed"}).to_list(500)

        income = sum(p.get("gross_amount", 0) for p in payments)
        commission = sum(p.get("commission_amount", 0) for p in payments)
        net = sum(p.get("net_amount", 0) for p in payments)
        exp = sum(e.get("amount", 0) for e in expenses)
        paid = sum(p.get("amount", 0) for p in payouts)
        pending = max(net - paid - exp, 0)

        total_commission += commission
        total_managed += listings_count

        banking = await get_db().owner_banking.find_one({"owner_id": oid})

        owner_summaries.append({
            "owner_id": oid,
            "name": owner.get("name", ""),
            "email": owner.get("email", ""),
            "properties": listings_count,
            "total_income": income,
            "total_commission": commission,
            "total_expenses": exp,
            "total_paid": paid,
            "pending_payout": pending,
            "has_banking": bool(banking),
        })

    return {
        "success": True,
        "total_commission_earned": total_commission,
        "total_managed_properties": total_managed,
        "owners": owner_summaries,
    }


