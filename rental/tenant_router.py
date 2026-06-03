"""
Rental Tenant Router
=====================
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import jwt
import hashlib
from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

router = APIRouter()

@router.post('/tenant/login')
async def tenant_login(request: Request):
    """Tenant login with email + phone number"""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    phone = body.get("phone", "").strip().replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    
    if not email or not phone:
        raise HTTPException(status_code=400, detail="Email y teléfono son requeridos")
    
    # Find tenant by email
    tenant = await get_db().tenants.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not tenant:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    
    # Verify phone (last 4 digits or full match)
    stored_phone = tenant.get("phone", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    if phone != stored_phone and phone != stored_phone[-4:]:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    
    tenant_id = str(tenant["_id"])
    token = create_tenant_token(tenant_id, email)
    
    return {
        "success": True,
        "token": token,
        "tenant": {
            "id": tenant_id,
            "name": tenant.get("name", ""),
            "email": tenant.get("email", ""),
            "tenant_number": tenant.get("tenant_number", ""),
        }
    }


@router.get('/tenant/dashboard')
async def tenant_dashboard(request: Request):
    """Get tenant dashboard: contract, payments, next due date"""
    tenant = await auth_tenant(request)
    tenant_id = tenant["_id"]
    
    # Get active contract
    contract = await get_db().rental_contracts.find_one({
        "tenant_id": tenant_id,
        "status": "active"
    })
    
    contract_data = None
    next_payment = None
    
    if contract:
        contract_data = {
            "id": str(contract["_id"]),
            "contract_number": contract.get("contract_number", ""),
            "property_address": contract.get("property_address", ""),
            "start_date": str(contract.get("start_date", "")),
            "end_date": str(contract.get("end_date", "")),
            "rent_amount": contract.get("rent_amount", 0),
            "deposit_amount": contract.get("deposit_amount", 0),
            "payment_due_day": contract.get("payment_due_day", 1),
            "late_fee_amount": contract.get("late_fee_amount", 0),
            "late_fee_grace_days": contract.get("late_fee_grace_days", 5),
            "status": contract.get("status", ""),
        }
        
        # Calculate next payment date
        today = datetime.utcnow()
        due_day = contract.get("payment_due_day", 1)
        if today.day > due_day:
            # Next month
            if today.month == 12:
                next_due = datetime(today.year + 1, 1, due_day)
            else:
                next_due = datetime(today.year, today.month + 1, due_day)
        else:
            next_due = datetime(today.year, today.month, due_day)
        
        # Check if this month is already paid
        current_month_paid = await get_db().rental_payments.find_one({
            "contract_id": str(contract["_id"]),
            "period_month": today.strftime("%B").lower(),
            "period_year": today.year,
            "status": "completed"
        })
        
        next_payment = {
            "due_date": next_due.strftime("%Y-%m-%d"),
            "amount": contract.get("rent_amount", 0),
            "current_month_paid": bool(current_month_paid),
        }
    
    # Get payment history
    payments = []
    cursor = get_db().rental_payments.find({"tenant_id": tenant_id}).sort("payment_date", -1).limit(24)
    async for p in cursor:
        payments.append({
            "id": str(p["_id"]),
            "receipt_number": p.get("receipt_number", ""),
            "amount": p.get("amount", 0),
            "late_fee": p.get("late_fee", 0),
            "total_paid": p.get("total_paid", 0),
            "payment_method": p.get("payment_method", ""),
            "period_month": p.get("period_month", ""),
            "period_year": p.get("period_year", 0),
            "payment_date": str(p.get("payment_date", "")),
            "status": p.get("status", ""),
        })
    
    # Get property info
    property_data = None
    if tenant.get("current_property_id") and tenant["current_property_id"] != "None":
        try:
            prop = await get_db().properties.find_one({"_id": ObjectId(tenant["current_property_id"])})
            if prop:
                property_data = {
                    "address": prop.get("address", ""),
                    "city": prop.get("city", ""),
                    "state": prop.get("state", ""),
                    "bedrooms": prop.get("bedrooms", 0),
                    "bathrooms": prop.get("bathrooms", 0),
                }
        except Exception:
            pass
    
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



@router.get('/tenant/payment/{payment_id}/receipt')
async def tenant_payment_receipt(payment_id: str, request: Request):
    """Generate PDF receipt for a specific payment (tenant-authenticated)"""
    tenant = await auth_tenant(request)
    tenant_id = tenant["_id"]

    try:
        payment = await get_db().rental_payments.find_one({"_id": ObjectId(payment_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payment ID")

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Security: ensure this payment belongs to the authenticated tenant
    if payment.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Fetch contract info
    contract = None
    if payment.get("contract_id"):
        try:
            contract = await get_db().rental_contracts.find_one({"_id": ObjectId(payment["contract_id"])})
        except Exception:
            pass

    from rental_pdf_service import generate_rental_receipt_pdf
    pdf_b64 = generate_rental_receipt_pdf(
        payment=serialize(payment),
        contract=serialize(contract) if contract else None,
        tenant=serialize(tenant),
    )

    receipt_num = payment.get('receipt_number', payment_id)
    filename = f"Receipt_{receipt_num}.pdf"

    return {"success": True, "pdf_base64": pdf_b64, "filename": filename}






# ═══════════════════════════════════════════════════════════════════════════════
# TENANTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get('/admin/tenants')
async def list_tenants(request: Request):
    """List all tenants"""
    user = await auth_admin(request)
    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    search = params.get('search', [''])[0]
    status_filter = params.get('status', [None])[0]

    query = {}
    if status_filter:
        query['status'] = status_filter
    if search:
        query['$or'] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"tenant_number": {"$regex": search, "$options": "i"}},
        ]

    cursor = get_db().tenants.find(query).sort("created_at", -1)
    tenants = []
    async for t in cursor:
        # Get current property name
        if t.get('current_property_id'):
            prop = await get_db().properties.find_one({"_id": ObjectId(t['current_property_id'])})
            t['current_property_address'] = prop.get('address', '') if prop else ''
        else:
            t['current_property_address'] = ''
        tenants.append(serialize(t))

    return {"success": True, "tenants": tenants, "count": len(tenants)}


@router.post('/admin/tenants')
async def create_tenant(request: Request):
    """Create a new tenant"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    count = await get_db().tenants.count_documents({})
    tenant_number = f"INQ-{now.year}-{str(count + 1).zfill(3)}"

    tenant_doc = {
        "tenant_number": tenant_number,
        "name": data.get('name', ''),
        "email": data.get('email', ''),
        "phone": data.get('phone', ''),
        "ssn_last4": data.get('ssn_last4', ''),
        "id_type": data.get('id_type', ''),  # passport, drivers_license, state_id, itin
        "id_number": data.get('id_number', ''),
        "emergency_contact_name": data.get('emergency_contact_name', ''),
        "emergency_contact_phone": data.get('emergency_contact_phone', ''),
        "employer": data.get('employer', ''),
        "monthly_income": float(data.get('monthly_income', 0)),
        "current_property_id": data.get('current_property_id', None),
        "status": data.get('status', 'active'),  # active, inactive, evicted
        "rental_history": [],
        "notes": data.get('notes', ''),
        "created_at": now,
        "updated_at": now,
        "created_by": user.get('email', 'admin'),
    }

    result = await get_db().tenants.insert_one(tenant_doc)
    return {
        "success": True,
        "message": f"Inquilino {tenant_number} creado",
        "tenant_id": str(result.inserted_id),
        "tenant_number": tenant_number,
    }


