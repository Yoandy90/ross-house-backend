"""Short-lived serialized mutation claim for one property.

The property document is the shared serialization point for operations that can
change lease authority or unit topology across collections. Claims are acquired
with one MongoDB CAS and released by exact token. Expired claims can be taken
over after a conservative timeout so a crashed request cannot permanently lock
a property.
"""
from datetime import datetime, timedelta
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException

from rental.shared import get_db

_LOCK_FIELD = "mutation_lock"
_LOCK_TTL_SECONDS = 120


def _property_oid(property_id: str) -> ObjectId:
    if not ObjectId.is_valid(str(property_id or "")):
        raise HTTPException(status_code=400, detail="lease_property_invalid")
    return ObjectId(str(property_id))


async def acquire_property_mutation_lock(property_id: str, operation: str, actor: str = "") -> str:
    """Acquire the per-property serialization claim or fail closed with 409."""
    oid = _property_oid(property_id)
    db = get_db()
    now = datetime.utcnow()
    token = uuid4().hex
    expires_at = now + timedelta(seconds=_LOCK_TTL_SECONDS)
    result = await db.properties.update_one(
        {
            "_id": oid,
            "$or": [
                {_LOCK_FIELD: {"$exists": False}},
                {_LOCK_FIELD: None},
                {f"{_LOCK_FIELD}.expires_at": {"$lt": now}},
            ],
        },
        {
            "$set": {
                _LOCK_FIELD: {
                    "token": token,
                    "operation": str(operation or "unknown"),
                    "actor": str(actor or ""),
                    "acquired_at": now,
                    "expires_at": expires_at,
                }
            }
        },
    )
    if getattr(result, "matched_count", 0) != 1:
        existing = await db.properties.find_one({"_id": oid}, {"_id": 1})
        if not existing:
            raise HTTPException(status_code=404, detail="lease_property_not_found")
        raise HTTPException(status_code=409, detail="property_mutation_in_progress")
    return token


async def release_property_mutation_lock(property_id: str, token: str) -> None:
    """Release only the exact claim owned by this request; never clear another."""
    if not token or not ObjectId.is_valid(str(property_id or "")):
        return
    await get_db().properties.update_one(
        {"_id": ObjectId(str(property_id)), f"{_LOCK_FIELD}.token": token},
        {"$unset": {_LOCK_FIELD: ""}},
    )
