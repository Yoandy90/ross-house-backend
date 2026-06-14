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
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"tenant_number": {"$regex": search, "$options": "i"}},
        ]

    cursor = get_db().tenants.find(query).sort("created_at", -1)
    tenants = []
    async for t in cursor:
        # Ensure first_name/last_name exist (backward compat with old 'name' field)
        if not t.get('first_name') and t.get('name'):
            parts = t['name'].split(' ', 1)
            t['first_name'] = parts[0]
            t['last_name'] = parts[1] if len(parts) > 1 else ''
        # Get current property name
        if t.get('current_property_id'):
            try:
                prop = await get_db().properties.find_one({"_id": ObjectId(t['current_property_id'])})
                t['current_property_address'] = prop.get('address', '') if prop else ''
            except:
                t['current_property_address'] = ''
        else:
            t['current_property_address'] = ''
        tenants.append(serialize(t))

    return {"success": True, "tenants": tenants, "count": len(tenants)}


@router.get('/admin/all-users')
async def list_all_users(request: Request):
    """List ALL registered users (tenants + marketplace app_users).
    Returns a unified list with source = 'tenant' | 'app_user' | 'linked'.
    Useful for admin to see everyone who has registered through any channel.
    """
    user = await auth_admin(request)
    db = get_db()

    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    search = params.get('search', [''])[0]

    # 1. All tenants (formal tenant records with optional app_user link)
    tenant_query = {}
    if search:
        tenant_query['$or'] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"tenant_number": {"$regex": search, "$options": "i"}},
        ]

    tenants = []
    tenant_app_ids = set()
    tenant_emails = set()
    tenant_phones = set()

    async for t in db.tenants.find(tenant_query).sort("created_at", -1):
        if t.get('app_user_id'):
            tenant_app_ids.add(str(t['app_user_id']))
        if t.get('email'):
            tenant_emails.add(t['email'].lower())
        if t.get('phone'):
            tenant_phones.add(t['phone'])
        # Determine if has app account
        has_app = bool(t.get('app_user_id'))
        if not has_app and t.get('email'):
            existing = await db.app_users.find_one({"email": {"$regex": f"^{t['email']}$", "$options": "i"}})
            if existing:
                has_app = True
                tenant_app_ids.add(str(existing['_id']))
        tenants.append({
            "id": str(t["_id"]),
            "source": "tenant",
            "tenant_number": t.get("tenant_number"),
            "name": t.get("name", ""),
            "first_name": t.get("first_name", ""),
            "last_name": t.get("last_name", ""),
            "email": t.get("email", ""),
            "phone": t.get("phone", ""),
            "photo_url": t.get("photo_url", ""),
            "status": t.get("status", "active"),
            "has_contract": bool(t.get("current_property_id")),
            "has_app_account": has_app,
            "current_property_id": t.get("current_property_id"),
            "created_at": t.get("created_at").isoformat() if t.get("created_at") else None,
        })

    # 2. app_users NOT yet linked to a tenant (registered via OTP, not yet a formal tenant)
    user_query = {"role": {"$ne": "admin"}}  # Don't list admin accounts in tenant view
    if search:
        user_query['$or'] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
        ]

    unlinked_users = []
    async for u in db.app_users.find(user_query).sort("created_at", -1):
        uid = str(u["_id"])
        u_email = (u.get("email") or "").lower()
        u_phone = u.get("phone") or ""
        # Skip if already represented in tenants list
        if uid in tenant_app_ids:
            continue
        if u_email and u_email in tenant_emails:
            continue
        if u_phone and u_phone in tenant_phones:
            continue
        unlinked_users.append({
            "id": uid,
            "source": "app_user",
            "tenant_number": None,
            "name": u.get("name", "") or f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
            "first_name": u.get("first_name", ""),
            "last_name": u.get("last_name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "photo_url": u.get("photo_url", "") or u.get("avatar_url", ""),
            "status": u.get("status", "active"),
            "has_contract": False,
            "has_app_account": True,
            "role": u.get("role", "tenant"),
            "profile_complete": u.get("profile_complete", False),
            "created_at": u.get("created_at").isoformat() if isinstance(u.get("created_at"), datetime) else u.get("created_at"),
        })

    return {
        "success": True,
        "users": tenants + unlinked_users,
        "summary": {
            "total": len(tenants) + len(unlinked_users),
            "tenants": len(tenants),
            "app_users_unlinked": len(unlinked_users),
        }
    }


@router.post('/admin/all-users/{user_id}/convert-to-tenant')
async def convert_app_user_to_tenant(user_id: str, request: Request):
    """Admin shortcut: take an app_user (registered via OTP without a contract)
    and create a formal tenant record for them.
    """
    admin = await auth_admin(request)
    db = get_db()

    try:
        u = await db.app_users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        u = None
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Don't duplicate
    if u.get('tenant_id'):
        existing = await db.tenants.find_one({"_id": ObjectId(u['tenant_id'])})
        if existing:
            return {"success": True, "message": "Ya es inquilino", "tenant_id": str(existing["_id"])}

    now = datetime.utcnow()
    count = await db.tenants.count_documents({})
    tenant_number = f"INQ-{now.year}-{str(count + 1).zfill(3)}"

    first_name = u.get("first_name") or (u.get("name", "").split(" ", 1)[0] if u.get("name") else "")
    last_name = u.get("last_name") or (u.get("name", "").split(" ", 1)[1] if u.get("name") and " " in u.get("name", "") else "")

    tenant_doc = {
        "tenant_number": tenant_number,
        "first_name": first_name,
        "last_name": last_name,
        "name": u.get("name", f"{first_name} {last_name}".strip()),
        "email": u.get("email", ""),
        "phone": u.get("phone", ""),
        "address": "",
        "photo_url": u.get("photo_url", "") or u.get("avatar_url", ""),
        "status": "active",
        "rental_history": [],
        "app_user_id": str(u["_id"]),
        "created_at": now,
        "updated_at": now,
        "created_by": admin.get("email", "admin"),
        "converted_from_app_user": True,
    }

    result = await db.tenants.insert_one(tenant_doc)
    tenant_id = str(result.inserted_id)

    # Link both ways
    await db.app_users.update_one(
        {"_id": u["_id"]},
        {"$set": {"tenant_id": tenant_id, "role": "tenant", "updated_at": now}}
    )

    return {
        "success": True,
        "message": f"Convertido a inquilino {tenant_number}",
        "tenant_id": tenant_id,
        "tenant_number": tenant_number,
    }


@router.post('/admin/tenants')
async def create_tenant(request: Request):
    """Create a new tenant + auto-create user account + send welcome email + SMS.
    Optional body fields:
      - password (str): If provided, admin sets the password. Otherwise a random 10-char
                        password is generated.
      - send_welcome (bool, default true): Send welcome email + SMS to the new tenant
    """
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    email = data.get('email', '').strip().lower()
    phone = data.get('phone', '').strip()
    custom_password = data.get('password', '').strip() if data.get('password') else None
    send_welcome = data.get('send_welcome', True)

    if not first_name or not last_name:
        raise HTTPException(status_code=400, detail="Nombre y apellido son requeridos")
    if not phone:
        raise HTTPException(status_code=400, detail="Teléfono es requerido")
    if custom_password and len(custom_password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    # Uniqueness checks across BOTH collections
    if email:
        import re as _re
        existing_t = await get_db().tenants.find_one({
            "email": {"$regex": f"^{_re.escape(email)}$", "$options": "i"}
        })
        if existing_t:
            raise HTTPException(status_code=400, detail=f"Este email ya está registrado para el inquilino {existing_t.get('tenant_number','?')}")
        existing_u = await get_db().app_users.find_one({
            "email": {"$regex": f"^{_re.escape(email)}$", "$options": "i"}
        })
        if existing_u and not existing_u.get('tenant_id'):
            # User exists in app_users but not yet linked — we'll link below
            pass
        elif existing_u and existing_u.get('tenant_id'):
            raise HTTPException(status_code=400, detail="Este email ya está en uso por otra cuenta")

    if phone:
        existing_t_p = await get_db().tenants.find_one({"phone": phone})
        if existing_t_p:
            raise HTTPException(status_code=400, detail=f"Este teléfono ya está registrado para el inquilino {existing_t_p.get('tenant_number','?')}")

    full_name = f"{first_name} {last_name}"

    count = await get_db().tenants.count_documents({})
    tenant_number = f"INQ-{now.year}-{str(count + 1).zfill(3)}"

    tenant_doc = {
        "tenant_number": tenant_number,
        "first_name": first_name,
        "last_name": last_name,
        "name": full_name,
        "email": email,
        "phone": phone,
        "address": data.get('address', ''),
        "photo_url": data.get('photo_url', ''),
        "ssn_last4": data.get('ssn_last4', ''),
        "id_type": data.get('id_type', ''),
        "id_number": data.get('id_number', ''),
        "emergency_contact": data.get('emergency_contact', ''),
        "emergency_phone": data.get('emergency_phone', ''),
        "employer": data.get('employer', ''),
        "monthly_income": float(data.get('monthly_income', 0) or 0),
        "current_property_id": data.get('current_property_id', None),
        "status": data.get('status', 'active'),
        "rental_history": [],
        "notes": data.get('notes', ''),
        "created_at": now,
        "updated_at": now,
        "created_by": user.get('email', 'admin'),
    }

    result = await get_db().tenants.insert_one(tenant_doc)
    tenant_id = str(result.inserted_id)

    # Auto-create user account if email is provided
    user_created = False
    generated_password = None
    if email:
        existing_user = await get_db().app_users.find_one({"email": email})
        if not existing_user:
            import random, string
            if custom_password:
                final_password = custom_password
            else:
                final_password = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            generated_password = final_password
            from rental.auth_router import hash_password
            app_user = {
                "name": full_name,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "password_hash": hash_password(final_password),
                "role": "tenant",
                "status": "active",
                "verified": True,
                "profile_complete": True,
                "tenant_id": tenant_id,
                "created_at": now,
                "updated_at": now,
                "created_from": "admin_panel",
            }
            user_result = await get_db().app_users.insert_one(app_user)
            tenant_doc['app_user_id'] = str(user_result.inserted_id)
            await get_db().tenants.update_one(
                {"_id": result.inserted_id},
                {"$set": {"app_user_id": str(user_result.inserted_id)}}
            )
            user_created = True
            logging.info(f"✅ Auto-created app user for tenant {tenant_number}: {email}")
        else:
            # Link existing user to tenant and update password if admin provided one
            update_fields = {"tenant_id": tenant_id, "updated_at": now}
            if custom_password:
                from rental.auth_router import hash_password
                update_fields["password_hash"] = hash_password(custom_password)
                generated_password = custom_password
            await get_db().app_users.update_one(
                {"_id": existing_user["_id"]},
                {"$set": update_fields}
            )
            await get_db().tenants.update_one(
                {"_id": result.inserted_id},
                {"$set": {"app_user_id": str(existing_user["_id"])}}
            )
            user_created = bool(custom_password)

    # ── Send welcome email + SMS ──
    email_sent = False
    sms_sent = False
    if send_welcome:
        if email and generated_password:
            try:
                await _send_welcome_email(email, full_name, generated_password)
                email_sent = True
            except Exception as e:
                logging.warning(f"Welcome email failed: {e}")
        if phone:
            try:
                await _send_welcome_sms(phone, full_name)
                sms_sent = True
            except Exception as e:
                logging.warning(f"Welcome SMS failed: {e}")

    return {
        "success": True,
        "message": f"Inquilino {tenant_number} creado",
        "tenant_id": tenant_id,
        "tenant_number": tenant_number,
        "user_created": user_created,
        "email_sent": email_sent,
        "sms_sent": sms_sent,
        "credentials": {
            "email": email,
            "password": generated_password,  # Returned ONCE so admin can show it/copy it
            "show_to_admin": bool(generated_password),
        } if generated_password else None,
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
    allowed = ['first_name', 'last_name', 'email', 'phone', 'address', 'photo_url', 'profile_photo_url',
               'ssn_last4', 'id_type', 'id_number', 'emergency_contact', 'emergency_phone',
               'employer', 'monthly_income', 'current_property_id', 'status', 'notes']
    for field in allowed:
        if field in data:
            if field == 'monthly_income':
                update_fields[field] = float(data[field] or 0)
            else:
                update_fields[field] = data[field]

    # Keep 'name' in sync
    if 'first_name' in data or 'last_name' in data:
        fn = data.get('first_name', tenant.get('first_name', ''))
        ln = data.get('last_name', tenant.get('last_name', ''))
        update_fields['name'] = f"{fn} {ln}".strip()

    update_fields['updated_at'] = now
    await get_db().tenants.update_one({"_id": ObjectId(tenant_id)}, {"$set": update_fields})
    return {"success": True, "message": "Inquilino actualizado"}


@router.post('/admin/tenants/{tenant_id}/photo')
async def upload_tenant_photo(tenant_id: str, request: Request):
    """Upload a tenant photo (base64 image from file upload or webcam)"""
    user = await auth_admin(request)
    data = await request.json()
    image_data = data.get('image_data', '')
    content_type = data.get('content_type', 'image/jpeg')

    if not image_data:
        raise HTTPException(status_code=400, detail="No image data provided")

    tenant = await get_db().tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    try:
        import base64, uuid
        from rental_storage_service import set_emergent_key, put_object, APP_NAME

        # Load storage key from DB
        config = await get_db().api_config.find_one({"_id": "main"})
        if config and config.get("EMERGENT_LLM_KEY"):
            set_emergent_key(config["EMERGENT_LLM_KEY"])

        # Clean base64
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]
        file_bytes = base64.b64decode(image_data)

        ext = 'jpg' if 'jpeg' in content_type else content_type.split('/')[-1]
        file_id = str(uuid.uuid4())
        path = f"{APP_NAME}/tenants/{tenant_id}/{file_id}.{ext}"

        result = put_object(path, file_bytes, content_type)
        storage_path = result.get("path", path)

        # Build public URL
        base_url = str(request.base_url).rstrip('/')
        fwd_host = request.headers.get('x-forwarded-host') or request.headers.get('host')
        fwd_proto = request.headers.get('x-forwarded-proto', 'https')
        if fwd_host:
            base_url = f"{fwd_proto}://{fwd_host}"
        clean = storage_path.replace(f"{APP_NAME}/", "") if storage_path.startswith(f"{APP_NAME}/") else storage_path
        photo_url = f"{base_url}/api/public/property-file/{clean}"

        # Update tenant
        await get_db().tenants.update_one(
            {"_id": ObjectId(tenant_id)},
            {"$set": {"photo_url": photo_url, "updated_at": datetime.utcnow()}}
        )

        return {"success": True, "photo_url": photo_url}
    except Exception as e:
        logging.error(f"Tenant photo upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Error al subir foto: {str(e)}")


@router.post('/admin/tenants/{tenant_id}/use-app-photo')
async def use_app_photo_as_official(tenant_id: str, request: Request):
    """Admin: Copy the tenant's self-uploaded app profile photo as the official (office) photo"""
    user = await auth_admin(request)
    tenant = await get_db().tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    profile_photo = tenant.get('profile_photo_url', '')
    if not profile_photo:
        raise HTTPException(status_code=400, detail="El inquilino no tiene foto de perfil en la app")

    await get_db().tenants.update_one(
        {"_id": ObjectId(tenant_id)},
        {"$set": {"photo_url": profile_photo, "updated_at": datetime.utcnow()}}
    )
    return {"success": True, "photo_url": profile_photo, "message": "Foto de la app copiada como foto oficial"}


@router.delete('/admin/tenants/{tenant_id}')
async def delete_tenant(tenant_id: str, request: Request):
    """Delete a tenant"""
    user = await auth_admin(request)
    tenant = await get_db().tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    active_contract = await get_db().rental_contracts.find_one({"tenant_id": tenant_id, "status": "active"})
    if active_contract:
        from urllib.parse import parse_qs
        params = parse_qs(str(request.url.query))
        force = params.get('force', ['false'])[0].lower() == 'true'
        if not force:
            raise HTTPException(status_code=400, detail="El inquilino tiene un contrato activo. Use ?force=true")

    await get_db().tenants.delete_one({"_id": ObjectId(tenant_id)})
    return {"success": True, "message": f"Inquilino {tenant.get('tenant_number', '')} eliminado"}


async def _send_welcome_email(email: str, name: str, password: str):
    """Send welcome email with login credentials to new tenant"""
    import os
    try:
        sendgrid_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')

        if not sendgrid_key:
            config = await get_db().api_config.find_one({"_id": "main"})
            if config:
                sendgrid_key = config.get('sendgrid_api_key') or config.get('SENDGRID_API_KEY')
                from_email = config.get('sendgrid_from_email', from_email)

        if not sendgrid_key:
            logging.warning(f"⚠️ No SendGrid key — skipping welcome email to {email}")
            return

        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To

        html = f"""
        <div style="font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 600px; margin: 0 auto; background: #0a0f1a; color: #e2e8f0; border-radius: 16px; overflow: hidden;">
            <div style="background: linear-gradient(135deg, #7c3aed, #4f46e5); padding: 32px; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 24px;">🏠 Ross House Rentals</h1>
                <p style="color: rgba(255,255,255,0.85); margin: 8px 0 0;">Bienvenido(a) a nuestra plataforma</p>
            </div>
            <div style="padding: 32px;">
                <p style="font-size: 16px;">Hola <strong>{name}</strong>,</p>
                <p>Tu cuenta ha sido creada exitosamente. Usa las siguientes credenciales para acceder a la aplicacion:</p>
                <div style="background: rgba(124,58,237,0.1); border: 1px solid rgba(124,58,237,0.3); border-radius: 12px; padding: 20px; margin: 20px 0;">
                    <p style="margin: 0 0 8px;"><strong>📧 Email:</strong> {email}</p>
                    <p style="margin: 0;"><strong>🔑 Contrasena:</strong> {password}</p>
                </div>
                <p style="color: #94a3b8; font-size: 14px;">Te recomendamos cambiar tu contrasena despues de iniciar sesion por primera vez.</p>
                <p style="color: #94a3b8; font-size: 14px;">Si tienes alguna pregunta, no dudes en contactarnos.</p>
                <hr style="border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 24px 0;" />
                <p style="text-align: center; color: #64748b; font-size: 12px;">Ross House Rentals LLC<br/>info@rosshouserentals.com</p>
            </div>
        </div>
        """

        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        mail = Mail(
            from_email=Email(from_email, "Ross House Rentals"),
            to_emails=To(email),
            subject=f"Bienvenido a Ross House Rentals - Tus credenciales de acceso",
            html_content=html,
        )
        response = sg.send(mail)
        logging.info(f"✅ Welcome email sent to {email} (status: {response.status_code})")
    except Exception as e:
        logging.error(f"❌ Failed to send welcome email to {email}: {e}")


async def _send_welcome_email_self_registered(email: str, name: str):
    """Welcome email for self-registered tenants (they chose their own password — do NOT email it back)."""
    import os
    try:
        sendgrid_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')

        if not sendgrid_key:
            config = await get_db().api_config.find_one({"_id": "main"})
            if config:
                sendgrid_key = config.get('sendgrid_api_key') or config.get('SENDGRID_API_KEY')
                from_email = config.get('sendgrid_from_email', from_email)

        if not sendgrid_key:
            logging.warning(f"⚠️ No SendGrid key — skipping welcome email to {email}")
            return

        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To

        html = f"""
        <div style="font-family: 'Segoe UI', Tahoma, sans-serif; max-width: 600px; margin: 0 auto; background: #0a0f1a; color: #e2e8f0; border-radius: 16px; overflow: hidden;">
            <div style="background: linear-gradient(135deg, #C8102E, #DC2626); padding: 32px; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 26px;">🏠 Ross House Rentals</h1>
                <p style="color: rgba(255,255,255,0.92); margin: 10px 0 0; font-size: 15px;">¡Bienvenido(a) a la familia!</p>
            </div>
            <div style="padding: 32px;">
                <p style="font-size: 18px; margin: 0 0 12px;">Hola <strong>{name}</strong> 👋,</p>
                <p style="font-size: 15px; line-height: 1.6;">¡Gracias por unirte a Ross House Rentals! Tu cuenta ha sido creada exitosamente y ya puedes acceder a todos nuestros servicios desde la app.</p>

                <div style="background: rgba(200,16,46,0.10); border-left: 4px solid #C8102E; padding: 16px 20px; margin: 24px 0; border-radius: 8px;">
                    <p style="margin: 0; font-weight: bold; color: #C8102E;">📧 Tu email de acceso:</p>
                    <p style="margin: 6px 0 0; font-size: 16px;">{email}</p>
                </div>

                <h3 style="color: #fff; margin: 28px 0 12px;">🚀 ¿Qué puedes hacer ahora?</h3>
                <ul style="font-size: 14px; line-height: 1.8; color: #cbd5e1; padding-left: 20px;">
                    <li><strong>Pagar tu renta</strong> con tarjeta de forma segura</li>
                    <li><strong>Conectar Xcel Energy</strong> y ver tu consumo eléctrico real</li>
                    <li><strong>Escanear tus recibos</strong> con la cámara y la IA</li>
                    <li><strong>Reportar mantenimiento</strong> 24/7</li>
                    <li><strong>Recibir notificaciones</strong> de facturas y pagos</li>
                </ul>

                <p style="color: #94a3b8; font-size: 13px; margin-top: 28px;">Si tienes dudas, responde a este correo o llámanos al teléfono que aparece en tu contrato.</p>

                <hr style="border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 24px 0;" />
                <p style="text-align: center; color: #64748b; font-size: 12px; margin: 0;">
                    Ross House Rentals LLC<br/>
                    <a href="mailto:info@rosshouserentals.com" style="color: #C8102E; text-decoration: none;">info@rosshouserentals.com</a>
                </p>
            </div>
        </div>
        """

        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        mail = Mail(
            from_email=Email(from_email, "Ross House Rentals"),
            to_emails=To(email),
            subject="🏠 ¡Bienvenido a Ross House Rentals!",
            html_content=html,
        )
        response = sg.send(mail)
        logging.info(f"✅ Welcome email (self-reg) sent to {email} (status: {response.status_code})")
    except Exception as e:
        logging.error(f"❌ Failed to send welcome email to {email}: {e}")


async def _send_welcome_sms(phone: str, name: str):
    """Send a friendly welcome SMS to the new tenant."""
    import os
    try:
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        from_phone = os.environ.get("TWILIO_PHONE_NUMBER", "")

        if not sid or not token or not from_phone:
            config = await get_db().api_config.find_one({"_id": "main"})
            if config:
                sid = sid or config.get('twilio_account_sid', '')
                token = token or config.get('twilio_auth_token', '')
                from_phone = from_phone or config.get('twilio_phone_number', '')

        if not sid or not token or not from_phone:
            logging.warning(f"⚠️ No Twilio config — skipping welcome SMS to {phone}")
            return

        # Normalize phone to E.164 if needed
        normalized = phone if phone.startswith('+') else f"+1{phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '')}"

        from twilio.rest import Client
        client = Client(sid, token)
        first_name = (name or "").split(' ', 1)[0] or "Vecino"
        body = (
            f"Hola {first_name}! 🏠 Bienvenido(a) a Ross House Rentals. "
            f"Tu cuenta esta lista. Ya puedes pagar tu renta, conectar tu electricidad "
            f"y reportar mantenimiento desde la app. Gracias por elegirnos!"
        )
        msg = client.messages.create(body=body, from_=from_phone, to=normalized)
        logging.info(f"✅ Welcome SMS sent to {phone[-4:]} (SID: {msg.sid})")
    except Exception as e:
        logging.error(f"❌ Failed to send welcome SMS to {phone}: {e}")


@router.post('/admin/resend-welcome')
async def admin_resend_welcome(request: Request):
    """Admin-only endpoint to manually resend welcome email + SMS to any user.
    Body: { "email": "..." } OR { "user_id": "..." } OR { "phone": "..." }
    """
    from rental.shared import auth_admin
    await auth_admin(request)
    data = await request.json()

    db = get_db()
    query = {}
    if data.get("user_id"):
        try:
            from bson import ObjectId
            query["_id"] = ObjectId(data["user_id"])
        except Exception:
            query["_id"] = data["user_id"]
    elif data.get("email"):
        import re as _re
        query["email"] = {"$regex": f"^{_re.escape(data['email'])}$", "$options": "i"}
    elif data.get("phone"):
        query["phone"] = data["phone"]
    else:
        raise HTTPException(status_code=400, detail="Provide user_id, email, or phone")

    user = await db.app_users.find_one(query)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    email = user.get("email", "")
    name = user.get("name") or f"{user.get('first_name','')} {user.get('last_name','')}".strip() or "Vecino"
    phone = user.get("phone", "")

    sent_email = False
    sent_sms = False
    err_email = None
    err_sms = None

    if email:
        try:
            await _send_welcome_email_self_registered(email, name)
            sent_email = True
        except Exception as e:
            err_email = str(e)
    if phone:
        try:
            await _send_welcome_sms(phone, name)
            sent_sms = True
        except Exception as e:
            err_sms = str(e)

    return {
        "success": True,
        "user": {"email": email, "name": name, "phone": phone},
        "email_sent": sent_email,
        "sms_sent": sent_sms,
        "email_error": err_email,
        "sms_error": err_sms,
    }




# ==================== TENANT PROFILE PHOTO (FROM MOBILE APP) ====================

@router.post('/marketplace/profile-photo')
async def upload_profile_photo(request: Request):
    """Tenant uploads their own profile photo from the mobile app.
    This is stored as profile_photo_url (separate from the admin's official photo_url)."""
    user = await auth_marketplace(request)
    data = await request.json()
    image_data = data.get('image_data', '')
    content_type = data.get('content_type', 'image/jpeg')

    if not image_data:
        raise HTTPException(status_code=400, detail="No image data provided")

    user_email = user.get('email', '')
    user_id = str(user.get('_id', ''))

    # Find tenant record linked to this user
    tenant = await get_db().tenants.find_one({
        "$or": [{"email": user_email}, {"app_user_id": user_id}]
    })

    try:
        import base64 as b64_mod, uuid
        from rental_storage_service import set_emergent_key, put_object, APP_NAME

        # Load storage key from DB
        config = await get_db().api_config.find_one({"_id": "main"})
        if config and config.get("EMERGENT_LLM_KEY"):
            set_emergent_key(config["EMERGENT_LLM_KEY"])

        # Clean base64
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]
        file_bytes = b64_mod.b64decode(image_data)

        ext = 'jpg' if 'jpeg' in content_type else content_type.split('/')[-1]
        file_id = str(uuid.uuid4())
        path = f"{APP_NAME}/profile-photos/{user_id}/{file_id}.{ext}"

        result = put_object(path, file_bytes, content_type)
        storage_path = result.get("path", path)

        # Build public URL
        base_url = str(request.base_url).rstrip('/')
        fwd_host = request.headers.get('x-forwarded-host') or request.headers.get('host')
        fwd_proto = request.headers.get('x-forwarded-proto', 'https')
        if fwd_host:
            base_url = f"{fwd_proto}://{fwd_host}"
        clean = storage_path.replace(f"{APP_NAME}/", "") if storage_path.startswith(f"{APP_NAME}/") else storage_path
        photo_url = f"{base_url}/api/public/property-file/{clean}"

        # Update app_users record
        await get_db().app_users.update_one(
            {"_id": user.get("_id")},
            {"$set": {"profile_photo_url": photo_url, "updated_at": datetime.utcnow()}}
        )

        # Also update tenant record if linked
        if tenant:
            await get_db().tenants.update_one(
                {"_id": tenant["_id"]},
                {"$set": {"profile_photo_url": photo_url, "updated_at": datetime.utcnow()}}
            )

        return {"success": True, "profile_photo_url": photo_url}
    except Exception as e:
        logging.error(f"Profile photo upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Error al subir foto: {str(e)}")


@router.get('/marketplace/profile-photo')
async def get_profile_photo(request: Request):
    """Get the current user's profile photo URL"""
    user = await auth_marketplace(request)
    photo_url = user.get('profile_photo_url', '')
    return {"success": True, "profile_photo_url": photo_url}


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


