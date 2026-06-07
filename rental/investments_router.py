"""
Rental Investments Router
==========================
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import uuid
from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

router = APIRouter()

INVESTMENT_PHASES = ["adquirida", "en_remodelacion", "en_venta", "vendida", "converted_to_rental"]


@router.post('/admin/investments')
async def admin_create_investment(request: Request):
    """Admin: Register a new investment property acquisition"""
    await auth_admin(request)
    data = await request.json()

    required = ["address", "purchase_price"]
    for f in required:
        if not data.get(f):
            raise HTTPException(status_code=400, detail=f"Campo requerido: {f}")

    investment = {
        "address": data.get("address", ""),
        "city": data.get("city", ""),
        "state": data.get("state", "TX"),
        "zip_code": data.get("zip_code", ""),
        "property_type": data.get("property_type", "house"),
        "bedrooms": int(data.get("bedrooms", 0)),
        "bathrooms": float(data.get("bathrooms", 0)),
        "square_feet": int(data.get("square_feet", 0)),
        "purchase_price": float(data.get("purchase_price", 0)),
        "purchase_date": data.get("purchase_date", ""),
        "seller_name": data.get("seller_name", ""),
        "seller_contact": data.get("seller_contact", ""),
        "estimated_repair_cost": float(data.get("estimated_repair_cost", 0)),
        "estimated_sale_price": float(data.get("estimated_sale_price", 0)),
        "notes": data.get("notes", ""),
        "phase": "adquirida",
        "expenses": [],
        "photos_before": data.get("photos_before", []),
        "photos_during": [],
        "photos_after": [],
        "sale_price": 0,
        "sale_date": "",
        "buyer_name": "",
        "agent_commission": 0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await get_db().investments.insert_one(investment)
    return {
        "success": True,
        "investment_id": str(result.inserted_id),
        "message": "Propiedad de inversión registrada"
    }


@router.get('/admin/investments/dashboard')
async def admin_investments_dashboard(request: Request):
    """Admin: Investment portfolio dashboard with ROI and pipeline stats"""
    await auth_admin(request)

    # Phase counts
    phase_counts = {}
    for phase in INVESTMENT_PHASES:
        count = await get_db().investments.count_documents({"phase": phase})
        phase_counts[phase] = count

    # Aggregate stats
    total_invested = 0
    total_sold = 0
    total_expenses = 0
    total_profit = 0
    active_count = 0

    async for inv in get_db().investments.find():
        inv = serialize(inv)
        purchase = inv.get("purchase_price", 0)
        expenses = sum(e.get("amount", 0) for e in inv.get("expenses", []))
        sale = inv.get("sale_price", 0)

        total_invested += purchase
        total_expenses += expenses

        if sale > 0:
            total_sold += sale
            total_profit += (sale - purchase - expenses)
        else:
            active_count += 1

    avg_roi = (total_profit / (total_invested + total_expenses) * 100) if (total_invested + total_expenses) > 0 and total_profit > 0 else 0

    # Recent expenses
    recent_expenses = []
    async for inv in get_db().investments.find({"expenses": {"$ne": []}}).sort("updated_at", -1).limit(5):
        inv = serialize(inv)
        for e in sorted(inv.get("expenses", []), key=lambda x: x.get("created_at", ""), reverse=True)[:2]:
            recent_expenses.append({
                "property": inv.get("address", ""),
                "description": e.get("description", ""),
                "amount": e.get("amount", 0),
                "category": e.get("category", ""),
                "date": e.get("date", ""),
            })

    return {
        "success": True,
        "pipeline": phase_counts,
        "stats": {
            "total_invested": total_invested,
            "total_expenses": total_expenses,
            "total_sold": total_sold,
            "total_profit": total_profit,
            "avg_roi": round(avg_roi, 1),
            "active_properties": active_count,
            "total_properties": sum(phase_counts.values()),
        },
        "recent_expenses": recent_expenses[:8],
    }


@router.get('/admin/investments')
async def admin_list_investments(request: Request):
    """Admin: List all investment properties with summary"""
    await auth_admin(request)
    phase_filter = request.query_params.get("phase", "all")

    query = {}
    if phase_filter != "all" and phase_filter in INVESTMENT_PHASES:
        query["phase"] = phase_filter

    cursor = get_db().investments.find(query).sort("created_at", -1)
    investments = []
    async for inv in cursor:
        doc = serialize(inv)
        expenses = doc.get("expenses", [])
        total_expenses = sum(e.get("amount", 0) for e in expenses)
        purchase = doc.get("purchase_price", 0)
        total_invested = purchase + total_expenses

        sale_price = doc.get("sale_price", 0)
        profit = sale_price - total_invested if sale_price > 0 else 0
        roi = (profit / total_invested * 100) if total_invested > 0 and sale_price > 0 else 0

        investments.append({
            "id": doc.get("_id"),
            "address": doc.get("address", ""),
            "city": doc.get("city", ""),
            "state": doc.get("state", ""),
            "property_type": doc.get("property_type", ""),
            "phase": doc.get("phase", "adquirida"),
            "purchase_price": purchase,
            "total_expenses": total_expenses,
            "total_invested": total_invested,
            "estimated_repair_cost": doc.get("estimated_repair_cost", 0),
            "estimated_sale_price": doc.get("estimated_sale_price", 0),
            "sale_price": sale_price,
            "profit": profit,
            "roi": round(roi, 1),
            "expense_count": len(expenses),
            "expenses": expenses,  # Include full expenses with receipts
            "notes": doc.get("notes", ""),
            "photo_count": len(doc.get("photos_before", [])) + len(doc.get("photos_during", [])) + len(doc.get("photos_after", [])),
            "purchase_date": doc.get("purchase_date", ""),
            "sale_date": doc.get("sale_date", ""),
            "created_at": doc.get("created_at", ""),
        })

    return {"success": True, "investments": investments, "count": len(investments)}


@router.get('/admin/investments/{inv_id}')
async def admin_get_investment(inv_id: str, request: Request):
    """Admin: Get full investment property details"""
    await auth_admin(request)
    try:
        inv = await get_db().investments.find_one({"_id": ObjectId(inv_id)})
    except:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")

    doc = serialize(inv)
    expenses = doc.get("expenses", [])
    total_expenses = sum(e.get("amount", 0) for e in expenses)
    purchase = doc.get("purchase_price", 0)
    total_invested = purchase + total_expenses
    sale_price = doc.get("sale_price", 0)
    profit = sale_price - total_invested if sale_price > 0 else 0
    roi = (profit / total_invested * 100) if total_invested > 0 and sale_price > 0 else 0

    doc["total_expenses"] = total_expenses
    doc["total_invested"] = total_invested
    doc["profit"] = profit
    doc["roi"] = round(roi, 1)

    # Expense breakdown by category
    by_category = {}
    for e in expenses:
        cat = e.get("category", "otros")
        by_category[cat] = by_category.get(cat, 0) + e.get("amount", 0)
    doc["expense_breakdown"] = by_category

    return {"success": True, "investment": doc}


@router.put('/admin/investments/{inv_id}')
async def admin_update_investment(inv_id: str, request: Request):
    """Admin: Update investment property details or change phase"""
    await auth_admin(request)
    data = await request.json()

    try:
        inv = await get_db().investments.find_one({"_id": ObjectId(inv_id)})
    except:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")

    allowed = [
        "address", "city", "state", "zip_code", "property_type",
        "bedrooms", "bathrooms", "square_feet",
        "purchase_price", "purchase_date", "seller_name", "seller_contact",
        "estimated_repair_cost", "estimated_sale_price", "notes", "phase",
        "sale_price", "sale_date", "buyer_name", "buyer_contact", "agent_commission",
    ]

    update = {"updated_at": datetime.utcnow()}
    for field in allowed:
        if field in data:
            if field in ["purchase_price", "estimated_repair_cost", "estimated_sale_price", "sale_price", "agent_commission"]:
                update[field] = float(data[field])
            elif field in ["bedrooms", "square_feet"]:
                update[field] = int(data[field])
            elif field == "bathrooms":
                update[field] = float(data[field])
            else:
                update[field] = data[field]

    await get_db().investments.update_one({"_id": ObjectId(inv_id)}, {"$set": update})
    return {"success": True, "message": "Inversión actualizada"}


@router.post('/admin/investments/{inv_id}/expenses')
async def admin_add_expense(inv_id: str, request: Request):
    """Admin: Add an expense/repair cost to an investment property"""
    await auth_admin(request)
    data = await request.json()

    if not data.get("description") or not data.get("amount"):
        raise HTTPException(status_code=400, detail="Descripción y monto son requeridos")

    try:
        inv = await get_db().investments.find_one({"_id": ObjectId(inv_id)})
    except:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")

    expense = {
        "id": str(ObjectId()),
        "description": data.get("description", ""),
        "amount": float(data.get("amount", 0)),
        "category": data.get("category", "otros"),
        "vendor": data.get("vendor", ""),
        "date": data.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
        "receipt_photo": data.get("receipt_photo", ""),
        "receipts": data.get("receipts", []),  # Support multiple receipts
        "notes": data.get("notes", ""),
        "created_at": datetime.utcnow(),
    }

    await get_db().investments.update_one(
        {"_id": ObjectId(inv_id)},
        {
            "$push": {"expenses": expense},
            "$set": {"updated_at": datetime.utcnow()},
        }
    )

    return {"success": True, "expense_id": expense["id"], "message": "Gasto agregado"}


@router.delete('/admin/investments/{inv_id}/expenses/{expense_id}')
async def admin_delete_expense(inv_id: str, expense_id: str, request: Request):
    """Admin: Remove an expense from an investment"""
    await auth_admin(request)

    result = await get_db().investments.update_one(
        {"_id": ObjectId(inv_id)},
        {
            "$pull": {"expenses": {"id": expense_id}},
            "$set": {"updated_at": datetime.utcnow()},
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    return {"success": True, "message": "Gasto eliminado"}


@router.post('/admin/investments/{inv_id}/photos')
async def admin_add_investment_photos(inv_id: str, request: Request):
    """Admin: Add photos to an investment property (before/during/after)"""
    await auth_admin(request)
    data = await request.json()

    photo_type = data.get("type", "during")
    photos = data.get("photos", [])

    if photo_type not in ["before", "during", "after"]:
        raise HTTPException(status_code=400, detail="Tipo de foto inválido: before, during, after")
    if not photos:
        raise HTTPException(status_code=400, detail="Se requiere al menos una foto")

    field = f"photos_{photo_type}"
    await get_db().investments.update_one(
        {"_id": ObjectId(inv_id)},
        {
            "$push": {field: {"$each": photos[:10]}},
            "$set": {"updated_at": datetime.utcnow()},
        }
    )

    return {"success": True, "added": len(photos[:10]), "message": f"Fotos '{photo_type}' agregadas"}


@router.get('/admin/investments/dashboard')
async def admin_investments_dashboard_v2(request: Request):
    """Duplicate removed - see admin_investments_dashboard above"""
    return await admin_investments_dashboard(request)


@router.post('/admin/investments/{inv_id}/convert-to-rental')
async def admin_convert_investment_to_rental(inv_id: str, request: Request):
    """
    Convert an investment property to a rental property.
    This creates a new property in the rentals system and archives the investment.
    """
    await auth_admin(request)
    data = await request.json()
    
    # Get the investment
    try:
        inv = await get_db().investments.find_one({"_id": ObjectId(inv_id)})
    except:
        raise HTTPException(status_code=400, detail="ID de inversión inválido")
    
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")
    
    if inv.get("phase") == "converted_to_rental":
        raise HTTPException(status_code=400, detail="Esta inversión ya fue convertida a alquiler")
    
    # Calculate total invested
    expenses_total = sum(e.get("amount", 0) for e in inv.get("expenses", []))
    total_invested = inv.get("purchase_price", 0) + expenses_total
    
    # Create the rental property
    now = datetime.utcnow()
    rental_property = {
        "name": data.get("name", inv.get("address", "Propiedad sin nombre")),
        "address": inv.get("address", ""),
        "city": inv.get("city", ""),
        "state": inv.get("state", "TX"),
        "zip_code": inv.get("zip_code", ""),
        "property_type": inv.get("property_type", "house"),
        "bedrooms": inv.get("bedrooms", 0),
        "bathrooms": inv.get("bathrooms", 0),
        "square_feet": inv.get("square_feet", 0),
        "description": data.get("description", inv.get("notes", "")),
        "amenities": data.get("amenities", []),
        "photos": inv.get("photos_after", []) or inv.get("photos_during", []) or inv.get("photos_before", []),
        "rent_price": float(data.get("rent_price", 0)),
        "deposit_amount": float(data.get("deposit_amount", 0)),
        "status": "available",
        "is_active": True,
        # Investment tracking fields
        "converted_from_investment": True,
        "original_investment_id": str(inv["_id"]),
        "total_investment": total_invested,
        "purchase_price": inv.get("purchase_price", 0),
        "repair_costs": expenses_total,
        "conversion_date": now,
        # Standard fields
        "created_at": now,
        "updated_at": now,
    }
    
    # Insert the rental property
    result = await get_db().properties.insert_one(rental_property)
    property_id = str(result.inserted_id)
    
    # Update the investment to mark as converted
    await get_db().investments.update_one(
        {"_id": ObjectId(inv_id)},
        {
            "$set": {
                "phase": "converted_to_rental",
                "converted_to_property_id": property_id,
                "conversion_date": now,
                "rent_price": float(data.get("rent_price", 0)),
                "updated_at": now,
            }
        }
    )
    
    return {
        "success": True,
        "property_id": property_id,
        "investment_id": inv_id,
        "total_invested": total_invested,
        "message": f"Inversión convertida a propiedad de alquiler exitosamente"
    }





