"""
Phase 1 — Session management + admin audit log API.

POST   /api/auth/logout                 revoke current session (idempotent)
POST   /api/auth/logout-all             revoke ALL sessions of the user
GET    /api/auth/sessions               list own sessions (safe fields only)
DELETE /api/auth/sessions/{sid}         revoke one of MY sessions (IDOR-safe)
GET    /api/admin/audit-logs            read-only audit trail (admin only)
"""
import logging
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request

from .shared import (get_db, auth_marketplace, auth_admin, TENANT_JWT_SECRET)
from .security import audit_log

logger = logging.getLogger("sessions")
router = APIRouter(tags=["sessions"])


def _decode_no_session_check(request: Request) -> dict:
    """Signature+exp validation ONLY (used by logout so an already-revoked
    session can still log out idempotently)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    try:
        return jwt.decode(auth.split(" ")[1], TENANT_JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")


@router.post("/auth/logout")
async def logout(request: Request):
    """Revoke the CURRENT session. Idempotent — success even if already revoked.
    Legacy tokens (no sid) get success too (client clears local storage)."""
    payload = _decode_no_session_check(request)
    sid = payload.get("sid")
    if sid:
        now = datetime.now(timezone.utc)
        await get_db().auth_sessions.update_one(
            {"sid": sid, "user_id": str(payload.get("user_id", "")),
             "revoked_at": None},
            {"$set": {"revoked_at": now, "revoked_reason": "logout"}})
        if payload.get("role") == "admin":
            await audit_log(admin_user_id=payload.get("user_id", ""),
                            action="logout", resource_type="session",
                            resource_id=sid, request=request)
    return {"success": True}


@router.post("/auth/logout-all")
async def logout_all(request: Request):
    """Revoke ALL sessions of the authenticated user.
    body.keep_current_session=true keeps the caller's session alive
    (use case: 'sign out other devices')."""
    user = await auth_marketplace(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    keep_current = bool(body.get("keep_current_session"))
    payload = _decode_no_session_check(request)
    current_sid = payload.get("sid", "")

    q = {"user_id": str(user["_id"]), "revoked_at": None}
    if keep_current and current_sid:
        q["sid"] = {"$ne": current_sid}
    now = datetime.now(timezone.utc)
    r = await get_db().auth_sessions.update_many(
        q, {"$set": {"revoked_at": now, "revoked_reason": "logout_all"}})
    if user.get("role") == "admin":
        await audit_log(admin_user_id=str(user["_id"]), action="logout_all",
                        resource_type="session", result="success",
                        request=request,
                        metadata={"revoked_count": r.modified_count,
                                  "kept_current": keep_current})
    return {"success": True, "revoked": r.modified_count}


@router.get("/auth/sessions")
async def list_sessions(request: Request):
    """List MY sessions — safe fields only (no tokens, no raw IP)."""
    user = await auth_marketplace(request)
    payload = _decode_no_session_check(request)
    current_sid = payload.get("sid", "")
    cursor = get_db().auth_sessions.find(
        {"user_id": str(user["_id"])}).sort("last_seen_at", -1).limit(50)
    items = []
    async for s in cursor:
        items.append({
            "sid": s["sid"],
            "device_name": s.get("device_name") or "",
            "platform": s.get("platform") or "",
            "app_version": s.get("app_version") or "",
            "created_at": s.get("created_at"),
            "last_seen_at": s.get("last_seen_at"),
            "current_session": s["sid"] == current_sid,
            "revoked": s.get("revoked_at") is not None,
        })
    return {"success": True, "sessions": items}


@router.delete("/auth/sessions/{sid}")
async def revoke_session(sid: str, request: Request):
    """Revoke one of MY sessions (e.g. another device). IDOR-safe: the query
    is scoped to the caller's user_id — a foreign sid yields 404."""
    user = await auth_marketplace(request)
    ses = await get_db().auth_sessions.find_one(
        {"sid": sid, "user_id": str(user["_id"])})
    if not ses:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if ses.get("revoked_at") is None:
        await get_db().auth_sessions.update_one(
            {"sid": sid},
            {"$set": {"revoked_at": datetime.now(timezone.utc),
                      "revoked_reason": "revoked_by_user"}})
    if user.get("role") == "admin":
        await audit_log(admin_user_id=str(user["_id"]), action="session_revoked",
                        resource_type="session", resource_id=sid, request=request)
    return {"success": True}


# ══════════════════════ ADMIN AUDIT LOG (read-only) ══════════════════════

@router.get("/admin/audit-logs")
async def get_audit_logs(request: Request, admin_user_id: str = "",
                         action: str = "", resource_type: str = "",
                         resource_id: str = "", date_from: str = "",
                         date_to: str = "", limit: int = 50, skip: int = 0):
    await auth_admin(request)
    q: dict = {}
    if admin_user_id:
        q["admin_user_id"] = admin_user_id
    if action:
        q["action"] = action
    if resource_type:
        q["resource_type"] = resource_type
    if resource_id:
        q["resource_id"] = resource_id
    if date_from or date_to:
        rng = {}
        try:
            if date_from:
                rng["$gte"] = datetime.fromisoformat(date_from)
            if date_to:
                rng["$lte"] = datetime.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Fecha inválida (ISO)")
        q["timestamp"] = rng
    limit = max(1, min(int(limit), 200))
    cursor = get_db().admin_audit_logs.find(q).sort("timestamp", -1) \
        .skip(max(0, int(skip))).limit(limit)
    items = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        items.append(d)
    total = await get_db().admin_audit_logs.count_documents(q)
    return {"success": True, "total": total, "items": items}
# NOTE: intentionally NO PUT/PATCH/DELETE — audit logs are append-only.
