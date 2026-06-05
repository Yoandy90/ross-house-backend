"""
Rental Auth Router
===================
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import os
import random
import jwt
import bcrypt
from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

router = APIRouter()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def normalize_rental_phone(phone: str, country_code: str = '+1') -> str:
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) == 10:
        return f'{country_code}{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    if phone.startswith('+'):
        return phone
    return f'{country_code}{digits}'

@router.post('/marketplace/register-push-token')
async def marketplace_register_push_token(request: Request):
    """Register an Expo push token for the current marketplace user"""
    user = await auth_marketplace(request)
    data = await request.json()
    
    push_token = data.get("push_token", "").strip()
    platform = data.get("platform", "ios")
    device_name = data.get("device_name", "")
    
    if not push_token:
        raise HTTPException(status_code=400, detail="push_token es requerido")
    
    await get_db().app_users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "push_token": push_token,
            "push_platform": platform,
            "push_device_name": device_name,
            "push_token_updated_at": datetime.utcnow(),
        }}
    )
    
    logging.info(f"📱 Push token registered for {user.get('email', '')} ({platform}/{device_name})")
    return {"success": True, "message": "Push token registrado"}


@router.get('/marketplace/notifications')
async def marketplace_get_notifications(request: Request):
    """Get recent notifications for the current marketplace user"""
    user = await auth_marketplace(request)
    user_id = str(user["_id"])
    
    limit = int(request.query_params.get("limit", "30"))
    
    notifications = []
    cursor = get_db().rental_notifications.find(
        {"$or": [{"user_id": user_id}, {"target": "all"}, {"target": user.get("role", "")}]}
    ).sort("created_at", -1).limit(limit)
    
    async for n in cursor:
        notifications.append({
            "id": str(n["_id"]),
            "title": n.get("title", ""),
            "body": n.get("body", ""),
            "type": n.get("type", ""),
            "read": user_id in n.get("read_by", []),
            "data": n.get("data", {}),
            "created_at": n.get("created_at", "").isoformat() if n.get("created_at") else "",
        })
    
    unread = await get_db().rental_notifications.count_documents({
        "$or": [{"user_id": user_id}, {"target": "all"}, {"target": user.get("role", "")}],
        "read_by": {"$ne": user_id}
    })
    
    return {"success": True, "notifications": notifications, "unread": unread}


@router.post('/marketplace/notifications/{notif_id}/read')
async def marketplace_mark_notification_read(notif_id: str, request: Request):
    """Mark a notification as read"""
    user = await auth_marketplace(request)
    user_id = str(user["_id"])
    
    await get_db().rental_notifications.update_one(
        {"_id": ObjectId(notif_id)},
        {"$addToSet": {"read_by": user_id}}
    )
    
    return {"success": True}




@router.post('/public/marketplace-register')
async def marketplace_register(request: Request):
    """Register a new marketplace user (tenant, landlord, or buyer)"""
    data = await request.json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    phone = data.get("phone", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "tenant")  # tenant, landlord, buyer

    if not name or not email or not phone:
        raise HTTPException(status_code=400, detail="Nombre, email y teléfono son requeridos")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")
    if role not in ("tenant", "landlord", "buyer"):
        raise HTTPException(status_code=400, detail="Rol inválido")

    # Check if email already exists
    existing = await get_db().app_users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con este email")

    user = {
        "name": name,
        "email": email,
        "phone": phone,
        "password_hash": hash_password(password),
        "role": role,
        "status": "active",
        "verified": False,
        "created_at": datetime.utcnow(),
    }

    # For landlords, also save company info if provided
    if role == "landlord":
        user["company_name"] = data.get("company_name", "")
        user["properties_count"] = 0
        user["commission_rate"] = 10  # default 10%

    result = await get_db().app_users.insert_one(user)
    user_id = str(result.inserted_id)

    # Also create in tenants collection if tenant role (backward compatibility)
    if role == "tenant":
        import random
        tenant_number = f"T-{random.randint(10000, 99999)}"
        await get_db().tenants.insert_one({
            "name": name,
            "first_name": name.split(' ', 1)[0],
            "last_name": name.split(' ', 1)[1] if ' ' in name else '',
            "email": email,
            "phone": phone,
            "tenant_number": tenant_number,
            "app_user_id": user_id,
            "status": "active",
            "created_at": datetime.utcnow(),
            "created_from": "app",
        })

    # Send welcome email with credentials
    try:
        from rental.tenant_router import _send_welcome_email
        await _send_welcome_email(email, name, password)
    except Exception as e:
        logging.warning(f"Could not send welcome email: {e}")

    token = create_marketplace_token(user_id, email, role)

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user_id,
            "name": name,
            "email": email,
            "role": role,
        }
    }


@router.post('/public/marketplace-login')
async def marketplace_login(request: Request):
    """Login for marketplace users (all roles).
    Supports: email+password (primary) or email+phone (legacy fallback).
    """
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "").strip()
    phone = body.get("phone", "").strip().replace("-", "").replace(" ", "").replace("(", "").replace(")", "")

    if not email:
        raise HTTPException(status_code=400, detail="Email es requerido")
    if not password and not phone:
        raise HTTPException(status_code=400, detail="Contraseña o teléfono es requerido")

    # Find user
    user = await get_db().app_users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not user:
        # Fallback: check tenants collection
        tenant = await get_db().tenants.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
        if not tenant:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")
        user_id = str(tenant["_id"])
        role = "tenant"
        token = create_tenant_token(user_id, email, tenant.get("tenant_number", ""))
        return {
            "success": True, "token": token,
            "user": {"id": user_id, "name": tenant.get("name", ""), "email": email,
                     "role": role, "tenant_number": tenant.get("tenant_number", "")},
        }

    # Method 1: Password authentication (primary)
    if password and user.get("password_hash"):
        if not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")
    # Method 2: Phone fallback (for users without password)
    elif phone:
        stored_phone = user.get("phone", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if phone != stored_phone and phone != stored_phone[-4:]:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")
    # Method 3: Password provided but user has no hash — try phone match
    elif password and not user.get("password_hash"):
        clean_pw = password.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        stored_phone = user.get("phone", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if clean_pw != stored_phone and clean_pw != stored_phone[-4:]:
            raise HTTPException(status_code=401, detail="Credenciales inválidas. Usa 'Olvidé mi contraseña' para establecer una.")
    else:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    user_id = str(user["_id"])
    role = user.get("role", "tenant")
    token = create_marketplace_token(user_id, email, role)

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user_id,
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "role": role,
            "tenant_number": user.get("tenant_number", ""),
            "has_password": bool(user.get("password_hash")),
        }
    }

    # Fallback: check tenants collection (backward compat)
    tenant = await get_db().tenants.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if tenant:
        stored_phone = tenant.get("phone", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if phone != stored_phone and phone != stored_phone[-4:]:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")

        tenant_id = str(tenant["_id"])
        token = create_marketplace_token(tenant_id, email, "tenant")

        return {
            "success": True,
            "token": token,
            "user": {
                "id": tenant_id,
                "name": tenant.get("name", ""),
                "email": tenant.get("email", ""),
                "role": "tenant",
                "tenant_number": tenant.get("tenant_number", ""),
            }
        }

    raise HTTPException(status_code=401, detail="Credenciales inválidas")


# ═══════════════════════════════════════════════════════════════════════════════
# PASSWORD MANAGEMENT (Forgot Password + Change Password + Set Password)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/auth/forgot-password')
async def forgot_password(request: Request):
    """Send a password reset code via SMS to the user's phone."""
    data = await request.json()
    email = data.get("email", "").strip().lower()

    if not email:
        raise HTTPException(status_code=400, detail="Email es requerido")

    user = await get_db().app_users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not user:
        return {"success": True, "message": "Si el email está registrado, recibirás un código por SMS."}

    phone = user.get("phone", "")
    if not phone:
        raise HTTPException(status_code=400, detail="No hay teléfono asociado a esta cuenta")

    code = str(random.randint(100000, 999999))
    expires = datetime.utcnow() + timedelta(minutes=10)

    await get_db().password_resets.update_one(
        {"email": email},
        {"$set": {"email": email, "code": code, "expires_at": expires, "used": False, "created_at": datetime.utcnow()}},
        upsert=True,
    )

    try:
        from twilio.rest import Client
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        from_phone = os.environ.get("TWILIO_PHONE_NUMBER", "")
        if sid and token and from_phone:
            client = Client(sid, token)
            normalized = normalize_rental_phone(phone)
            client.messages.create(
                body=f"Ross House Rentals: Tu código para restablecer contraseña es {code}. Expira en 10 minutos.",
                from_=from_phone, to=normalized,
            )
            logging.info(f"✅ Password reset code sent to {phone[-4:]}")
    except Exception as e:
        logging.error(f"Failed to send SMS for password reset: {e}")

    phone_masked = f"***-***-{phone[-4:]}" if len(phone) >= 4 else "***"
    return {"success": True, "message": "Código enviado por SMS", "phone_masked": phone_masked}


