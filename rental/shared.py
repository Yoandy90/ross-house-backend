"""
Rental Shared Utilities
========================
Database, auth helpers, serialization, push notifications.
Shared across all rental sub-routers.
"""
import logging
import jwt
import hashlib
import io
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from bson import ObjectId
from fastapi import HTTPException, Request

_db = None

TENANT_JWT_SECRET = "ross-house-rentals-tenant-secret-2026"


def get_db():
    """Get the shared database reference"""
    return _db


def set_db(db):
    """Set the shared database reference"""
    global _db
    _db = db
    logging.info("✅ Rental shared DB initialized")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def auth_admin(request: Request):
    """Authenticate admin user from JWT or session token"""
    db = get_db()
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="No autorizado")
    token = auth.replace('Bearer ', '')

    # ── Try JWT first (marketplace tokens) ──
    try:
        payload = jwt.decode(token, TENANT_JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") == "marketplace" and payload.get("role") == "admin":
            user = await db.app_users.find_one({"_id": ObjectId(payload["user_id"])})
            if user and user.get("role") == "admin":
                return serialize(user)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, Exception):
        pass  # Fall through to session-based auth

    # ── Fallback: session-based auth ──
    session = await db.user_sessions.find_one({'session_token': token})
    if not session:
        raise HTTPException(status_code=401, detail="Sesión inválida")

    expires_at = session['expires_at']
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < datetime.now(timezone.utc):
        await db.user_sessions.delete_one({'session_token': token})
        raise HTTPException(status_code=401, detail='Sesión expirada')

    user_id = session['user_id']
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
    except:
        user = await db.users.find_one({"_id": user_id})

    if not user or user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Se requiere rol de administrador")
    return user


def create_tenant_token(tenant_id: str, email: str):
    """Create JWT token for tenant"""
    payload = {
        "tenant_id": tenant_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(days=7),
        "type": "tenant"
    }
    return jwt.encode(payload, TENANT_JWT_SECRET, algorithm="HS256")


def create_marketplace_token(user_id: str, email: str, role: str):
    """Create JWT token for marketplace user (any role)"""
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": datetime.utcnow() + timedelta(days=30),
        "type": "marketplace"
    }
    return jwt.encode(payload, TENANT_JWT_SECRET, algorithm="HS256")


async def auth_marketplace(request: Request):
    """Authenticate a marketplace user from JWT"""
    db = get_db()
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = auth.split(" ")[1]
    try:
        payload = jwt.decode(token, TENANT_JWT_SECRET, algorithms=["HS256"])
        ptype = payload.get("type", "")

        if ptype == "marketplace":
            user = await db.app_users.find_one({"_id": ObjectId(payload["user_id"])})
            if not user:
                user = await db.tenants.find_one({"_id": ObjectId(payload["user_id"])})
                if user:
                    user["role"] = "tenant"
            if not user:
                raise HTTPException(status_code=401, detail="Usuario no encontrado")
            return serialize(user)

        elif ptype == "tenant":
            tenant = await db.tenants.find_one({"_id": ObjectId(payload["tenant_id"])})
            if not tenant:
                raise HTTPException(status_code=401, detail="Inquilino no encontrado")
            t = serialize(tenant)
            t["role"] = "tenant"
            return t
        else:
            raise HTTPException(status_code=401, detail="Token inválido")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")


