"""Mongo-backed renewable lease for singleton opportunity scans."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


@dataclass(frozen=True)
class ScanLease:
    document_id: str
    owner: str
    generation: int
    ttl_seconds: int


def _document_id(name: str) -> str:
    if not isinstance(name, str) or not name or not name.replace("_", "").isalnum():
        raise ValueError("opportunity_scan_name_invalid")
    return f"opportunity_scan_lease:{name}"


def _now(value=None):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("opportunity_scan_clock_must_be_aware")
    return current.astimezone(timezone.utc)


async def acquire_scan_lease(db, name: str, *, ttl_seconds: int = 14400,
                             now=None, owner: str | None = None) -> ScanLease | None:
    document_id = _document_id(name)
    if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 21600:
        raise ValueError("opportunity_scan_ttl_invalid")
    current = _now(now)
    identity = owner or secrets.token_urlsafe(18)
    try:
        document = await db.rental_config.find_one_and_update(
            {"_id": document_id, "$or": [
                {"lease_until": {"$lte": current}},
                {"lease_until": {"$exists": False}},
            ]},
            {"$set": {"lease_owner": identity,
                      "lease_until": current + timedelta(seconds=ttl_seconds),
                      "updated_at": current},
             "$inc": {"lease_generation": 1}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None
    if not document or document.get("lease_owner") != identity:
        return None
    generation = document.get("lease_generation")
    if type(generation) is not int or generation < 1:
        raise RuntimeError("opportunity_scan_lease_generation_invalid")
    return ScanLease(document_id, identity, generation, ttl_seconds)


async def get_scan_lease_status(db, name: str, *, now=None) -> dict:
    """Return operational lease state without exposing the owner secret."""
    current = _now(now)
    document = await db.rental_config.find_one(
        {"_id": _document_id(name)},
        {"lease_owner": 1, "lease_until": 1, "lease_generation": 1},
    ) or {}
    lease_until = document.get("lease_until")
    if isinstance(lease_until, datetime) and lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    active = bool(document.get("lease_owner")) and isinstance(lease_until, datetime) \
        and lease_until > current
    generation = document.get("lease_generation")
    return {
        "running": active,
        "lease_expires_at": lease_until.isoformat() if active else "",
        "lease_generation": generation if type(generation) is int and generation >= 0 else 0,
    }


async def renew_scan_lease(db, lease: ScanLease, *, now=None) -> bool:
    current = _now(now)
    result = await db.rental_config.update_one(
        {"_id": lease.document_id, "lease_owner": lease.owner,
         "lease_generation": lease.generation, "lease_until": {"$gt": current}},
        {"$set": {"lease_until": current + timedelta(seconds=lease.ttl_seconds),
                  "updated_at": current}},
    )
    return result.modified_count == 1


async def release_scan_lease(db, lease: ScanLease) -> bool:
    result = await db.rental_config.update_one(
        {"_id": lease.document_id, "lease_owner": lease.owner,
         "lease_generation": lease.generation},
        {"$unset": {"lease_owner": "", "lease_until": ""},
         "$set": {"released_at": datetime.now(timezone.utc)}},
    )
    return result.modified_count == 1
