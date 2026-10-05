"""
Phase 1 — Security primitives: persistent rate limiting, IP/OTP hashing,
admin audit log. Central module — do NOT duplicate this logic in routers.
"""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from pymongo import ReturnDocument

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
    """Atomically reserve one request in a DB-backed fixed window.

    A single find-and-update removes the count-then-insert race across workers
    and replicas. Identifiers are hashed before storage. Blocked requests still
    increment the bounded-time counter, which prevents retry storms from
    reopening the window.
    """
    if max_requests < 1 or window_seconds < 1:
        raise ValueError("rate limit values must be positive")

    now = datetime.now(timezone.utc)
    bucket_start_epoch = int(now.timestamp()) // window_seconds * window_seconds
    bucket_id = hashlib.sha256(
        f"{endpoint}\0{key}\0{bucket_start_epoch}".encode()
    ).hexdigest()
    bucket_start = datetime.fromtimestamp(bucket_start_epoch, tz=timezone.utc)
    expires_at = bucket_start + timedelta(seconds=window_seconds * 2)

    doc = await get_db().rate_limit_windows.find_one_and_update(
        {"_id": bucket_id},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {
                "endpoint": endpoint,
                "created_at": bucket_start,
                "expires_at": expires_at,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not doc or int(doc.get("count", max_requests + 1)) > max_requests:
        raise HTTPException(status_code=429, detail=RATE_LIMIT_429)


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
