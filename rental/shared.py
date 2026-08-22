"""
Rental Shared Utilities
========================
Database, auth helpers, serialization, push notifications.
Shared across all rental sub-routers.
"""
import logging
import os
import secrets as _secrets
import jwt
import hashlib
import io
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from bson import ObjectId
from fastapi import HTTPException, Request

_db = None

# ── JWT signing secret ────────────────────────────────────────────────────
# MUST be provided via environment (TENANT_JWT_SECRET) in production.
# If missing, we generate a random per-process secret (invalidates all
# existing tokens on restart — safer than shipping a hardcoded one).
TENANT_JWT_SECRET = os.environ.get("TENANT_JWT_SECRET")
if not TENANT_JWT_SECRET:
    if os.environ.get("ENVIRONMENT", "").lower() == "production":
        logging.critical(
            "[SECURITY] TENANT_JWT_SECRET env var is not set in production. "
            "Falling back to a random per-process secret — all existing "
            "tokens will be invalidated on every restart. "
            "Set TENANT_JWT_SECRET to a long random string ASAP."
        )
    TENANT_JWT_SECRET = _secrets.token_urlsafe(64)


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
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, Exception):
        payload = None  # Fall through to session-based auth
    if payload and payload.get("type") == "marketplace" and payload.get("role") == "admin":
        await _validate_session_claims(payload)  # raises 401 if sid revoked/expired
        user = await db.app_users.find_one({"_id": ObjectId(payload["user_id"])})
        if user and user.get("role") == "admin":
            return serialize(user)

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
    """LEGACY: JWT without server-side session (no sid). Kept ONLY for
    backward compatibility of already-issued tokens; new logins must use
    create_session_token(). Retirement: 30 days after Phase 1 deploy (max
    natural expiry of outstanding tokens)."""
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": datetime.utcnow() + timedelta(days=30),
        "type": "marketplace"
    }
    return jwt.encode(payload, TENANT_JWT_SECRET, algorithm="HS256")


# ══════════════════════════ SERVER-SIDE SESSIONS (Phase 1) ══════════════════════════

SESSION_TTL_DAYS = 30


async def create_session(user_id: str, role: str, request=None,
                         device_name: str = "", platform: str = "",
                         app_version: str = "") -> str:
    """Create a server-side session and return its sid."""
    import uuid as _uuid
    import hashlib as _hashlib
    sid = _uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    ip = request.client.host if (request and request.client) else "unknown"
    salt = os.environ.get("VISITOR_IP_SALT", "rhr-static-salt")
    ua = (request.headers.get("user-agent", "") if request else "")[:200]
    await get_db().auth_sessions.insert_one({
        "sid": sid,
        "user_id": str(user_id),
        "role": role,
        "created_at": now,
        "last_seen_at": now,
        "expires_at": now + timedelta(days=SESSION_TTL_DAYS),
        "revoked_at": None,
        "revoked_reason": None,
        "device_name": device_name or (request.headers.get("x-device-name", "")[:80] if request else ""),
        "platform": platform or (request.headers.get("x-platform", "")[:40] if request else ""),
        "app_version": app_version or (request.headers.get("x-app-version", "")[:40] if request else ""),
        "ip_hash": _hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32],
        "user_agent": ua,
    })
    return sid


async def create_session_token(user_id: str, email: str, role: str,
                               request=None) -> str:
    """Phase 1 token factory: server-side session + JWT bound via sid/jti."""
    import uuid as _uuid
    sid = await create_session(user_id, role, request)
    now = datetime.utcnow()
    payload = {
        "user_id": str(user_id),
        "sub": str(user_id),
        "email": email,
        "role": role,
        "sid": sid,
        "jti": _uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(days=SESSION_TTL_DAYS),
        "type": "marketplace",
    }
    return jwt.encode(payload, TENANT_JWT_SECRET, algorithm="HS256")


async def _validate_session_claims(payload: dict) -> None:
    """Central sid validation. Tokens WITHOUT sid = legacy (allowed during the
    30-day migration window). Tokens WITH sid must map to a live, unrevoked,
    unexpired session owned by the same user."""
    sid = payload.get("sid")
    if not sid:
        return  # legacy token — valid until natural exp
    if not isinstance(sid, str) or len(sid) != 32:
        raise HTTPException(status_code=401, detail="session_invalid")
    ses = await get_db().auth_sessions.find_one({"sid": sid})
    if not ses:
        raise HTTPException(status_code=401, detail="session_invalid")
    if ses.get("revoked_at") is not None:
        raise HTTPException(status_code=401, detail="session_revoked")
    exp = ses.get("expires_at")
    if isinstance(exp, datetime) and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp and exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="session_expired")
    if str(ses.get("user_id")) != str(payload.get("user_id", "")):
        raise HTTPException(status_code=401, detail="session_invalid")
    await get_db().auth_sessions.update_one(
        {"sid": sid}, {"$set": {"last_seen_at": datetime.now(timezone.utc)}})


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
            await _validate_session_claims(payload)  # sid binding (Phase 1)
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
    except HTTPException:
        raise  # session_revoked / session_expired / session_invalid — do not mask
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")


# ══════════════════ CENTRAL AUTHORIZATION PRIMITIVES (Phase 1) ══════════════════
# Prefer these names in new/refactored routers. They wrap the (already
# centralized) validators above — single point for sid/revocation checks.

require_authenticated_user = auth_marketplace
require_admin = auth_admin


def require_role(*roles):
    """Dependency factory: user must be authenticated AND have one of `roles`."""
    async def _dep(request: Request):
        user = await auth_marketplace(request)
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Permiso denegado")
        return user
    return _dep


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