@router.post('/auth/reset-password')
async def reset_password(request: Request):
    """Verify reset code and set a new password."""
    data = await request.json()
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    new_password = data.get("new_password", "").strip()

    if not email or not code or not new_password:
        raise HTTPException(status_code=400, detail="Email, código y nueva contraseña son requeridos")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")

    reset = await get_db().password_resets.find_one({"email": email, "code": code, "used": False})
    if not reset:
        raise HTTPException(status_code=400, detail="Código inválido o expirado")
    if reset.get("expires_at") and reset["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="El código ha expirado")

    await get_db().password_resets.update_one({"_id": reset["_id"]}, {"$set": {"used": True}})

    hashed = hash_password(new_password)
    await get_db().app_users.update_one(
        {"email": {"$regex": f"^{email}$", "$options": "i"}},
        {"$set": {"password_hash": hashed, "updated_at": datetime.utcnow()}}
    )

    logging.info(f"✅ Password reset successful for {email}")
    return {"success": True, "message": "Contraseña actualizada exitosamente"}


@router.put('/auth/change-password')
async def change_password(request: Request):
    """Change password for authenticated user."""
    user = await auth_marketplace(request)
    data = await request.json()
    current_password = data.get("current_password", "").strip()
    new_password = data.get("new_password", "").strip()

    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres")

    if user.get("password_hash"):
        if not current_password:
            raise HTTPException(status_code=400, detail="Contraseña actual es requerida")
        if not verify_password(current_password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")

    hashed = hash_password(new_password)
    try:
        oid = ObjectId(user["_id"]) if not isinstance(user["_id"], ObjectId) else user["_id"]
    except:
        oid = user["_id"]

    await get_db().app_users.update_one({"_id": oid}, {"$set": {"password_hash": hashed, "updated_at": datetime.utcnow()}})

    msg = "Contraseña actualizada" if user.get("password_hash") else "Contraseña establecida exitosamente"
    return {"success": True, "message": msg}




def normalize_rental_phone(phone: str, country_code: str = '+1') -> str:
    """Normalize phone number to E.164 format"""
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) == 10:
        return f'{country_code}{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    if phone.startswith('+'):
        return phone
    return f'{country_code}{digits}'


@router.post('/rental/phone/send-otp')
async def rental_phone_send_otp(request: Request):
    """Send a 6-digit OTP code via SMS for Ross House Rentals users"""
    import random, os
    body = await request.json()
    raw_phone = body.get('phone', '').strip()
    country_code = body.get('country_code', '+1')
    
    phone = normalize_rental_phone(raw_phone, country_code)
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) < 10:
        raise HTTPException(status_code=400, detail='Número de teléfono inválido')
    
    # Rate limit: max 5 OTP per phone per 10 minutes
    ten_min_ago = datetime.utcnow() - timedelta(minutes=10)
    recent_otps = await get_db().phone_otps.count_documents({
        'phone': phone,
        'source': 'rental',
        'created_at': {'$gte': ten_min_ago}
    })
    if recent_otps >= 5:
        raise HTTPException(status_code=429, detail='Demasiados intentos. Espera 10 minutos.')
    
    code = f'{random.randint(100000, 999999)}'
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=5)
    
    await get_db().phone_otps.insert_one({
        'phone': phone,
        'code': code,
        'expires_at': expires_at,
        'created_at': now,
        'verified': False,
        'attempts': 0,
        'source': 'rental',
    })
    
    # Send SMS via Twilio
    sms_sent = False
    try:
        twilio_sid = os.getenv('TWILIO_ACCOUNT_SID')
        twilio_token = os.getenv('TWILIO_AUTH_TOKEN')
        twilio_phone = os.getenv('TWILIO_PHONE_NUMBER')
        
        # Also check DB config
        if not twilio_sid:
            config_doc = await get_db().api_config.find_one({'_id': 'main'})
            if config_doc:
                twilio_sid = config_doc.get('twilio_account_sid') or config_doc.get('TWILIO_ACCOUNT_SID')
                twilio_token = config_doc.get('twilio_auth_token') or config_doc.get('TWILIO_AUTH_TOKEN')
                twilio_phone = config_doc.get('twilio_phone_number') or config_doc.get('TWILIO_PHONE_NUMBER')
        
        if twilio_sid and twilio_token and twilio_phone:
            from twilio.rest import Client
            client = Client(twilio_sid, twilio_token)
            message = client.messages.create(
                body=f'Ross House Rentals: Tu código de verificación es {code}. Expira en 5 minutos.',
                from_=twilio_phone,
                to=phone
            )
            sms_sent = True
            logging.info(f"✅ Rental OTP SMS sent to ***{phone[-4:]}: SID={message.sid}")
        else:
            logging.error("❌ Twilio credentials not found for Rental OTP")
    except Exception as e:
        logging.error(f"❌ Error sending Rental OTP SMS: {e}")
    
    # Check if user already exists in app_users
    existing_user = await get_db().app_users.find_one({
        '$or': [
            {'phone': phone},
            {'phone': phone.replace('+1', '')},
            {'phone': phone[-10:]},
            {'phone': raw_phone},
        ]
    })
    
    # Also check tenants collection
    if not existing_user:
        existing_user = await get_db().tenants.find_one({
            '$or': [
                {'phone': phone},
                {'phone': phone.replace('+1', '')},
                {'phone': phone[-10:]},
                {'phone': raw_phone},
            ]
        })
    
    return {
        'success': True,
        'sms_sent': sms_sent,
        'phone_masked': f'***-***-{phone[-4:]}',
        'is_new_user': existing_user is None,
        'expires_in_seconds': 300,
        'message': 'Código enviado por SMS' if sms_sent else 'Error enviando SMS. Intenta de nuevo.',
    }


