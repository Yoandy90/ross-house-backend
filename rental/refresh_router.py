"""POST /api/auth/refresh — Fase A (backend only, detrás de REFRESH_TOKENS_ENABLED).

Modos:
1) Bootstrap (1 vez por sesión): Authorization: Bearer <access JWT con sid válido>
   sin body.refresh_token → emite el PRIMER refresh de esa sesión.
2) Refresh normal: body {"refresh_token": "..."} → rotación + nuevo access.

Seguridad:
- Solo sesiones modernas de auth_sessions (sid). Tokens legacy user_sessions
  y JWTs sin sid NO pueden usar este endpoint.
- Nunca crea sesiones nuevas (⇒ nunca salta 2FA: renueva la sesión que el 2FA creó).
- Reuse fuera de grace ⇒ revoca la familia + audit_log. Respuesta SIEMPRE 401 genérica.
- El refresh raw jamás se persiste, loggea ni entra a audit meta.
- Rate limit persistente: 30 intentos / 10 min por sid.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import jwt
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .shared import get_db, TENANT_JWT_SECRET, SESSION_TTL_DAYS
from .security import audit_log, check_rate_limit_persistent
from .refresh_tokens import (refresh_enabled, generate_refresh_token, hash_refresh_token,
                             next_refresh_token, classify_refresh_attempt, rotation_update,
                             bootstrap_update, reuse_revocation_update, CONSUMED_COLLECTION,
                             ROTATE, GRACE_ROTATE, REUSE_DETECTED, BOOTSTRAP, DENY)

logger = logging.getLogger("refresh")
router = APIRouter(tags=["auth"])

GENERIC_401 = HTTPException(status_code=401, detail="No autorizado")


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


_indexes_ready = False


async def _ensure_indexes():
    """TTL en consumed_refresh_hashes (D2): los hashes consumidos viven hasta la
    expiración absoluta de la familia, sin crecimiento ilimitado del doc de sesión."""
    global _indexes_ready
    if _indexes_ready:
        return
    coll = get_db()[CONSUMED_COLLECTION]
    await coll.create_index("expires_at", expireAfterSeconds=0)
    await coll.create_index("hash")
    _indexes_ready = True


async def _mark_consumed(presented_hash: str, session: dict):
    """Registra el hash consumido (D2). Nunca el raw. Upsert idempotente."""
    exp = session.get("expires_at") or (datetime.utcnow() + timedelta(days=SESSION_TTL_DAYS))
    if isinstance(exp, datetime) and exp.tzinfo is not None:
        exp = exp.replace(tzinfo=None)
    await get_db()[CONSUMED_COLLECTION].update_one(
        {"hash": presented_hash},
        {"$setOnInsert": {"hash": presented_hash, "sid": session["sid"],
                          "user_id": str(session.get("user_id", "")),
                          "created_at": datetime.utcnow(),
                          "expires_at": exp + timedelta(days=1)}},
        upsert=True)


async def _revoke_family_for_reuse(sid: str, user_id: str, request: Request):
    await get_db().auth_sessions.update_one({"sid": sid, "revoked_at": None},
                                            {"$set": reuse_revocation_update()})
    await audit_log(admin_user_id=str(user_id), action="refresh_reuse_detected",
                    resource_type="session", resource_id=sid, request=request)
    logger.warning("refresh reuse detected sid=%s", sid)


def _issue_access(session: dict, email: str) -> str:
    """Access JWT con el MISMO sid; exp acotado a la expiración de la sesión."""
    import uuid as _uuid
    now = datetime.utcnow()
    ses_exp = session.get("expires_at")
    if isinstance(ses_exp, datetime) and ses_exp.tzinfo is not None:
        ses_exp = ses_exp.replace(tzinfo=None)
    exp = min(now + timedelta(days=SESSION_TTL_DAYS), ses_exp) if ses_exp else now + timedelta(days=SESSION_TTL_DAYS)
    payload = {
        "user_id": str(session["user_id"]), "sub": str(session["user_id"]),
        "email": email, "role": session.get("role", ""), "sid": session["sid"],
        "jti": _uuid.uuid4().hex, "iat": now, "exp": exp, "type": "marketplace",
    }
    return jwt.encode(payload, TENANT_JWT_SECRET, algorithm="HS256")


async def _session_from_bearer(request: Request) -> Optional[dict]:
    """Extrae la sesión sid del access JWT del header. Legacy (sin sid) ⇒ None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1], TENANT_JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None  # raw legacy user_sessions tokens caen aquí ⇒ rechazados
    sid = payload.get("sid")
    if not sid:
        return None  # JWT legacy sin sid: NO puede usar refresh
    ses = await get_db().auth_sessions.find_one({"sid": sid})
    if ses and str(ses.get("user_id")) != str(payload.get("user_id", "")):
        return None
    return ses


