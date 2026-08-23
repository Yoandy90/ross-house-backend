"""Refresh Tokens — Fase A (backend only). Lógica pura, testeable.

Diseño aprobado (REFRESH_TOKEN_DESIGN.md) + ajustes del checkpoint:
- Refresh token: opaco, 256 bits CSPRNG, NUNCA JWT. Server guarda SOLO hash SHA-256.
- Familia = auth_sessions doc (Phase 1). Campos aditivos, sesiones viejas intactas.
- Rotación en cada refresh; el hash anterior pasa a rotated_hashes[] (cap 10).
- Reuse detection: token rotado fuera de la grace window ⇒ revocar familia + audit.
- Grace window 60s: SOLO el hash de la generación INMEDIATAMENTE anterior
  (refresh_prev_hash) dentro de 60s de la rotación se trata como carrera legítima
  (retry/concurrencia) y NO revoca; se rota normalmente. Cualquier hash más viejo
  (en rotated_hashes[]) o el prev_hash fuera de los 60s = REUSE ⇒ revocación.
- Expiración ABSOLUTA: refresh_expires_at = expires_at de la sesión (30d Fase A).
  La rotación NO extiende la vida de la sesión (sin renovación infinita).
- El access emitido por refresh mantiene el MISMO sid (jamás crea sesión nueva ⇒
  jamás salta 2FA) y su exp se acota a la expiración de la sesión.
- Legacy user_sessions (tokens raw) NO puede usar /auth/refresh: solo sesiones
  sid modernas de auth_sessions.

Feature flags (defaults preservan producción actual):
- REFRESH_TOKENS_ENABLED   (default false → endpoint deshabilitado 404)
- ALLOW_LEGACY_USER_SESSIONS (default true → fallback user_sessions sigue vivo;
  Fase B lo apagará)
- REQUIRE_SESSION_SID      (ya existía; Fase B lo activará)
"""
import os
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

GRACE_SECONDS = 60
ROTATED_HASHES_CAP = 10

# Acciones que puede decidir la lógica pura
ROTATE = "ROTATE"                 # hash actual válido → rotar
GRACE_ROTATE = "GRACE_ROTATE"     # prev hash dentro de grace → carrera legítima, rotar
REUSE_DETECTED = "REUSE_DETECTED" # token rotado viejo / prev fuera de grace → revocar familia
BOOTSTRAP = "BOOTSTRAP"           # sesión sin refresh aún → emitir primer refresh (1 sola vez)
DENY = "DENY"                     # revocada/expirada/mismatch → 401 genérico


def refresh_enabled() -> bool:
    return os.environ.get("REFRESH_TOKENS_ENABLED", "").lower() == "true"


def legacy_user_sessions_allowed() -> bool:
    # Default true = comportamiento actual de producción. Fase B: poner false.
    return os.environ.get("ALLOW_LEGACY_USER_SESSIONS", "true").lower() != "false"


def generate_refresh_token() -> str:
    """256 bits CSPRNG, url-safe. El valor raw JAMÁS se persiste ni se loggea."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def classify_refresh_attempt(session: dict, presented_hash: str | None,
                             now: datetime | None = None) -> str:
    """Decisión pura sobre un intento de refresh contra una sesión auth_sessions.

    presented_hash=None ⇒ intento de BOOTSTRAP (con access sid válido): solo se
    permite si la sesión aún no tiene refresh_token_hash (una sola vez por sesión).
    """
    now = now or datetime.now(timezone.utc)

    if session is None:
        return DENY
    if session.get("revoked_at") is not None:
        return DENY
    exp = _aware(session.get("expires_at"))
    if exp and exp < now:
        return DENY

    current_hash = session.get("refresh_token_hash")

    # ── Bootstrap (una vez por sesión) ──
    if presented_hash is None:
        return BOOTSTRAP if not current_hash else DENY

    # ── Refresh normal ──
    if not current_hash:
        return DENY  # la sesión nunca emitió refresh: nada que rotar
    r_exp = _aware(session.get("refresh_expires_at"))
    if r_exp and r_exp < now:
        return DENY  # expiración absoluta del refresh

    if presented_hash == current_hash:
        return ROTATE

    prev_hash = session.get("refresh_prev_hash")
    rotated_at = _aware(session.get("refresh_rotated_at"))
    if prev_hash and presented_hash == prev_hash:
        if rotated_at and (now - rotated_at).total_seconds() <= GRACE_SECONDS:
            return GRACE_ROTATE  # retry/carrera legítima
        return REUSE_DETECTED    # prev fuera de grace ⇒ sospechoso

    if presented_hash in (session.get("rotated_hashes") or []):
        return REUSE_DETECTED    # generación vieja ⇒ robo

    return DENY  # hash desconocido: 401 genérico (sin filtrar información)


def rotation_update(session: dict, new_hash: str, now: datetime | None = None) -> dict:
    """Campos $set para rotar el refresh de una sesión. NUNCA incluye el raw."""
    now = now or datetime.now(timezone.utc)
    old = session.get("refresh_token_hash")
    rotated = list(session.get("rotated_hashes") or [])
    prev = session.get("refresh_prev_hash")
    if prev and prev not in rotated:
        rotated.append(prev)
    rotated = rotated[-ROTATED_HASHES_CAP:]
    return {
        "refresh_token_hash": new_hash,
        "refresh_prev_hash": old,
        "rotated_hashes": rotated,
        "refresh_rotated_at": now,
        "refresh_generation": int(session.get("refresh_generation") or 0) + 1,
    }


def bootstrap_update(session: dict, new_hash: str, now: datetime | None = None) -> dict:
    """Campos $set para el PRIMER refresh de la sesión (bootstrap).
    refresh_expires_at = expires_at de la sesión ⇒ expiración absoluta,
    la rotación posterior nunca la extiende."""
    now = now or datetime.now(timezone.utc)
    return {
        "refresh_token_hash": new_hash,
        "refresh_prev_hash": None,
        "rotated_hashes": [],
        "refresh_family_id": session.get("sid"),
        "refresh_issued_at": now,
        "refresh_expires_at": session.get("expires_at"),  # ABSOLUTA (30d Fase A)
        "refresh_rotated_at": None,
        "refresh_generation": 1,
    }


def reuse_revocation_update(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "revoked_at": now,
        "revoked_reason": "refresh_reuse",
        "refresh_reuse_detected_at": now,
        "refresh_revoked_at": now,
    }