@router.put('/admin/tenants/{tenant_id}')
async def update_tenant(tenant_id: str, request: Request):
    """Update a tenant"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    tenant = await get_db().tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    update_fields = {}
    allowed = ['name', 'email', 'phone', 'ssn_last4', 'id_type', 'id_number',
               'emergency_contact_name', 'emergency_contact_phone', 'employer',
               'monthly_income', 'status', 'notes']
    for field in allowed:
        if field in data:
            if field == 'monthly_income':
                update_fields[field] = float(data[field])
            else:
                update_fields[field] = data[field]
    update_fields['updated_at'] = now

    await get_db().tenants.update_one({"_id": ObjectId(tenant_id)}, {"$set": update_fields})
    return {"success": True, "message": "Inquilino actualizado"}


@router.delete('/admin/tenants/{tenant_id}')
async def delete_tenant(tenant_id: str, request: Request):
    """Delete a tenant"""
    user = await auth_admin(request)
    tenant = await get_db().tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    # Check if tenant has active contracts
    active_contract = await get_db().rental_contracts.find_one({"tenant_id": tenant_id, "status": "active"})
    if active_contract:
        from urllib.parse import parse_qs
        params = parse_qs(str(request.url.query))
        force = params.get('force', ['false'])[0].lower() == 'true'
        if not force:
            raise HTTPException(status_code=400, detail="El inquilino tiene un contrato activo. Use ?force=true")

    await get_db().tenants.delete_one({"_id": ObjectId(tenant_id)})
    return {"success": True, "message": f"Inquilino {tenant.get('tenant_number', '')} eliminado"}




# ==================== TENANT MAINTENANCE REQUESTS ====================

@router.post('/tenant/maintenance-request')
async def create_maintenance_request(request: Request):
    """Tenant: Submit a maintenance request"""
    tenant = await auth_tenant(request)
    data = await request.json()
    
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    
    if not title or not description:
        raise HTTPException(status_code=400, detail="Título y descripción son requeridos")
    
    maintenance = {
        "tenant_id": tenant["_id"],
        "tenant_name": tenant.get("name", ""),
        "tenant_email": tenant.get("email", ""),
        "property_id": tenant.get("current_property_id", ""),
        "title": title,
        "description": description,
        "category": data.get("category", "general"),
        "priority": data.get("priority", "normal"),
        "status": "open",
        "photos": data.get("photos", []),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    
    result = await get_db().maintenance_requests.insert_one(maintenance)
    
    # ── Send push notifications to admins and property owner ──
    try:
        await send_rental_push_to_admins(
            title="🔧 Nueva Solicitud de Mantenimiento",
            body=f"{tenant.get('name', 'Inquilino')}: {title}",
            data={"type": "maintenance_new", "request_id": str(result.inserted_id)}
        )
        # Notify property owner if property has one
        property_id = tenant.get("current_property_id", "")
        if property_id:
            prop = await get_db().properties.find_one({"_id": ObjectId(property_id)}) if ObjectId.is_valid(property_id) else None
            if prop and prop.get("owner_id"):
                await send_rental_push_to_user(
                    user_id=prop["owner_id"],
                    title="🔧 Solicitud de Mantenimiento",
                    body=f"{tenant.get('name', 'Inquilino')}: {title}",
                    data={"type": "maintenance_new", "request_id": str(result.inserted_id)}
                )
    except Exception as e:
        logging.warning(f"⚠️ Push notification error (maintenance create): {e}")
    
    return {
        "success": True,
        "message": "Solicitud de mantenimiento creada",
        "request_id": str(result.inserted_id),
    }


@router.get('/tenant/maintenance-requests')
async def list_tenant_maintenance_requests(request: Request):
    """Tenant: List their maintenance requests"""
    tenant = await auth_tenant(request)
    
    cursor = get_db().maintenance_requests.find(
        {"tenant_id": tenant["_id"]}
    ).sort("created_at", -1).limit(50)
    
    requests_list = []
    async for r in cursor:
        requests_list.append({
            "id": str(r["_id"]),
            "title": r.get("title", ""),
            "description": r.get("description", ""),
            "category": r.get("category", ""),
            "priority": r.get("priority", ""),
            "status": r.get("status", ""),
            "created_at": r.get("created_at", "").isoformat() if r.get("created_at") else "",
            "updated_at": r.get("updated_at", "").isoformat() if r.get("updated_at") else "",
        })
    
    return {"success": True, "requests": requests_list}


@router.get('/admin/maintenance-requests')
async def admin_list_maintenance_requests(
    request: Request,
    status: str = "",
    page: int = 1,
    limit: int = 50,
):
    """Admin: List all maintenance requests"""
    user = await auth_admin(request)
    query = {}
    if status:
        query["status"] = status
    
    total = await get_db().maintenance_requests.count_documents(query)
    skip = (page - 1) * limit
    cursor = get_db().maintenance_requests.find(query).sort("created_at", -1).skip(skip).limit(limit)
    
    requests_list = []
    async for r in cursor:
        requests_list.append({
            "id": str(r["_id"]),
            "tenant_id": r.get("tenant_id", ""),
            "tenant_name": r.get("tenant_name", ""),
            "tenant_email": r.get("tenant_email", ""),
            "property_id": r.get("property_id", ""),
            "title": r.get("title", ""),
            "description": r.get("description", ""),
            "category": r.get("category", ""),
            "priority": r.get("priority", ""),
            "status": r.get("status", "open"),
            "created_at": r.get("created_at", "").isoformat() if r.get("created_at") else "",
        })
    
    return {"success": True, "requests": requests_list, "total": total}


@router.put('/admin/maintenance-requests/{request_id}')
async def update_maintenance_request(request_id: str, request: Request):
    """Admin: Update maintenance request status"""
    user = await auth_admin(request)
    data = await request.json()
    
    update_fields = {"updated_at": datetime.utcnow()}
    if "status" in data:
        update_fields["status"] = data["status"]
    if "admin_notes" in data:
        update_fields["admin_notes"] = data["admin_notes"]
    if "assigned_to" in data:
        update_fields["assigned_to"] = data["assigned_to"]
    
    # Get the request before updating (for notification)
    maint_req = await get_db().maintenance_requests.find_one({"_id": ObjectId(request_id)})
    
    result = await get_db().maintenance_requests.update_one(
        {"_id": ObjectId(request_id)},
        {"$set": update_fields}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    
    # ── Send push notification to tenant about status change ──
    if maint_req and "status" in data:
        status_labels = {
            "open": "Abierta",
            "in_progress": "En Progreso",
            "resolved": "Resuelta",
            "closed": "Cerrada",
        }
        status_label = status_labels.get(data["status"], data["status"])
        try:
            tenant_id = maint_req.get("tenant_id", "")
            if tenant_id:
                await send_rental_push_to_user(
                    user_id=tenant_id,
                    title="📋 Actualización de Mantenimiento",
                    body=f"Tu solicitud '{maint_req.get('title', '')}' está ahora: {status_label}",
                    data={"type": "maintenance_update", "request_id": request_id, "status": data["status"]}
                )
            # Also notify owner
            property_id = maint_req.get("property_id", "")
            if property_id and ObjectId.is_valid(property_id):
                prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
                if prop and prop.get("owner_id"):
                    await send_rental_push_to_user(
                        user_id=prop["owner_id"],
                        title="📋 Mantenimiento Actualizado",
                        body=f"'{maint_req.get('title', '')}' → {status_label}",
                        data={"type": "maintenance_update", "request_id": request_id, "status": data["status"]}
                    )
        except Exception as e:
            logging.warning(f"⚠️ Push notification error (maintenance update): {e}")
    
    return {"success": True, "message": "Solicitud actualizada"}






# ==================== TENANT ONLINE PAYMENT ====================

@router.get('/tenant/payment-config')
async def get_tenant_payment_config(request: Request):
    """Tenant: Get payment configuration and methods"""
    tenant = await auth_tenant(request)
    
    # Get rental config for payment methods
    config = await get_db().rental_config.find_one({}) or {}
    
    # Get contract for amount due
    contract = await get_db().rental_contracts.find_one({
        "tenant_id": tenant["_id"],
        "status": "active"
    })
    
    # Check if current month is paid
    now = datetime.utcnow()
    current_month = now.strftime('%B').lower()
    current_year = now.year
    
    current_paid = False
    if contract:
        existing = await get_db().rental_payments.find_one({
            "contract_id": str(contract["_id"]),
            "period_month": {"$regex": f"^{current_month[:3]}", "$options": "i"},
            "period_year": current_year,
            "status": {"$in": ["completed", "paid", "pending_verification"]}
        })
        current_paid = existing is not None
    
    payment_methods = config.get("payment_methods", {})
    
    return {
        "success": True,
        "stripe_enabled": bool(config.get("stripe_enabled", False) and config.get("stripe_secret_key")),
        "rent_amount": contract.get("rent_amount", 0) if contract else 0,
        "late_fee": contract.get("late_fee_amount", 0) if contract else 0,
        "due_day": contract.get("payment_due_day", 1) if contract else 1,
        "current_month_paid": current_paid,
        "current_month": now.strftime('%B %Y'),
        "contract_id": str(contract["_id"]) if contract else None,
        "payment_methods": {
            "zelle": payment_methods.get("zelle", {
                "enabled": True,
                "email": "rosshouserentals@gmail.com",
                "phone": "(806) 934-2018",
                "name": "Ross House Rentals LLC"
            }),
            "cashapp": payment_methods.get("cashapp", {
                "enabled": True,
                "tag": "$RossHouseRentals"
            }),
            "bank_transfer": payment_methods.get("bank_transfer", {
                "enabled": True,
                "bank_name": "Contact office for details",
                "account_name": "Ross House Rentals LLC"
            }),
            "money_order": payment_methods.get("money_order", {
                "enabled": True,
                "address": "305 Bruce Ave, Dumas, TX 79029",
                "payable_to": "Ross House Rentals LLC"
            })
        }
    }


@router.post('/tenant/submit-payment')
async def tenant_submit_payment(request: Request):
    """Tenant: Submit a payment confirmation for verification"""
    tenant = await auth_tenant(request)
    data = await request.json()
    
    payment_method = data.get("payment_method", "").strip()
    reference_number = data.get("reference_number", "").strip()
    amount = data.get("amount", 0)
    
    if not payment_method:
        raise HTTPException(status_code=400, detail="Método de pago requerido")
    if not amount or float(amount) <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")
    
    # Get active contract
    contract = await get_db().rental_contracts.find_one({
        "tenant_id": tenant["_id"],
        "status": "active"
    })
    
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")
    
    now = datetime.utcnow()
    
    # Check for duplicate submission this month
    current_month = now.strftime('%B').lower()
    existing = await get_db().rental_payments.find_one({
        "contract_id": str(contract["_id"]),
        "period_month": {"$regex": f"^{current_month[:3]}", "$options": "i"},
        "period_year": now.year,
        "status": {"$in": ["pending_verification", "completed", "paid"]}
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un pago registrado para este mes")
    
    # Create payment record
    receipt_number = f"REC-{now.strftime('%Y%m%d')}-{str(tenant['_id'])[-4:]}"
    
    payment = {
        "contract_id": str(contract["_id"]),
        "property_id": str(contract.get("property_id", "")),
        "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name", ""),
        "amount": float(amount),
        "late_fee": float(data.get("late_fee", 0)),
        "total_paid": float(amount) + float(data.get("late_fee", 0)),
        "payment_method": payment_method,
        "reference_number": reference_number,
        "notes": data.get("notes", ""),
        "receipt_number": receipt_number,
        "period_month": now.strftime('%B').lower(),
        "period_year": now.year,
        "payment_date": now.isoformat(),
        "status": "pending_verification",
        "submitted_by": "tenant",
        "submitted_at": now,
        "created_at": now,
        "updated_at": now,
    }
    
    result = await get_db().rental_payments.insert_one(payment)
    
    return {
        "success": True,
        "message": "Pago enviado para verificación. Recibirás confirmación pronto.",
        "payment_id": str(result.inserted_id),
        "receipt_number": receipt_number,
    }


@router.get('/tenant/payment-history')
async def tenant_payment_history(request: Request):
    """Tenant: Get detailed payment history"""
    tenant = await auth_tenant(request)
    
    # Get all contracts for this tenant
    contracts = await get_db().rental_contracts.find(
        {"tenant_id": tenant["_id"]}
    ).to_list(100)
    
    contract_ids = [str(c["_id"]) for c in contracts]
    
    if not contract_ids:
        return {"success": True, "payments": [], "total_paid": 0}
    
    cursor = get_db().rental_payments.find(
        {"contract_id": {"$in": contract_ids}}
    ).sort("created_at", -1).limit(100)
    
    payments = []
    total_paid = 0
    async for p in cursor:
        amt = p.get("total_paid") or p.get("amount", 0)
        if p.get("status") in ["completed", "paid"]:
            total_paid += amt
        payments.append({
            "id": str(p["_id"]),
            "receipt_number": p.get("receipt_number", ""),
            "amount": p.get("amount", 0),
            "late_fee": p.get("late_fee", 0),
            "total_paid": amt,
            "payment_method": p.get("payment_method", ""),
            "reference_number": p.get("reference_number", ""),
            "period_month": p.get("period_month", ""),
            "period_year": p.get("period_year", 0),
            "payment_date": p.get("payment_date", ""),
            "status": p.get("status", ""),
            "notes": p.get("notes", ""),
        })
    
    return {"success": True, "payments": payments, "total_paid": total_paid}


