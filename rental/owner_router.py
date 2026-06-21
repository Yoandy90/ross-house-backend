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


# ─────────────────────────────────────────────────────────────────
#  ADMIN: OWNER CRUD (Propietarios)
# ─────────────────────────────────────────────────────────────────

import bcrypt as _bcrypt

def _hash_password(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


@router.get('/admin/owners')
async def admin_list_owners(request: Request):
    """List all owners (landlords) with aggregate stats: properties count, revenue YTD, expenses, paid, pending payout."""
    await auth_admin(request)
    db = get_db()
    now = datetime.utcnow()
    year_start = datetime(now.year, 1, 1)

    cursor = db.app_users.find({"role": {"$in": ["landlord", "owner"]}, "deleted": {"$ne": True}})
    owners = await cursor.to_list(500)

    out = []
    for u in owners:
        oid = str(u.get("_id"))
        # Internal admin-managed properties (rental.properties.owner_id)
        admin_props = await db.properties.find({"$or": [
            {"owner_id": oid}, {"owner_id": ObjectId(oid)} if ObjectId.is_valid(oid) else {"owner_id": oid}
        ]}).to_list(500)
        # Marketplace listings
        mp_listings = await db.marketplace_listings.find({
            "owner_id": ObjectId(oid) if ObjectId.is_valid(oid) else oid,
            "status": {"$ne": "deleted"}
        }).to_list(500)
        # Stripe Connect status
        connect = await db.stripe_connect_accounts.find_one({"user_id": oid})
        stripe_connected = bool(connect and connect.get("charges_enabled"))

        # Revenue YTD = sum of payments completed for owner's properties this year
        prop_ids = [str(p["_id"]) for p in admin_props]
        revenue_ytd = 0.0
        async for pay in db.rental_payments.find({
            "property_id": {"$in": prop_ids},
            "status": "completed",
            "payment_date": {"$gte": year_start.isoformat()},
        }):
            revenue_ytd += float(pay.get("amount", 0))

        # Maintenance expenses YTD
        maintenance_ytd = 0.0
        async for ex in db.rental_property_expenses.find({
            "property_id": {"$in": prop_ids},
            "category": {"$in": ["maintenance", "repair", "service"]},
            "expense_date": {"$gte": year_start.isoformat()},
        }):
            maintenance_ytd += float(ex.get("amount", 0))

        # Paid out to owner YTD
        paid_ytd = 0.0
        async for po in db.owner_payouts.find({
            "owner_id": oid,
            "status": "completed",
            "payout_date": {"$gte": year_start.isoformat()},
        }):
            paid_ytd += float(po.get("amount", 0))

        out.append({
            "id": oid,
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "company": u.get("business_name") or u.get("company", ""),
            "tax_id": u.get("tax_id", ""),
            "address": u.get("address", ""),
            "city": u.get("city", ""),
            "state": u.get("state", ""),
            "zip_code": u.get("zip_code", ""),
            "status": u.get("status", "active"),
            "kyc_status": u.get("kyc_status", "pending"),
            "stripe_connected": stripe_connected,
            "stripe_account_id": (connect or {}).get("stripe_account_id", ""),
            "created_at": (u.get("created_at") or now).isoformat() if isinstance(u.get("created_at"), datetime) else str(u.get("created_at", "")),
            "stats": {
                "admin_properties": len(admin_props),
                "marketplace_listings": len(mp_listings),
                "total_properties": len(admin_props) + len(mp_listings),
                "revenue_ytd": round(revenue_ytd, 2),
                "maintenance_ytd": round(maintenance_ytd, 2),
                "paid_ytd": round(paid_ytd, 2),
                "pending_payout": round(max(0, revenue_ytd - paid_ytd - maintenance_ytd), 2),
            }
        })

    return {"success": True, "owners": out, "total": len(out)}


@router.post('/admin/owners')
async def admin_create_owner(request: Request):
    """Create a new owner/landlord directly (without public landlord-register flow).
    Body: { name, email, phone?, company?, tax_id?, address?, city?, state?, zip_code?, password? }
    Password is optional — if not provided, generates a temporary one.
    """
    await auth_admin(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Nombre y email son requeridos")

    db = get_db()
    existing = await db.app_users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email")

    pw = (data.get("password") or "").strip()
    is_temp = False
    if not pw:
        pw = f"Lord{datetime.utcnow().strftime('%y%m%d%H%M')}"
        is_temp = True
    elif len(pw) < 6:
        raise HTTPException(status_code=400, detail="Password mínimo 6 caracteres")

    now = datetime.utcnow()
    doc = {
        "name": name,
        "email": email,
        "phone": data.get("phone", ""),
        "business_name": data.get("company", ""),
        "tax_id": data.get("tax_id", ""),
        "address": data.get("address", ""),
        "city": data.get("city", ""),
        "state": data.get("state", "TX"),
        "zip_code": data.get("zip_code", ""),
        "role": "landlord",
        "status": "active",
        "kyc_status": "approved",  # Admin-created owners are pre-approved
        "password_hash": _hash_password(pw),
        "_temp_password": pw if is_temp else "",
        "created_at": now,
        "updated_at": now,
        "created_by_admin": True,
    }
    res = await db.app_users.insert_one(doc)

    return {
        "success": True,
        "owner_id": str(res.inserted_id),
        "temp_password": pw if is_temp else None,
        "message": "Propietario creado",
    }


@router.put('/admin/owners/{owner_id}')
async def admin_update_owner(owner_id: str, request: Request):
    """Update owner profile."""
    await auth_admin(request)
    if not ObjectId.is_valid(owner_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    user = await db.app_users.find_one({"_id": ObjectId(owner_id)})
    if not user:
        raise HTTPException(status_code=404, detail="Propietario no encontrado")

    data = await request.json()
    update = {}
    for f in ["name", "email", "phone", "tax_id", "address", "city", "state", "zip_code", "status", "kyc_status"]:
        if f in data:
            update[f] = data[f]
    if "company" in data:
        update["business_name"] = data["company"]
    update["updated_at"] = datetime.utcnow()

    await db.app_users.update_one({"_id": ObjectId(owner_id)}, {"$set": update})
    return {"success": True, "message": "Propietario actualizado"}


@router.delete('/admin/owners/{owner_id}')
async def admin_delete_owner(owner_id: str, request: Request):
    """Soft-delete owner. Properties are NOT deleted but unassigned (owner_id removed)."""
    await auth_admin(request)
    if not ObjectId.is_valid(owner_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    user = await db.app_users.find_one({"_id": ObjectId(owner_id)})
    if not user:
        raise HTTPException(status_code=404, detail="Propietario no encontrado")

    # Soft-delete user
    await db.app_users.update_one(
        {"_id": ObjectId(owner_id)},
        {"$set": {"deleted": True, "deleted_at": datetime.utcnow(), "status": "deleted"}},
    )
    # Unassign properties (so they show "Sin Propietario" instead of phantom owner)
    unassign_count = await db.properties.update_many(
        {"$or": [{"owner_id": owner_id}, {"owner_id": ObjectId(owner_id)}]},
        {"$set": {"owner_id": None, "owner_name": "", "owner_email": "", "owner_phone": ""}},
    )
    return {
        "success": True,
        "message": "Propietario eliminado",
        "properties_unassigned": unassign_count.modified_count,
    }


@router.get('/admin/owners/{owner_id}')
async def admin_get_owner_detail(owner_id: str, request: Request):
    """Detailed owner profile: properties, recent payments, expenses, payout history, contracts."""
    await auth_admin(request)
    if not ObjectId.is_valid(owner_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    user = await db.app_users.find_one({"_id": ObjectId(owner_id)})
    if not user:
        raise HTTPException(status_code=404, detail="Propietario no encontrado")

    # Properties (admin + marketplace)
    admin_props = await db.properties.find({"$or": [
        {"owner_id": owner_id}, {"owner_id": ObjectId(owner_id)}
    ]}).to_list(200)
    mp_listings = await db.marketplace_listings.find({
        "owner_id": ObjectId(owner_id), "status": {"$ne": "deleted"}
    }).to_list(200)

    prop_ids = [str(p["_id"]) for p in admin_props]

    # Recent payments
    payments = []
    async for pay in db.rental_payments.find({"property_id": {"$in": prop_ids}}).sort("payment_date", -1).limit(50):
        payments.append(serialize(pay))

    # Recent expenses
    expenses = []
    async for ex in db.rental_property_expenses.find({"property_id": {"$in": prop_ids}}).sort("expense_date", -1).limit(50):
        expenses.append(serialize(ex))

    # Recent payouts
    payouts = []
    async for po in db.owner_payouts.find({"owner_id": owner_id}).sort("payout_date", -1).limit(50):
        payouts.append(serialize(po))

    # Active contracts on owner's properties
    contracts = []
    async for c in db.rental_contracts.find({"property_id": {"$in": prop_ids}, "status": "active"}).sort("start_date", -1).limit(50):
        contracts.append(serialize(c))

    return {
        "success": True,
        "owner": {
            "id": str(user.get("_id")),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
            "company": user.get("business_name", ""),
            "tax_id": user.get("tax_id", ""),
            "address": user.get("address", ""),
            "city": user.get("city", ""),
            "state": user.get("state", ""),
            "zip_code": user.get("zip_code", ""),
            "status": user.get("status", "active"),
            "kyc_status": user.get("kyc_status", "pending"),
        },
        "admin_properties": [serialize(p) for p in admin_props],
        "marketplace_listings": [serialize(l) for l in mp_listings],
        "payments": payments,
        "expenses": expenses,
        "payouts": payouts,
        "contracts": contracts,
        "summary": {
            "properties_count": len(admin_props) + len(mp_listings),
            "total_revenue": sum(float(p.get("amount", 0)) for p in payments if p.get("status") == "completed"),
            "total_expenses": sum(float(e.get("amount", 0)) for e in expenses),
            "total_paid_out": sum(float(p.get("amount", 0)) for p in payouts if p.get("status") == "completed"),
            "active_contracts": len(contracts),
        }
    }


@router.patch('/admin/properties/{property_id}/assign-owner')
async def admin_assign_property_owner(property_id: str, request: Request):
    """Assign or change a property's owner. Body: { owner_id, owner_name?, owner_email?, owner_phone? }
    Pass owner_id=null to unassign.
    """
    await auth_admin(request)
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=400, detail="ID de propiedad inválido")
    db = get_db()
    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    data = await request.json()
    owner_id_raw = data.get("owner_id")

    if not owner_id_raw:
        # Unassign
        await db.properties.update_one(
            {"_id": ObjectId(property_id)},
            {"$set": {"owner_id": None, "owner_name": "", "owner_email": "", "owner_phone": "", "updated_at": datetime.utcnow()}},
        )
        return {"success": True, "message": "Propiedad desasignada"}

    if not ObjectId.is_valid(owner_id_raw):
        raise HTTPException(status_code=400, detail="owner_id inválido")
    owner = await db.app_users.find_one({"_id": ObjectId(owner_id_raw)})
    if not owner:
        raise HTTPException(status_code=404, detail="Propietario no encontrado")

    update = {
        "owner_id": str(owner.get("_id")),
        "owner_name": owner.get("name", ""),
        "owner_email": owner.get("email", ""),
        "owner_phone": owner.get("phone", ""),
        "updated_at": datetime.utcnow(),
    }
    await db.properties.update_one({"_id": ObjectId(property_id)}, {"$set": update})
    return {"success": True, "message": f"Asignada a {owner.get('name', '')}", "owner_id": str(owner.get("_id"))}