async def auth_tenant(request: Request):
    """Authenticate tenant from JWT token"""
    db = get_db()
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = auth.split(" ")[1]
    try:
        payload = jwt.decode(token, TENANT_JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "tenant":
            raise HTTPException(status_code=401, detail="Token inválido")
        tenant = await db.tenants.find_one({"_id": ObjectId(payload["tenant_id"])})
        if not tenant:
            raise HTTPException(status_code=401, detail="Inquilino no encontrado")
        return serialize(tenant)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")


async def auth_tenant_flex(request: Request):
    """Accept BOTH legacy `tenant` JWTs and modern `marketplace` JWTs.

    Returns a tenant-like dict so existing endpoints keep working.
    For marketplace users with role=tenant, resolves the matching
    `tenants` document (or builds an in-memory one) so callers can use
    `tenant['_id']` as the contract.tenant_id.
    """
    db = get_db()
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = auth.split(" ")[1]

    # ── 1) Try legacy tenant JWT ──
    try:
        payload = jwt.decode(token, TENANT_JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") == "tenant":
            tenant = await db.tenants.find_one({"_id": ObjectId(payload["tenant_id"])})
            if tenant:
                return serialize(tenant)
    except Exception:
        pass

    # ── 2) Fall back to marketplace JWT and resolve tenant by app_user_id/email ──
    try:
        user = await auth_marketplace(request)
    except HTTPException as e:
        raise e
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")

    user_id = str(user.get("_id", ""))
    user_email = (user.get("email") or "").strip().lower()

    # Try linked tenants record
    tenant = None
    if user_id:
        tenant = await db.tenants.find_one({"app_user_id": user_id})
    if not tenant and user_email:
        import re as _re
        tenant = await db.tenants.find_one({
            "email": {"$regex": f"^{_re.escape(user_email)}$", "$options": "i"}
        })

    if tenant:
        return serialize(tenant)

    # No tenants record exists — return the marketplace user as-is so endpoints
    # that only need _id / email can still work. Contracts use tenant_id which
    # for some seed data is the app_user_id directly.
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def serialize(doc):
    """Convert MongoDB doc to JSON-safe dict"""
    if not doc:
        return None
    doc['_id'] = str(doc['_id'])
    doc['id'] = doc['_id']  # alias for downstream code that expects 'id'
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            doc[k] = str(v)
        elif isinstance(v, datetime):
            doc[k] = v.isoformat()
        elif isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (ObjectId, datetime)):
                    v[kk] = str(vv) if isinstance(vv, ObjectId) else vv.isoformat()
                elif isinstance(vv, list):
                    for item in vv:
                        if isinstance(item, dict):
                            for kkk, vvv in item.items():
                                if isinstance(vvv, (ObjectId, datetime)):
                                    item[kkk] = str(vvv) if isinstance(vvv, ObjectId) else vvv.isoformat()
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    for kk, vv in item.items():
                        if isinstance(vv, (ObjectId, datetime)):
                            item[kk] = str(vv) if isinstance(vv, ObjectId) else vv.isoformat()
    return doc


# ═══════════════════════════════════════════════════════════════════════════════
# PUSH NOTIFICATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def send_rental_push_to_user(user_id: str, title: str, body: str, data: dict = None):
    """Send push notification to a specific marketplace user by their ID"""
    db = get_db()
    if not user_id:
        return

    user = None
    try:
        user = await db.app_users.find_one({"_id": ObjectId(user_id)})
    except:
        user = await db.app_users.find_one({"_id": user_id})

    if not user:
        try:
            user = await db.tenants.find_one({"_id": ObjectId(user_id)})
        except:
            user = await db.tenants.find_one({"_id": user_id})

    if not user:
        logging.warning(f"⚠️ Push: User {user_id} not found")
        return

    push_token = user.get("push_token", "")
    if not push_token:
        logging.info(f"ℹ️ Push: User {user_id} ({user.get('name', '')}) has no push token")
        return

    try:
        from push_notification_service import send_push_notification
        await send_push_notification(
            expo_push_token=push_token,
            title=title,
            body=body,
            data=data or {}
        )
        logging.info(f"📱 Push sent to {user.get('name', '')} ({user.get('email', '')}): {title}")
    except Exception as e:
        logging.warning(f"⚠️ Push send error: {e}")


async def send_rental_push_to_admins(title: str, body: str, data: dict = None):
    """Send push notification to Ross House admin users only (app_users collection)"""
    db = get_db()

    # Only query app_users (Ross House Rentals app tokens)
    # NOT db.users which belongs to Ross Lending/Tax app
    admin_users = await db.app_users.find(
        {"role": "admin", "push_token": {"$exists": True, "$ne": ""}}
    ).to_list(50)

    for admin in admin_users:
        push_token = admin.get("push_token", "")
        if push_token:
            try:
                from push_notification_service import send_push_notification
                await send_push_notification(
                    expo_push_token=push_token,
                    title=title,
                    body=body,
                    data=data or {}
                )
                logging.info(f"📱 Push sent to admin {admin.get('email', '')}: {title}")
            except Exception as e:
                logging.warning(f"⚠️ Push to admin error: {e}")