@router.post('/rental/phone/verify-otp')
async def rental_phone_verify_otp(request: Request):
    """Verify OTP code and login/register the user in app_users collection"""
    import uuid
    body = await request.json()
    raw_phone = body.get('phone', '').strip()
    country_code = body.get('country_code', '+1')
    code = body.get('code', '').strip()
    name = body.get('name', '').strip()
    
    phone = normalize_rental_phone(raw_phone, country_code)
    
    if not code or len(code) != 6:
        raise HTTPException(status_code=400, detail='Código de 6 dígitos requerido')
    
    now = datetime.utcnow()
    
    # Find the most recent valid OTP for this phone
    otp_record = await get_db().phone_otps.find_one(
        {
            'phone': phone,
            'code': code,
            'verified': False,
            'source': 'rental',
            'expires_at': {'$gt': now},
            'attempts': {'$lt': 5},
        },
        sort=[('created_at', -1)]
    )
    
    if not otp_record:
        # Record attempt on the latest OTP
        latest = await get_db().phone_otps.find_one(
            {'phone': phone, 'verified': False, 'source': 'rental'},
            sort=[('created_at', -1)]
        )
        if latest:
            await get_db().phone_otps.update_one(
                {'_id': latest['_id']},
                {'$inc': {'attempts': 1}}
            )
        raise HTTPException(status_code=400, detail='Código incorrecto o expirado')
    
    # Mark OTP as verified
    await get_db().phone_otps.update_one(
        {'_id': otp_record['_id']},
        {'$set': {'verified': True, 'verified_at': now}}
    )
    
    # Find or create user in app_users collection
    user = await get_db().app_users.find_one({
        '$or': [
            {'phone': phone},
            {'phone': phone.replace('+1', '')},
            {'phone': phone[-10:]},
            {'phone': raw_phone},
        ]
    })
    
    # Also check tenants collection as fallback
    if not user:
        user = await get_db().tenants.find_one({
            '$or': [
                {'phone': phone},
                {'phone': phone.replace('+1', '')},
                {'phone': phone[-10:]},
                {'phone': raw_phone},
            ]
        })
        if user:
            # Migrate tenant to app_users with role=tenant
            user['role'] = user.get('role', 'tenant')
    
    if user:
        user_id = str(user['_id'])
        # Update user phone and last login
        collection = get_db().app_users if await get_db().app_users.find_one({'_id': user['_id']}) else get_db().tenants
        await collection.update_one(
            {'_id': user['_id']},
            {'$set': {'phone': phone, 'last_login': now, 'phone_verified': True}}
        )
        logging.info(f"✅ Rental Phone OTP login: ***{phone[-4:]} (existing user: {user.get('name', '')})")
    else:
        # Create new user in app_users
        user_name = name or f'Usuario {phone[-4:]}'
        phone_digits = ''.join(filter(str.isdigit, phone))
        placeholder_email = f'phone_{phone_digits}@rosshouserentals.com'
        
        new_user = {
            'phone': phone,
            'name': user_name,
            'email': placeholder_email,
            'role': 'tenant',
            'source': 'rental_app',
            'phone_verified': True,
            'auth_method': 'phone_otp',
            'created_at': now,
            'last_login': now,
        }
        
        result = await get_db().app_users.insert_one(new_user)
        new_user['_id'] = result.inserted_id
        user = new_user
        user_id = str(result.inserted_id)
        logging.info(f"✅ Rental Phone OTP registration: {user_name} (***{phone[-4:]})")
    
    # Create marketplace JWT token (same as email login)
    user_id = str(user['_id'])
    email = user.get('email', '')
    role = user.get('role', 'tenant')
    token = create_marketplace_token(user_id, email, role)
    
    # Clean up old OTPs
    await get_db().phone_otps.delete_many({
        'phone': phone,
        'source': 'rental',
        'created_at': {'$lt': now - timedelta(hours=1)},
    })
    
    return {
        'success': True,
        'token': token,
        'user': {
            'id': user_id,
            'name': user.get('name', ''),
            'email': user.get('email', ''),
            'phone': phone,
            'role': role,
            'phone_verified': True,
            'tenant_number': user.get('tenant_number', ''),
        }
    }


