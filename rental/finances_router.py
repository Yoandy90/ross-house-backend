"""
Rental Finances Router
=======================
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

EXPENSE_CATEGORIES = {
    'maintenance': 'Mantenimiento',
    'repair': 'Reparación',
    'insurance': 'Seguro',
    'taxes': 'Impuestos',
    'utilities': 'Servicios (agua, luz, gas)',
    'landscaping': 'Jardinería/Exterior',
    'cleaning': 'Limpieza',
    'appliance': 'Electrodomésticos',
    'legal': 'Legal/Desalojo',
    'advertising': 'Publicidad',
    'management': 'Administración',
    'other': 'Otro',
}


@router.get('/admin/property-expenses')
async def list_property_expenses(request: Request):
    """List all property expenses with optional filters"""
    user = await auth_admin(request)
    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))

    query: Dict[str, Any] = {}
    property_id = params.get('property_id', [None])[0]
    if property_id:
        query['property_id'] = property_id

    category = params.get('category', [None])[0]
    if category:
        query['category'] = category

    search = params.get('search', [None])[0]
    if search:
        query['$or'] = [
            {'description': {'$regex': search, '$options': 'i'}},
            {'vendor': {'$regex': search, '$options': 'i'}},
            {'property_address': {'$regex': search, '$options': 'i'}},
        ]

    # Date range filter
    date_from = params.get('date_from', [None])[0]
    date_to = params.get('date_to', [None])[0]
    if date_from or date_to:
        date_query = {}
        if date_from:
            date_query['$gte'] = date_from
        if date_to:
            date_query['$lte'] = date_to
        query['expense_date'] = date_query

    cursor = get_db().property_expenses.find(query).sort("expense_date", -1).limit(500)
    expenses = []
    async for doc in cursor:
        doc['_id'] = str(doc['_id'])
        expenses.append(doc)

    # Also get totals
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": None,
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }}
    ]
    agg = await get_db().property_expenses.aggregate(pipeline).to_list(1)
    totals = agg[0] if agg else {"total": 0, "count": 0}

    # By category
    cat_pipeline = [
        {"$match": query},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        {"$sort": {"total": -1}},
    ]
    cat_data = await get_db().property_expenses.aggregate(cat_pipeline).to_list(20)

    return {
        "expenses": expenses,
        "total_amount": totals.get("total", 0),
        "total_count": totals.get("count", 0),
        "by_category": [{**c, "_id": c["_id"] or "other", "label": EXPENSE_CATEGORIES.get(c["_id"] or "other", c["_id"] or "Otro")} for c in cat_data],
        "categories": EXPENSE_CATEGORIES,
    }


@router.post('/admin/property-expenses')
async def create_property_expense(request: Request):
    """Create a new property expense"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    property_id = data.get('property_id')
    if not property_id:
        raise HTTPException(status_code=400, detail="Se requiere property_id")

    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    count = await get_db().property_expenses.count_documents({})
    expense_number = f"EXP-{now.year}-{str(count + 1).zfill(4)}"

    expense_doc = {
        "expense_number": expense_number,
        "property_id": property_id,
        "property_address": prop.get('address', ''),
        "property_number": prop.get('property_number', ''),
        "category": data.get('category', 'other'),
        "description": data.get('description', ''),
        "amount": float(data.get('amount', 0)),
        "vendor": data.get('vendor', ''),
        "expense_date": data.get('expense_date', now.strftime('%Y-%m-%d')),
        "receipt_number": data.get('receipt_number', ''),
        "notes": data.get('notes', ''),
        "status": data.get('status', 'paid'),  # paid, pending, cancelled
        "created_at": now,
        "updated_at": now,
        "created_by": user.get('email', 'admin'),
    }

    result = await get_db().property_expenses.insert_one(expense_doc)

    return {
        "success": True,
        "message": f"Gasto {expense_number} registrado: {EXPENSE_CATEGORIES.get(expense_doc['category'], 'Otro')} — ${expense_doc['amount']:.2f}",
        "expense_id": str(result.inserted_id),
        "expense_number": expense_number,
    }