async def _user_email(session: dict) -> Optional[str]:
    try:
        user = await get_db().app_users.find_one({"_id": ObjectId(session["user_id"])}, {"email": 1})
    except Exception:
        user = None
    return (user or {}).get("email")


@router.post("/auth/refresh")
async def refresh(request: Request, body: RefreshRequest):
    if not refresh_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    db = get_db()
    await _ensure_indexes()
    presented_hash = hash_refresh_token(body.refresh_token) if body.refresh_token else None

    # Localizar sesión: por hash del refresh, o por bearer sid (bootstrap)
    session = None
    if presented_hash:
        session = await db.auth_sessions.find_one({"$or": [
            {"refresh_token_hash": presented_hash},
            {"refresh_prev_hash": presented_hash},
            {"rotated_hashes": presented_hash},
        ]})
        if session is None:
            # D2: reuse ANTIGUO (>cap de rotated_hashes) — buscar en consumidos
            consumed = await db[CONSUMED_COLLECTION].find_one({"hash": presented_hash})
            if consumed:
                await _revoke_family_for_reuse(consumed["sid"], consumed.get("user_id", ""), request)
            raise GENERIC_401
    else:
        session = await _session_from_bearer(request)
        if session is None:
            raise GENERIC_401

    await check_rate_limit_persistent("auth_refresh", session.get("sid", "?"),
                                      max_requests=30, window_seconds=600)

    action = classify_refresh_attempt(session, presented_hash)

    if action == REUSE_DETECTED:
        await _revoke_family_for_reuse(session["sid"], session.get("user_id", ""), request)
        raise GENERIC_401

    if action == DENY:
        raise GENERIC_401

    email = await _user_email(session)
    if not email:
        raise GENERIC_401

    # ── D1: semántica exacta de grace — RESPUESTA IDEMPOTENTE ──
    # Request B (replay de R_n dentro de 60s): re-derivamos R_{n+1} con la cadena
    # HMAC determinista y devolvemos EL MISMO refresh que recibió Request A.
    # NO se rota de nuevo, NO se genera R3, R2 sigue siendo el token vigente,
    # NO hace falta almacenar R2 raw (se re-deriva de R_n + clave del servidor).
    # Replay repetido de R_n durante la grace siempre produce la MISMA respuesta
    # (cero acuñación de tokens nuevos). Pasada la grace, R_n ⇒ REUSE ⇒ revocación.
    if action == GRACE_ROTATE:
        rederived = next_refresh_token(body.refresh_token)
        if hash_refresh_token(rederived) != session.get("refresh_token_hash"):
            # La cadena avanzó más allá (R_{n+1} ya fue consumido): generación vieja
            await _revoke_family_for_reuse(session["sid"], session.get("user_id", ""), request)
            raise GENERIC_401
        return {"success": True, "access_token": _issue_access(session, email),
                "refresh_token": rederived, "token_type": "bearer"}

    if action == BOOTSTRAP:
        new_raw = generate_refresh_token()          # generación 1: CSPRNG puro
        update = bootstrap_update(session, hash_refresh_token(new_raw))
        # Guard atómico del bootstrap: solo UNA de dos carreras simultáneas gana
        guard = {"sid": session["sid"], "revoked_at": None, "refresh_token_hash": None}
    else:  # ROTATE
        new_raw = next_refresh_token(body.refresh_token)  # cadena determinista D1
        update = rotation_update(session, hash_refresh_token(new_raw))
        guard = {"sid": session["sid"], "revoked_at": None,
                 "refresh_token_hash": session.get("refresh_token_hash")}

    res = await db.auth_sessions.update_one(guard, {"$set": update})
    if res.modified_count != 1:
        raise GENERIC_401  # carrera perdida / revocada en el interín

    if action == ROTATE:
        await _mark_consumed(presented_hash, session)  # D2: registro permanente hasta expiry

    return {
        "success": True,
        "access_token": _issue_access(session, email),
        "refresh_token": new_raw,
        "token_type": "bearer",
    }