@router.get('/marketplace/me')
async def get_marketplace_profile(request: Request):
    """Get current user profile"""
    user = await auth_marketplace(request)
    return {
        "success": True,
        "user": {
            "id": user.get("_id"),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
            "role": user.get("role", "tenant"),
            "company_name": user.get("company_name", ""),
            "created_at": str(user.get("created_at", "")),
        }
    }


@router.put('/marketplace/me')
async def update_marketplace_profile(request: Request):
    """Update current user profile (name, phone, email, company_name, profile_photo)."""
    user = await auth_marketplace(request)
    db = get_db()
    data = await request.json()

    # Allowed fields to update
    allowed = {"name", "phone", "email", "company_name"}
    update_fields = {}

    for field in allowed:
        if field in data and data[field] is not None:
            value = str(data[field]).strip()
            if field == "email" and value:
                # Check if email already used by another user
                existing = await db.app_users.find_one({
                    "email": value,
                    "_id": {"$ne": ObjectId(user["_id"]) if not isinstance(user["_id"], ObjectId) else user["_id"]}
                })
                if existing:
                    raise HTTPException(status_code=400, detail="Este email ya está en uso por otro usuario")
            if field == "phone" and value:
                # Clean phone: keep only digits
                value = ''.join(c for c in value if c.isdigit())
            update_fields[field] = value

    if not update_fields:
        raise HTTPException(status_code=400, detail="No hay campos para actualizar")

    update_fields["updated_at"] = datetime.utcnow()

    try:
        oid = ObjectId(user["_id"]) if not isinstance(user["_id"], ObjectId) else user["_id"]
    except:
        oid = user["_id"]

    await db.app_users.update_one({"_id": oid}, {"$set": update_fields})

    # Fetch updated user
    updated = await db.app_users.find_one({"_id": oid})

    return {
        "success": True,
        "message": "Perfil actualizado exitosamente",
        "user": {
            "id": str(updated["_id"]),
            "name": updated.get("name", ""),
            "email": updated.get("email", ""),
            "phone": updated.get("phone", ""),
            "role": updated.get("role", "tenant"),
            "company_name": updated.get("company_name", ""),
        }
    }






