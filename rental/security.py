"""
Phase 1 — Security primitives: persistent rate limiting, IP/OTP hashing,
admin audit log. Central module — do NOT duplicate this logic in routers.
"""
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from .shared import get_db

_IP_SALT = os.environ.get("VISITOR_IP_SALT", "rhr-static-salt")

RATE_LIMIT_429 = "Demasiados intentos. Intenta de nuevo más tarde."


def hash_ip(ip: str) -> str:
    """Never store raw IPs — salted SHA-256."""
    return hashlib.sha256(f"{_IP_SALT}:{ip or 'unknown'}".encode()).hexdigest()[:32]


def hash_otp(code: str) -> str:
    return hashlib.sha256(f"{_IP_SALT}:otp:{code}".encode()).hexdigest()


def client_ip_hash(request: Request) -> str:
    ip = request.client.host if request and request.client else "unknown"
    return hash_ip(ip)


async def check_rate_limit_persistent(endpoint: str, key: str,
                                      max_requests: int = 5,
                                      window_seconds: int = 300) -> None:
    """DB-backed rate limiter — survives restarts/redeploys and works across
    replicas. Key should already be hashed/normalized (ip_hash, email, phone).
    Raises a consistent 429 (no enumeration hints)."""
    db = get_db()
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    window_start = now - timedelta(seconds=window_seconds)
    coll = db.rate_limit_events
    count = await coll.count_documents({
        "endpoint": endpoint, "key": key, "created_at": {"$gte": window_start}})
    if count >= max_requests:
        raise HTTPException(status_code=429, detail=RATE_LIMIT_429)
    await coll.insert_one({"endpoint": endpoint, "key": key, "created_at": now})


# ══════════════════════════ ADMIN AUDIT LOG ══════════════════════════

_FORBIDDEN_META_KEYS = {"password", "password_hash", "token", "jwt", "otp",
                        "code", "api_key", "api_token", "secret", "ssn",
                        "account_number", "routing_number", "card_number",
                        "authorization", "screenshot_base64"}


def _sanitize_meta(meta: Optional[dict]) -> dict:
    if not isinstance(meta, dict):
        return {}
    out = {}
    for k, v in meta.items():
        if any(bad in k.lower() for bad in _FORBIDDEN_META_KEYS):
            out[k] = "[REDACTED]"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v[:300] if isinstance(v, str) else v
        else:
            out[k] = str(v)[:300]
    return out


async def audit_log(*, admin_user_id: str, action: str,
                    resource_type: str = "", resource_id: str = "",
                    result: str = "success", request: Optional[Request] = None,
                    metadata: Optional[dict] = None) -> None:
    """Append-only admin audit trail. NEVER raises (auditing must not break
    the underlying operation)."""
    try:
        doc = {
            "timestamp": datetime.now(timezone.utc),
            "admin_user_id": str(admin_user_id or ""),
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id or ""),
            "result": result,
            "request_id": request.headers.get("x-request-id", "") if request else "",
            "ip_hash": client_ip_hash(request) if request else "",
            "metadata": _sanitize_meta(metadata),
        }
        await get_db().admin_audit_logs.insert_one(doc)
    except Exception:  # noqa: BLE001 — never break the operation being audited
        import logging
        logging.exception("audit_log write failed")
