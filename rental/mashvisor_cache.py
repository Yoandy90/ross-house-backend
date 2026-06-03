"""
Mashvisor API Cache Service
============================
MongoDB-based caching layer for Mashvisor API responses.
Eliminates redundant API calls when multiple users search for the same data.

Cache TTLs:
  - listings:         4 hours  (properties change moderately)
  - overview/market:  24 hours (aggregate stats are stable)
  - neighborhoods:    12 hours
  - property-analysis: 12 hours
  - top-properties:   6 hours

MongoDB TTL index on `expires_at` auto-deletes expired documents.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("mashvisor_cache")

# ── TTL Configuration (in hours) ──────────────────────────────────────────────
CACHE_TTL = {
    "listings":          4,
    "overview":          24,
    "neighborhoods":     12,
    "property_analysis": 12,
    "top_properties":    6,
    "city_market":       24,
    "default":           4,
}

# ── Module-level DB reference ─────────────────────────────────────────────────
_db = None

# ── Stats counters (in-memory, reset on restart) ─────────────────────────────
_stats = {
    "hits": 0,
    "misses": 0,
    "sets": 0,
    "errors": 0,
}


def init_cache(db):
    """Initialize cache with database reference and ensure TTL index."""
    global _db
    _db = db
    logger.info("✅ Mashvisor Cache initialized")


async def ensure_indexes():
    """Create TTL index on mashvisor_cache collection. Call once at startup."""
    if _db is None:
        return
    try:
        await _db.mashvisor_cache.create_index(
            "expires_at",
            expireAfterSeconds=0,
            name="ttl_auto_expire",
        )
        await _db.mashvisor_cache.create_index(
            "cache_key",
            unique=True,
            name="unique_cache_key",
        )
        logger.info("✅ Mashvisor Cache indexes ensured")
    except Exception as e:
        logger.warning(f"⚠️ Cache index creation: {e}")


def _build_cache_key(category: str, path: str, params: Optional[dict] = None) -> str:
    """Build a deterministic cache key from the request parameters."""
    key_data = {
        "category": category,
        "path": path,
        "params": params or {},
    }
    raw = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_cached(category: str, path: str, params: Optional[dict] = None) -> Optional[dict]:
    """
    Try to retrieve a cached response.
    Returns the cached data dict or None if miss/expired.
    """
    global _stats
    if _db is None:
        return None

    cache_key = _build_cache_key(category, path, params)

    try:
        doc = await _db.mashvisor_cache.find_one(
            {"cache_key": cache_key, "expires_at": {"$gt": datetime.now(timezone.utc)}}
        )
        if doc:
            _stats["hits"] += 1
            logger.info(f"🟢 CACHE HIT: {category} | {path} | params={params}")
            return doc.get("data")
        else:
            _stats["misses"] += 1
            logger.info(f"🔴 CACHE MISS: {category} | {path} | params={params}")
            return None
    except Exception as e:
        _stats["errors"] += 1
        logger.error(f"❌ Cache read error: {e}")
        return None


async def set_cached(category: str, path: str, data: dict, params: Optional[dict] = None):
    """
    Store a response in the cache with the appropriate TTL.
    Uses upsert to handle race conditions gracefully.
    """
    global _stats
    if _db is None:
        return

    cache_key = _build_cache_key(category, path, params)
    ttl_hours = CACHE_TTL.get(category, CACHE_TTL["default"])
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    try:
        await _db.mashvisor_cache.update_one(
            {"cache_key": cache_key},
            {
                "$set": {
                    "cache_key": cache_key,
                    "category": category,
                    "path": path,
                    "params": params or {},
                    "data": data,
                    "expires_at": expires_at,
                    "cached_at": datetime.now(timezone.utc),
                    "ttl_hours": ttl_hours,
                }
            },
            upsert=True,
        )
        _stats["sets"] += 1
        logger.info(f"💾 CACHE SET: {category} | TTL={ttl_hours}h | {path}")
    except Exception as e:
        _stats["errors"] += 1
        logger.error(f"❌ Cache write error: {e}")


async def get_cache_stats() -> dict:
    """Return cache statistics for the admin dashboard."""
    total_docs = 0
    categories = {}

    if _db is not None:
        try:
            total_docs = await _db.mashvisor_cache.count_documents({})
            pipeline = [
                {"$match": {"expires_at": {"$gt": datetime.now(timezone.utc)}}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            ]
            async for doc in _db.mashvisor_cache.aggregate(pipeline):
                categories[doc["_id"]] = doc["count"]
        except Exception:
            pass

    total_requests = _stats["hits"] + _stats["misses"]
    hit_rate = round((_stats["hits"] / total_requests * 100), 1) if total_requests > 0 else 0

    return {
        "total_cached_items": total_docs,
        "active_by_category": categories,
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "sets": _stats["sets"],
        "errors": _stats["errors"],
        "total_requests": total_requests,
        "hit_rate_percent": hit_rate,
        "api_calls_saved": _stats["hits"],
        "ttl_config": CACHE_TTL,
    }


async def clear_cache(category: Optional[str] = None) -> int:
    """
    Clear cache entries. If category is provided, only clear that category.
    Returns the number of documents deleted.
    """
    global _stats
    if _db is None:
        return 0

    try:
        query = {"category": category} if category else {}
        result = await _db.mashvisor_cache.delete_many(query)
        deleted = result.deleted_count
        if not category:
            _stats = {"hits": 0, "misses": 0, "sets": 0, "errors": 0}
        logger.info(f"🗑️ CACHE CLEARED: {category or 'ALL'} | {deleted} items deleted")
        return deleted
    except Exception as e:
        logger.error(f"❌ Cache clear error: {e}")
        return 0