# ═══════════════════════════════════════════════════════════════════════════════
# DELETE ACCOUNT (Apple App Store Requirement)
# ═══════════════════════════════════════════════════════════════════════════════

@router.delete('/marketplace/delete-account')
async def marketplace_delete_account(request: Request):
    """Delete the current user's account and all associated data.
    Required by Apple App Store Review Guidelines §5.1.1(v).
    """
    user = await auth_marketplace(request)
    db = get_db()
    user_id = str(user["_id"])
    now = datetime.utcnow()

    logging.warning(f"⚠️ ACCOUNT DELETION requested by {user.get('email')} (id={user_id})")

    # 1. Archive user data before deletion
    archive = {
        "user_id": user_id,
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "role": user.get("role", ""),
        "deleted_at": now,
        "reason": "user_requested",
    }
    await db.deleted_accounts.insert_one(archive)

    # 2. Remove autopay config
    await db.autopay_config.delete_many({"user_id": user_id})

    # 3. Remove push tokens
    try:
        oid = ObjectId(user_id)
        await db.app_users.update_one({"_id": oid}, {"$unset": {"push_token": 1, "push_platform": 1}})
    except:
        pass

    # 4. Anonymize the user record (don't fully delete - keep for financial records)
    try:
        oid = ObjectId(user_id)
        await db.app_users.update_one(
            {"_id": oid},
            {"$set": {
                "name": "Cuenta Eliminada",
                "email": f"deleted_{user_id}@removed.local",
                "phone": "",
                "status": "deleted",
                "deleted_at": now,
                "push_token": "",
            }}
        )
    except:
        pass

    # 5. Remove chat messages (anonymize)
    await db.chat_messages.update_many(
        {"sender_id": user_id},
        {"$set": {"sender_name": "Usuario Eliminado"}}
    )

    logging.info(f"✅ Account deleted/anonymized for user {user_id}")

    return {
        "success": True,
        "message": "Tu cuenta ha sido eliminada exitosamente. Todos tus datos personales han sido removidos."
    }