@router.put('/admin/property-expenses/{expense_id}')
async def update_property_expense(expense_id: str, request: Request):
    """Update an existing expense"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    expense = await get_db().property_expenses.find_one({"_id": ObjectId(expense_id)})
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    update_fields = {"updated_at": now}
    for field in ['category', 'description', 'vendor', 'expense_date', 'receipt_number', 'notes', 'status']:
        if field in data:
            update_fields[field] = data[field]
    if 'amount' in data:
        update_fields['amount'] = float(data['amount'])
    if 'property_id' in data and data['property_id'] != expense.get('property_id'):
        prop = await get_db().properties.find_one({"_id": ObjectId(data['property_id'])})
        if prop:
            update_fields['property_id'] = data['property_id']
            update_fields['property_address'] = prop.get('address', '')
            update_fields['property_number'] = prop.get('property_number', '')

    await get_db().property_expenses.update_one({"_id": ObjectId(expense_id)}, {"$set": update_fields})
    return {"success": True, "message": "Gasto actualizado exitosamente"}


@router.delete('/admin/property-expenses/{expense_id}')
async def delete_property_expense(expense_id: str, request: Request):
    """Delete an expense"""
    user = await auth_admin(request)
    expense = await get_db().property_expenses.find_one({"_id": ObjectId(expense_id)})
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")

    await get_db().property_expenses.delete_one({"_id": ObjectId(expense_id)})
    return {"success": True, "message": "Gasto eliminado"}


@router.get('/admin/property-performance')
async def get_property_performance(request: Request):
    """Get performance metrics for all properties (revenue vs expenses)"""
    user = await auth_admin(request)

    # Get all properties
    props = []
    async for p in get_db().properties.find().sort("property_number", 1):
        p['_id'] = str(p['_id'])
        props.append(p)

    performance = []
    for prop in props:
        pid = prop['_id']

        # Revenue: sum of payments for contracts linked to this property
        rev_pipeline = [
            {"$match": {"property_id": pid}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        ]
        rev_data = await get_db().rental_payments.aggregate(rev_pipeline).to_list(1)
        total_revenue = rev_data[0]["total"] if rev_data else 0
        payment_count = rev_data[0]["count"] if rev_data else 0

        # Expenses: sum of expenses for this property
        exp_pipeline = [
            {"$match": {"property_id": pid, "status": {"$ne": "cancelled"}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        ]
        exp_data = await get_db().property_expenses.aggregate(exp_pipeline).to_list(1)
        total_expenses = exp_data[0]["total"] if exp_data else 0
        expense_count = exp_data[0]["count"] if exp_data else 0

        net_profit = total_revenue - total_expenses
        roi = ((net_profit / total_expenses) * 100) if total_expenses > 0 else 0

        performance.append({
            "property_id": pid,
            "property_number": prop.get('property_number', ''),
            "address": prop.get('address', ''),
            "status": prop.get('status', 'available'),
            "rent_amount": prop.get('rent_amount', 0),
            "total_revenue": total_revenue,
            "payment_count": payment_count,
            "total_expenses": total_expenses,
            "expense_count": expense_count,
            "net_profit": net_profit,
            "roi_percent": round(roi, 1),
        })

    # Sort by net profit descending
    performance.sort(key=lambda x: x['net_profit'], reverse=True)

    return {
        "performance": performance,
        "summary": {
            "total_revenue": sum(p['total_revenue'] for p in performance),
            "total_expenses": sum(p['total_expenses'] for p in performance),
            "net_profit": sum(p['net_profit'] for p in performance),
            "property_count": len(performance),
        }
    }





@router.get('/admin/property-report/data')
async def get_report_data(request: Request):
    """Get complete data for bank report / business plan"""
    user = await auth_admin(request)
    now = datetime.utcnow()

    # All properties
    props = []
    async for p in get_db().properties.find().sort("property_number", 1):
        p['_id'] = str(p['_id'])
        props.append(p)

    # All active contracts with tenant info
    contracts = []
    async for c in get_db().rental_contracts.find({"status": "active"}).sort("start_date", -1):
        c['_id'] = str(c['_id'])
        contracts.append(c)

    # All tenants
    tenants = []
    async for t in get_db().tenants.find({"status": "active"}).sort("name", 1):
        t['_id'] = str(t['_id'])
        tenants.append(t)

    # Revenue by property
    rev_pipeline = [
        {"$match": {"status": "completed"}},
        {"$group": {"_id": "$property_id", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    rev_data = {r['_id']: r for r in await get_db().rental_payments.aggregate(rev_pipeline).to_list(100)}

    # Expenses by property
    exp_pipeline = [
        {"$match": {"status": {"$ne": "cancelled"}}},
        {"$group": {"_id": "$property_id", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    exp_data = {e['_id']: e for e in await get_db().property_expenses.aggregate(exp_pipeline).to_list(100)}

    # Expenses by category
    cat_pipeline = [
        {"$match": {"status": {"$ne": "cancelled"}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
        {"$sort": {"total": -1}},
    ]
    expense_categories = await get_db().property_expenses.aggregate(cat_pipeline).to_list(20)

    # Monthly revenue trend (12 months)
    monthly_trend = []
    for i in range(11, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        m_start = datetime(y, m, 1)
        m_end = datetime(y, m + 1, 1) if m < 12 else datetime(y + 1, 1, 1)

        rev_pipe = [
            {"$match": {"payment_date": {"$gte": m_start, "$lt": m_end}, "status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        rev = await get_db().rental_payments.aggregate(rev_pipe).to_list(1)

        exp_pipe = [
            {"$match": {"expense_date": {"$gte": m_start.strftime('%Y-%m-%d'), "$lt": m_end.strftime('%Y-%m-%d')}, "status": {"$ne": "cancelled"}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        exp = await get_db().property_expenses.aggregate(exp_pipe).to_list(1)

        monthly_trend.append({
            "month": m_start.strftime('%b %Y'),
            "revenue": rev[0]['total'] if rev else 0,
            "expenses": exp[0]['total'] if exp else 0,
            "net": (rev[0]['total'] if rev else 0) - (exp[0]['total'] if exp else 0),
        })

    # Build property details with financials
    property_details = []
    total_revenue = 0
    total_expenses = 0
    total_rent = 0

    for p in props:
        pid = p['_id']
        r = rev_data.get(pid, {"total": 0, "count": 0})
        e = exp_data.get(pid, {"total": 0, "count": 0})
        net = r['total'] - e['total']

        # Find active contract for this property
        contract = next((c for c in contracts if c.get('property_id') == pid), None)

        property_details.append({
            "property_number": p.get('property_number', ''),
            "address": p.get('address', ''),
            "status": p.get('status', 'available'),
            "rent_amount": p.get('rent_amount', 0),
            "deposit_amount": p.get('deposit_amount', 0),
            "total_revenue": r['total'],
            "total_expenses": e['total'],
            "net_income": net,
            "payment_count": r['count'],
            "expense_count": e['count'],
            "tenant_name": contract.get('tenant_name', '') if contract else '',
            "lease_start": contract.get('start_date', '') if contract else '',
            "lease_end": contract.get('end_date', '') if contract else '',
        })

        total_revenue += r['total']
        total_expenses += e['total']
        total_rent += p.get('rent_amount', 0)

    total_properties = len(props)
    rented_count = len([p for p in props if p.get('status') == 'rented'])
    occupancy_rate = (rented_count / total_properties * 100) if total_properties > 0 else 0
    annual_gross_income = total_rent * 12
    annual_expenses = total_expenses
    noi = annual_gross_income - annual_expenses
    estimated_value = noi * 10 if noi > 0 else total_rent * 120
    cap_rate = (noi / estimated_value * 100) if estimated_value > 0 else 0
    dscr = noi / (annual_gross_income * 0.06) if annual_gross_income > 0 else 0  # Assume 6% debt service

    return {
        "report_date": now.strftime('%Y-%m-%d'),
        "company_name": "Ross Property Management",
        "summary": {
            "total_properties": total_properties,
            "rented_properties": rented_count,
            "available_properties": total_properties - rented_count,
            "occupancy_rate": round(occupancy_rate, 1),
            "total_tenants": len(tenants),
            "active_contracts": len(contracts),
            "monthly_rental_income": total_rent,
            "annual_gross_income": annual_gross_income,
            "annual_expenses": annual_expenses,
            "net_operating_income": noi,
            "estimated_portfolio_value": estimated_value,
            "cap_rate": round(cap_rate, 2),
            "dscr": round(dscr, 2),
            "total_revenue_collected": total_revenue,
            "total_expenses_paid": total_expenses,
            "net_profit": total_revenue - total_expenses,
        },
        "properties": property_details,
        "rent_roll": [{
            "tenant": c.get('tenant_name', ''),
            "property": c.get('property_address', ''),
            "unit": c.get('property_number', ''),
            "rent": c.get('rent_amount', 0),
            "deposit": c.get('deposit_amount', 0),
            "lease_start": c.get('start_date', ''),
            "lease_end": c.get('end_date', ''),
            "status": 'Activo',
        } for c in contracts],
        "expense_breakdown": [{
            "category": EXPENSE_CATEGORIES.get(c['_id'] or 'other', c['_id'] or 'Otro'),
            "total": c['total'],
            "count": c['count'],
            "percent": round(c['total'] / total_expenses * 100, 1) if total_expenses > 0 else 0,
        } for c in expense_categories],
        "monthly_trend": monthly_trend,
    }



@router.get('/admin/overdue-payments')
async def get_overdue_payments(request: Request):
    """Get tenants with overdue/late payments"""
    user = await auth_admin(request)
    now = datetime.utcnow()

    # Get active contracts
    overdue = []
    async for c in get_db().rental_contracts.find({"status": "active"}):
        cid = str(c['_id'])
        payment_due_day = c.get('payment_due_day', 1)
        grace_days = c.get('late_fee_grace_days', 5)
        rent_amount = c.get('rent_amount', 0)

        # Check if current month payment exists
        month_start = datetime(now.year, now.month, 1)
        next_month = datetime(now.year, now.month + 1, 1) if now.month < 12 else datetime(now.year + 1, 1, 1)

        payment = await get_db().rental_payments.find_one({
            "contract_id": cid,
            "payment_date": {"$gte": month_start, "$lt": next_month},
            "status": "completed",
        })

        if not payment and now.day > payment_due_day + grace_days:
            days_late = now.day - payment_due_day
            overdue.append({
                "contract_id": cid,
                "contract_number": c.get('contract_number', ''),
                "tenant_name": c.get('tenant_name', ''),
                "tenant_phone": c.get('tenant_phone', ''),
                "property_address": c.get('property_address', ''),
                "property_number": c.get('property_number', ''),
                "rent_amount": rent_amount,
                "due_day": payment_due_day,
                "days_late": days_late,
                "late_fee": c.get('late_fee_amount', 0),
                "total_owed": rent_amount + c.get('late_fee_amount', 0),
            })

    overdue.sort(key=lambda x: x['days_late'], reverse=True)
    return {"overdue": overdue, "count": len(overdue)}


@router.get('/admin/contract-calendar')
async def get_contract_calendar(request: Request):
    """Get contract expirations and important dates"""
    user = await auth_admin(request)
    now = datetime.utcnow()

    events = []

    # Upcoming expirations (next 180 days)
    six_months = (now + timedelta(days=180)).strftime('%Y-%m-%d')
    async for c in get_db().rental_contracts.find({
        "status": "active",
        "end_date": {"$lte": six_months, "$gte": now.strftime('%Y-%m-%d')}
    }).sort("end_date", 1):
        end = c.get('end_date', '')
        days_left = (datetime.strptime(end, '%Y-%m-%d') - now).days if end else 999
        urgency = 'critical' if days_left < 30 else 'warning' if days_left < 60 else 'info'
        events.append({
            "type": "expiration",
            "date": end,
            "days_left": days_left,
            "urgency": urgency,
            "title": f"Vence: {c.get('tenant_name', '')}",
            "subtitle": c.get('property_address', ''),
            "contract_number": c.get('contract_number', ''),
            "rent_amount": c.get('rent_amount', 0),
        })

    # Draft contracts pending activation
    async for c in get_db().rental_contracts.find({"status": "draft"}).sort("created_at", -1).limit(10):
        events.append({
            "type": "draft",
            "date": c.get('start_date', ''),
            "days_left": None,
            "urgency": "info",
            "title": f"Borrador: {c.get('tenant_name', '')}",
            "subtitle": c.get('property_address', ''),
            "contract_number": c.get('contract_number', ''),
            "rent_amount": c.get('rent_amount', 0),
        })

    return {"events": events}


@router.get('/admin/maintenance-dashboard')
async def get_maintenance_dashboard(request: Request):
    """Get maintenance overview"""
    user = await auth_admin(request)
    now = datetime.utcnow()

    # Properties in maintenance
    maint_props = []
    async for p in get_db().properties.find({"status": "maintenance"}):
        p['_id'] = str(p['_id'])
        # Get recent maintenance expenses
        expenses = []
        async for e in get_db().property_expenses.find({
            "property_id": p['_id'],
            "category": {"$in": ["maintenance", "repair", "appliance", "cleaning", "landscaping"]}
        }).sort("expense_date", -1).limit(5):
            e['_id'] = str(e['_id'])
            expenses.append(e)
        maint_props.append({**p, "recent_expenses": expenses})

    # Recent maintenance expenses (all properties)
    recent_maint = []
    async for e in get_db().property_expenses.find({
        "category": {"$in": ["maintenance", "repair", "appliance", "cleaning", "landscaping"]}
    }).sort("expense_date", -1).limit(10):
        e['_id'] = str(e['_id'])
        recent_maint.append(e)

    # Maintenance spend by month (last 6 months)
    monthly_maint = []
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        m_start = datetime(y, m, 1).strftime('%Y-%m-%d')
        m_end = (datetime(y, m + 1, 1) if m < 12 else datetime(y + 1, 1, 1)).strftime('%Y-%m-%d')

        pipe = [
            {"$match": {"expense_date": {"$gte": m_start, "$lt": m_end}, "category": {"$in": ["maintenance", "repair", "appliance", "cleaning", "landscaping"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}}
        ]
        result = await get_db().property_expenses.aggregate(pipe).to_list(1)
        monthly_maint.append({
            "month": datetime(y, m, 1).strftime('%b %Y'),
            "total": result[0]['total'] if result else 0,
            "count": result[0]['count'] if result else 0,
        })

    return {
        "maintenance_properties": maint_props,
        "recent_maintenance": recent_maint,
        "monthly_trend": monthly_maint,
        "total_in_maintenance": len(maint_props),
    }


# ==================== ADMIN RENTAL APPLICATIONS ====================

APPLICATION_STATUSES = {"new", "reviewing", "approved", "rejected", "archived"}


def _serialize_application(a: dict) -> dict:
    return {
        "id": str(a["_id"]),
        "name": a.get("name", ""),
        "email": a.get("email", ""),
        "phone": a.get("phone", ""),
        "property_interest": a.get("property_interest", ""),
        "employment": a.get("employment", ""),
        "monthly_income": a.get("monthly_income", ""),
        "message": a.get("message", ""),
        "status": a.get("status", "new"),
        "admin_notes": a.get("admin_notes", ""),
        "source": a.get("source", "website"),
        "created_at": a.get("created_at", "").isoformat() if a.get("created_at") else "",
        "updated_at": a.get("updated_at", "").isoformat() if a.get("updated_at") else "",
        "reviewed_by": a.get("reviewed_by", ""),
    }


@router.get('/admin/rental-applications')
async def list_rental_applications(
    request: Request,
    status: str = "",
    search: str = "",
    page: int = 1,
    limit: int = 50,
):
    """Admin: List rental applications with filters + stats"""
    user = await auth_admin(request)
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 50), 200))

    query: dict = {}
    if status:
        query["status"] = status
    if search:
        import re as _re
        rg = {"$regex": _re.escape(search), "$options": "i"}
        query["$or"] = [
            {"name": rg}, {"email": rg}, {"phone": rg},
            {"property_interest": rg}, {"message": rg}, {"employment": rg},
        ]

    db = get_db()
    total = await db.rental_applications.count_documents(query)
    total_pages = max(1, (total + limit - 1) // limit)
    page = min(page, total_pages) if total > 0 else 1
    skip = (page - 1) * limit
    cursor = db.rental_applications.find(query).sort("created_at", -1).skip(skip).limit(limit)

    apps = [_serialize_application(a) async for a in cursor]

    # Stats breakdown (across all applications, ignoring filters except status)
    stats = {s: 0 for s in APPLICATION_STATUSES}
    stats["total"] = 0
    try:
        async for d in db.rental_applications.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]):
            k = (d.get("_id") or "new").lower()
            if k in stats:
                stats[k] = d.get("count", 0)
            stats["total"] += d.get("count", 0)
    except Exception:
        pass

    return {
        "success": True,
        "applications": apps,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "stats": stats,
    }


@router.get('/admin/rental-applications/{app_id}')
async def get_rental_application(app_id: str, request: Request):
    """Admin: Fetch a single rental application"""
    user = await auth_admin(request)
    if not ObjectId.is_valid(app_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    a = await get_db().rental_applications.find_one({"_id": ObjectId(app_id)})
    if not a:
        raise HTTPException(status_code=404, detail="Aplicación no encontrada")
    return {"success": True, "application": _serialize_application(a)}


@router.patch('/admin/rental-applications/{app_id}')
async def update_rental_application(app_id: str, request: Request):
    """Admin: Update status / notes of a rental application"""
    user = await auth_admin(request)
    if not ObjectId.is_valid(app_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    data = await request.json()

    update_fields = {"updated_at": datetime.utcnow()}
    if "status" in data:
        new_status = (data.get("status") or "").lower().strip()
        if new_status not in APPLICATION_STATUSES:
            raise HTTPException(status_code=400, detail=f"Status inválido. Use: {sorted(APPLICATION_STATUSES)}")
        update_fields["status"] = new_status
        update_fields["reviewed_by"] = user.get("email") or user.get("name") or ""
        update_fields["reviewed_at"] = datetime.utcnow()
    if "admin_notes" in data:
        update_fields["admin_notes"] = (data.get("admin_notes") or "").strip()

    if len(update_fields) == 1:  # only updated_at
        raise HTTPException(status_code=400, detail="No hay cambios a aplicar")

    result = await get_db().rental_applications.update_one(
        {"_id": ObjectId(app_id)}, {"$set": update_fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Aplicación no encontrada")

    a = await get_db().rental_applications.find_one({"_id": ObjectId(app_id)})
    return {"success": True, "application": _serialize_application(a)}


@router.delete('/admin/rental-applications/{app_id}')
async def delete_rental_application(app_id: str, request: Request):
    """Admin: Delete a rental application"""
    user = await auth_admin(request)
    if not ObjectId.is_valid(app_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    result = await get_db().rental_applications.delete_one({"_id": ObjectId(app_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Aplicación no encontrada")
    return {"success": True, "message": "Aplicación eliminada"}


