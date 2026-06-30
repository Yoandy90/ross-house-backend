"""
Visitor Analytics Router — "Visitor Intelligence" Module
=========================================================
Self-hosted, privacy-first website analytics for rosshouserentals.com.
No third-party trackers. All data lives in our MongoDB.

# Collections
  - visitor_sessions  : one document per browser session
      { _id, session_id, visitor_id (hash), first_seen, last_seen,
        pages_count, events_count, device, browser, os, referrer,
        utm_source, utm_medium, utm_campaign, country, country_code,
        region, city, lat, lon, timezone, is_bot, lead_id (linked) }

  - visitor_events    : every pageview / custom event (TTL 90d)
      { _id, session_id, visitor_id, ts, type ('page'|'event'),
        path, title, referrer, event_name, event_data,
        duration_ms, scroll_pct }

  - geo_cache         : IP -> geo lookup (TTL 30d)

# Public endpoints (no auth, called by browser)
  POST /api/public/track/session   start/identify a session
  POST /api/public/track/page      log a pageview
  POST /api/public/track/event     log a custom event
  POST /api/public/track/heartbeat keep session 'live'

# Admin endpoints (auth_admin)
  GET  /api/admin/analytics/live          who is online RIGHT NOW
  GET  /api/admin/analytics/overview      KPIs for a date range
  GET  /api/admin/analytics/top-pages
  GET  /api/admin/analytics/sources       referrer + UTM breakdown
  GET  /api/admin/analytics/geo           country/city aggregation
  GET  /api/admin/analytics/devices       device/browser/os split
  GET  /api/admin/analytics/timeline      hourly counts for charts
  GET  /api/admin/analytics/funnel        visit → property → lead
  GET  /api/admin/analytics/sessions      paginated list (drill-down)
  GET  /api/admin/analytics/sessions/{id} full session + event trail
"""
from __future__ import annotations

import os
import re
import hashlib
import logging
import httpx
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Visitor Analytics"])

# ── Config ───────────────────────────────────────────────────────────────────
LIVE_WINDOW_SECONDS = 60          # "online now" threshold
SESSION_IDLE_SECONDS = 30 * 60    # 30min idle = new session
IP_SALT = os.environ.get("VISITOR_IP_SALT", "ross-house-2026")
GEO_PROVIDER = os.environ.get("VISITOR_GEO_PROVIDER", "ipapi")  # ipapi.co (free 1000/day)

BOT_RE = re.compile(
    r"(?i)(bot|crawl|spider|slurp|googlebot|bingbot|duckduckbot|yandex|baidu|"
    r"facebookexternalhit|whatsapp|telegrambot|linkedinbot|twitterbot|"
    r"semrush|ahrefs|mj12|petalbot|applebot|headlesschrome|phantomjs|"
    r"curl/|wget/|python-requests|node-fetch|axios/|monitor|uptime)"
)

MOBILE_RE  = re.compile(r"(?i)(mobile|android|iphone|ipod)")
TABLET_RE  = re.compile(r"(?i)(tablet|ipad)")

BROWSER_PATTERNS = [
    ("Edge",    r"Edg/"),
    ("Opera",   r"OPR/|Opera/"),
    ("Chrome",  r"Chrome/"),
    ("Firefox", r"Firefox/"),
    ("Safari",  r"Safari/"),
]
OS_PATTERNS = [
    ("Windows", r"Windows NT"),
    ("macOS",   r"Mac OS X|Macintosh"),
    ("iOS",     r"iPhone|iPad|iPod"),
    ("Android", r"Android"),
    ("Linux",   r"Linux"),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{IP_SALT}:{ip}".encode()).hexdigest()[:32]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt.tzinfo is None else dt.isoformat()


def _parse_user_agent(ua: str) -> Dict[str, str]:
    if not ua:
        return {"device": "Unknown", "browser": "Unknown", "os": "Unknown"}
    if TABLET_RE.search(ua):
        device = "Tablet"
    elif MOBILE_RE.search(ua):
        device = "Mobile"
    else:
        device = "Desktop"
    browser = "Other"
    for name, pat in BROWSER_PATTERNS:
        if re.search(pat, ua):
            browser = name
            break
    os_name = "Other"
    for name, pat in OS_PATTERNS:
        if re.search(pat, ua):
            os_name = name
            break
    return {"device": device, "browser": browser, "os": os_name}


def _is_bot(ua: str) -> bool:
    return bool(ua and BOT_RE.search(ua))


def _extract_referrer(ref: str) -> Dict[str, Optional[str]]:
    if not ref:
        return {"referrer_host": None, "referrer_url": None}
    try:
        u = urlparse(ref)
        host = (u.netloc or "").lower()
        # Treat self-referrals as direct
        if "rosshouserentals" in host:
            return {"referrer_host": None, "referrer_url": None}
        return {"referrer_host": host or None, "referrer_url": ref[:500]}
    except Exception:
        return {"referrer_host": None, "referrer_url": ref[:500]}


async def _lookup_geo(db, ip: str) -> Dict[str, Any]:
    """Return {country, country_code, region, city, lat, lon, timezone}.

    Uses a permanent local cache to avoid burning the free-tier quota.
    """
    empty = {"country": None, "country_code": None, "region": None,
             "city": None, "lat": None, "lon": None, "timezone": None}
    if not ip or ip in ("127.0.0.1", "::1") or ip.startswith("192.168.") or ip.startswith("10."):
        return {**empty, "country": "Local", "country_code": "LO", "city": "Localhost"}

    cached = await db.geo_cache.find_one({"_id": ip})
    if cached:
        return {k: cached.get(k) for k in empty.keys()}

    geo = empty.copy()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            if GEO_PROVIDER == "ipapi":
                # https://ipapi.co/{ip}/json/  (free 1000/day, no key)
                r = await client.get(f"https://ipapi.co/{ip}/json/")
                if r.status_code == 200:
                    j = r.json()
                    geo = {
                        "country":      j.get("country_name"),
                        "country_code": j.get("country_code"),
                        "region":       j.get("region"),
                        "city":         j.get("city"),
                        "lat":          j.get("latitude"),
                        "lon":          j.get("longitude"),
                        "timezone":     j.get("timezone"),
                    }
            else:
                # ip-api.com (free ~45/min)
                r = await client.get(f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,lat,lon,timezone")
                if r.status_code == 200:
                    j = r.json()
                    if j.get("status") == "success":
                        geo = {
                            "country":      j.get("country"),
                            "country_code": j.get("countryCode"),
                            "region":       j.get("regionName"),
                            "city":         j.get("city"),
                            "lat":          j.get("lat"),
                            "lon":          j.get("lon"),
                            "timezone":     j.get("timezone"),
                        }
    except Exception as e:
        logger.warning(f"[visitor] geo lookup failed for {ip}: {e}")

    # Cache (TTL via expires_at + Mongo TTL index)
    await db.geo_cache.update_one(
        {"_id": ip},
        {"$set": {**geo, "cached_at": _now(),
                  "expires_at": _now() + timedelta(days=30)}},
        upsert=True,
    )
    return geo


async def _ensure_indexes(db):
    try:
        await db.visitor_events.create_index([("ts", -1)])
        await db.visitor_events.create_index("session_id")
        await db.visitor_events.create_index([("ts", 1)],
            expireAfterSeconds=90 * 24 * 3600)  # 90d TTL
        await db.visitor_sessions.create_index([("last_seen", -1)])
        await db.visitor_sessions.create_index("session_id", unique=True)
        await db.visitor_sessions.create_index("visitor_id")
        await db.geo_cache.create_index([("expires_at", 1)], expireAfterSeconds=0)
    except Exception as e:
        logger.warning(f"[visitor] index creation: {e}")


_indexes_ready = False


async def _ensure_once(db):
    global _indexes_ready
    if not _indexes_ready:
        await _ensure_indexes(db)
        _indexes_ready = True


def _client_ip(request: Request) -> str:
    # Trust X-Forwarded-For (Railway/Vercel add it). Take first non-empty.
    xff = request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


# ── Pydantic models ──────────────────────────────────────────────────────────

class TrackSessionPayload(BaseModel):
    session_id: Optional[str] = None
    screen_w: Optional[int] = None
    screen_h: Optional[int] = None
    lang: Optional[str] = None
    referrer: Optional[str] = None
    landing_path: Optional[str] = None


class TrackPagePayload(BaseModel):
    session_id: str
    path: str = Field(..., max_length=500)
    title: Optional[str] = Field(None, max_length=300)
    referrer: Optional[str] = Field(None, max_length=500)
    duration_ms: Optional[int] = None      # of previous page
    scroll_pct: Optional[int] = None       # max scroll on previous page


class TrackEventPayload(BaseModel):
    session_id: str
    name: str = Field(..., max_length=80)
    data: Optional[Dict[str, Any]] = None
    path: Optional[str] = Field(None, max_length=500)


class HeartbeatPayload(BaseModel):
    session_id: str
    path: Optional[str] = Field(None, max_length=500)


# ── Public tracking endpoints ────────────────────────────────────────────────

@router.post("/public/track/session")
async def track_session(payload: TrackSessionPayload, request: Request):
    db = get_db()
    await _ensure_once(db)
    ua = request.headers.get("user-agent", "")
    if _is_bot(ua):
        return {"ok": True, "ignored": "bot"}

    ip = _client_ip(request)
    visitor_id = _hash_ip(ip)
    ua_info = _parse_user_agent(ua)
    ref = _extract_referrer(payload.referrer or "")

    # UTM extraction (from landing path if present)
    utm = {"utm_source": None, "utm_medium": None, "utm_campaign": None}
    if payload.landing_path:
        try:
            q = parse_qs(urlparse(payload.landing_path).query)
            utm = {
                "utm_source":   (q.get("utm_source")   or [None])[0],
                "utm_medium":   (q.get("utm_medium")   or [None])[0],
                "utm_campaign": (q.get("utm_campaign") or [None])[0],
            }
        except Exception:
            pass

    # Reuse existing session if alive, otherwise start fresh
    if payload.session_id:
        existing = await db.visitor_sessions.find_one({"_id": payload.session_id})
        if existing:
            await db.visitor_sessions.update_one(
                {"_id": payload.session_id},
                {"$set": {"last_seen": _now()}},
            )
            return {"ok": True, "session_id": payload.session_id, "is_new": False}

    geo = await _lookup_geo(db, ip)
    sid = payload.session_id or hashlib.sha256(f"{visitor_id}:{_now().timestamp()}".encode()).hexdigest()[:24]
    doc = {
        "_id": sid,
        "session_id": sid,
        "visitor_id": visitor_id,
        "first_seen": _now(),
        "last_seen": _now(),
        "pages_count": 0,
        "events_count": 0,
        "is_bot": False,
        "user_agent": ua[:500],
        "lang": payload.lang,
        "screen_w": payload.screen_w,
        "screen_h": payload.screen_h,
        "referrer_host": ref["referrer_host"],
        "referrer_url": ref["referrer_url"],
        "landing_path": (payload.landing_path or "")[:500],
        **ua_info,   # device, browser, os
        **utm,
        **geo,
        "lead_id": None,
    }
    await db.visitor_sessions.insert_one(doc)
    return {"ok": True, "session_id": sid, "is_new": True}


@router.post("/public/track/page")
async def track_page(payload: TrackPagePayload, request: Request):
    db = get_db()
    await _ensure_once(db)
    ua = request.headers.get("user-agent", "")
    if _is_bot(ua):
        return {"ok": True, "ignored": "bot"}
    sess = await db.visitor_sessions.find_one({"_id": payload.session_id})
    if not sess:
        # auto-start a session if the client sends a page before /session
        await track_session(TrackSessionPayload(session_id=payload.session_id,
                                                referrer=payload.referrer or "",
                                                landing_path=payload.path), request)
        sess = await db.visitor_sessions.find_one({"_id": payload.session_id})
        if not sess:
            return {"ok": False}
    now = _now()
    await db.visitor_events.insert_one({
        "_id": hashlib.sha256(f"{payload.session_id}:{now.timestamp()}:page:{payload.path}".encode()).hexdigest()[:24],
        "session_id": payload.session_id,
        "visitor_id": sess.get("visitor_id"),
        "ts": now,
        "type": "page",
        "path": payload.path[:500],
        "title": (payload.title or "")[:300],
        "referrer": (payload.referrer or "")[:500],
        "duration_ms": payload.duration_ms,
        "scroll_pct": payload.scroll_pct,
    })
    await db.visitor_sessions.update_one(
        {"_id": payload.session_id},
        {"$set": {"last_seen": now}, "$inc": {"pages_count": 1}},
    )
    return {"ok": True}


@router.post("/public/track/event")
async def track_event(payload: TrackEventPayload, request: Request):
    db = get_db()
    await _ensure_once(db)
    ua = request.headers.get("user-agent", "")
    if _is_bot(ua):
        return {"ok": True, "ignored": "bot"}
    sess = await db.visitor_sessions.find_one({"_id": payload.session_id})
    if not sess:
        return {"ok": False, "reason": "no_session"}
    now = _now()
    await db.visitor_events.insert_one({
        "_id": hashlib.sha256(f"{payload.session_id}:{now.timestamp()}:event:{payload.name}".encode()).hexdigest()[:24],
        "session_id": payload.session_id,
        "visitor_id": sess.get("visitor_id"),
        "ts": now,
        "type": "event",
        "event_name": payload.name[:80],
        "event_data": payload.data or {},
        "path": (payload.path or "")[:500],
    })
    await db.visitor_sessions.update_one(
        {"_id": payload.session_id},
        {"$set": {"last_seen": now}, "$inc": {"events_count": 1}},
    )
    return {"ok": True}


@router.post("/public/track/heartbeat")
async def track_heartbeat(payload: HeartbeatPayload, request: Request):
    db = get_db()
    await _ensure_once(db)
    ua = request.headers.get("user-agent", "")
    if _is_bot(ua):
        return {"ok": True, "ignored": "bot"}
    res = await db.visitor_sessions.update_one(
        {"_id": payload.session_id},
        {"$set": {"last_seen": _now(), "current_path": (payload.path or "")[:500]}},
    )
    return {"ok": res.matched_count > 0}


# ── Admin: aggregations ──────────────────────────────────────────────────────

def _range_to_dt(range_str: str) -> datetime:
    range_str = (range_str or "7d").lower()
    n = int(re.sub(r"[^\d]", "", range_str) or "7")
    if range_str.endswith("h"):
        return _now() - timedelta(hours=n)
    if range_str.endswith("m"):
        return _now() - timedelta(minutes=n)
    return _now() - timedelta(days=n)


@router.get("/admin/analytics/live")
async def admin_live(request: Request):
    await auth_admin(request)
    db = get_db()
    await _ensure_once(db)
    cutoff = _now() - timedelta(seconds=LIVE_WINDOW_SECONDS)
    cursor = db.visitor_sessions.find({"last_seen": {"$gte": cutoff}, "is_bot": {"$ne": True}}).sort("last_seen", -1)
    sessions: List[Dict[str, Any]] = []
    async for s in cursor:
        sessions.append({
            "session_id": s["_id"],
            "country": s.get("country"),
            "country_code": s.get("country_code"),
            "city": s.get("city"),
            "device": s.get("device"),
            "browser": s.get("browser"),
            "os": s.get("os"),
            "current_path": s.get("current_path") or s.get("landing_path"),
            "pages_count": s.get("pages_count", 0),
            "last_seen": _iso(s["last_seen"]) if isinstance(s.get("last_seen"), datetime) else s.get("last_seen"),
            "referrer_host": s.get("referrer_host"),
            "lead_id": s.get("lead_id"),
        })
    return {"online_now": len(sessions), "sessions": sessions, "as_of": _iso(_now())}


@router.get("/admin/analytics/overview")
async def admin_overview(request: Request, range: str = "7d"):
    await auth_admin(request)
    db = get_db()
    await _ensure_once(db)
    since = _range_to_dt(range)
    prev_since = since - (datetime.now(timezone.utc) - since)

    async def _stats(start: datetime, end: Optional[datetime] = None) -> Dict[str, Any]:
        match: Dict[str, Any] = {"first_seen": {"$gte": start}, "is_bot": {"$ne": True}}
        if end:
            match["first_seen"]["$lt"] = end
        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": None,
                "sessions": {"$sum": 1},
                "visitors": {"$addToSet": "$visitor_id"},
                "pages":    {"$sum": "$pages_count"},
                "events":   {"$sum": "$events_count"},
                "leads":    {"$sum": {"$cond": [{"$ifNull": ["$lead_id", False]}, 1, 0]}},
                "bounces":  {"$sum": {"$cond": [{"$lte": ["$pages_count", 1]}, 1, 0]}},
                "duration_sum": {"$sum": {"$dateDiff": {"startDate": "$first_seen", "endDate": "$last_seen", "unit": "second"}}},
            }},
        ]
        try:
            res = await db.visitor_sessions.aggregate(pipeline).to_list(1)
        except Exception:
            res = []
        if not res:
            return {"sessions": 0, "visitors": 0, "pages": 0, "events": 0, "leads": 0,
                    "bounce_rate": 0, "avg_duration_sec": 0}
        r = res[0]
        sess = r.get("sessions", 0)
        return {
            "sessions": sess,
            "visitors": len(r.get("visitors", [])),
            "pages":    r.get("pages", 0),
            "events":   r.get("events", 0),
            "leads":    r.get("leads", 0),
            "bounce_rate": round((r.get("bounces", 0) / sess) * 100, 1) if sess else 0,
            "avg_duration_sec": int(r.get("duration_sum", 0) / sess) if sess else 0,
        }

    current = await _stats(since)
    previous = await _stats(prev_since, since)

    def _delta(curr: float, prev: float) -> Optional[float]:
        if prev == 0:
            return None
        return round(((curr - prev) / prev) * 100, 1)

    return {
        "range": range,
        "since": _iso(since),
        "current": current,
        "previous": previous,
        "deltas": {
            "sessions": _delta(current["sessions"], previous["sessions"]),
            "visitors": _delta(current["visitors"], previous["visitors"]),
            "pages":    _delta(current["pages"],    previous["pages"]),
            "leads":    _delta(current["leads"],    previous["leads"]),
        },
    }


@router.get("/admin/analytics/timeline")
async def admin_timeline(request: Request, range: str = "7d", granularity: str = "hour"):
    await auth_admin(request)
    db = get_db()
    await _ensure_once(db)
    since = _range_to_dt(range)
    # Choose bucket size
    bucket = "%Y-%m-%dT%H:00:00Z" if granularity == "hour" else "%Y-%m-%dT00:00:00Z"
    pipeline = [
        {"$match": {"ts": {"$gte": since}, "type": "page"}},
        {"$group": {
            "_id": {"$dateToString": {"format": bucket, "date": "$ts"}},
            "pages": {"$sum": 1},
            "visitors": {"$addToSet": "$visitor_id"},
            "sessions": {"$addToSet": "$session_id"},
        }},
        {"$project": {"ts": "$_id", "pages": 1, "visitors": {"$size": "$visitors"}, "sessions": {"$size": "$sessions"}, "_id": 0}},
        {"$sort": {"ts": 1}},
    ]
    res = await db.visitor_events.aggregate(pipeline).to_list(None)
    return {"timeline": res, "granularity": granularity}


@router.get("/admin/analytics/top-pages")
async def admin_top_pages(request: Request, range: str = "7d", limit: int = 20):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    pipeline = [
        {"$match": {"ts": {"$gte": since}, "type": "page"}},
        {"$group": {
            "_id": "$path",
            "views": {"$sum": 1},
            "visitors": {"$addToSet": "$visitor_id"},
            "avg_duration": {"$avg": "$duration_ms"},
            "avg_scroll":   {"$avg": "$scroll_pct"},
        }},
        {"$project": {
            "path": "$_id", "_id": 0, "views": 1,
            "visitors": {"$size": "$visitors"},
            "avg_duration_sec": {"$round": [{"$divide": [{"$ifNull": ["$avg_duration", 0]}, 1000]}, 1]},
            "avg_scroll_pct": {"$round": [{"$ifNull": ["$avg_scroll", 0]}, 0]},
        }},
        {"$sort": {"views": -1}},
        {"$limit": limit},
    ]
    rows = await db.visitor_events.aggregate(pipeline).to_list(None)
    return {"top_pages": rows}


@router.get("/admin/analytics/sources")
async def admin_sources(request: Request, range: str = "7d", limit: int = 20):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    # Referrer hosts
    ref_pipeline = [
        {"$match": {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}}},
        {"$group": {"_id": {"$ifNull": ["$referrer_host", "(direct)"]},
                    "sessions": {"$sum": 1}, "leads": {"$sum": {"$cond": [{"$ifNull": ["$lead_id", False]}, 1, 0]}}}},
        {"$project": {"source": "$_id", "_id": 0, "sessions": 1, "leads": 1}},
        {"$sort": {"sessions": -1}},
        {"$limit": limit},
    ]
    referrers = await db.visitor_sessions.aggregate(ref_pipeline).to_list(None)
    # UTM campaigns
    utm_pipeline = [
        {"$match": {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}, "utm_source": {"$ne": None}}},
        {"$group": {"_id": {"src": "$utm_source", "med": "$utm_medium", "cmp": "$utm_campaign"},
                    "sessions": {"$sum": 1}, "leads": {"$sum": {"$cond": [{"$ifNull": ["$lead_id", False]}, 1, 0]}}}},
        {"$project": {"source": "$_id.src", "medium": "$_id.med", "campaign": "$_id.cmp",
                      "sessions": 1, "leads": 1, "_id": 0}},
        {"$sort": {"sessions": -1}},
        {"$limit": limit},
    ]
    utms = await db.visitor_sessions.aggregate(utm_pipeline).to_list(None)
    return {"referrers": referrers, "utm_campaigns": utms}


@router.get("/admin/analytics/geo")
async def admin_geo(request: Request, range: str = "7d"):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    countries = await db.visitor_sessions.aggregate([
        {"$match": {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}, "country": {"$ne": None}}},
        {"$group": {"_id": {"c": "$country", "cc": "$country_code"},
                    "sessions": {"$sum": 1}, "visitors": {"$addToSet": "$visitor_id"},
                    "leads": {"$sum": {"$cond": [{"$ifNull": ["$lead_id", False]}, 1, 0]}}}},
        {"$project": {"country": "$_id.c", "country_code": "$_id.cc",
                      "sessions": 1, "visitors": {"$size": "$visitors"}, "leads": 1, "_id": 0}},
        {"$sort": {"sessions": -1}},
        {"$limit": 50},
    ]).to_list(None)

    cities = await db.visitor_sessions.aggregate([
        {"$match": {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}, "city": {"$ne": None}}},
        {"$group": {"_id": {"c": "$city", "co": "$country", "cc": "$country_code",
                            "lat": "$lat", "lon": "$lon"},
                    "sessions": {"$sum": 1}, "leads": {"$sum": {"$cond": [{"$ifNull": ["$lead_id", False]}, 1, 0]}}}},
        {"$project": {"city": "$_id.c", "country": "$_id.co", "country_code": "$_id.cc",
                      "lat": "$_id.lat", "lon": "$_id.lon",
                      "sessions": 1, "leads": 1, "_id": 0}},
        {"$sort": {"sessions": -1}},
        {"$limit": 100},
    ]).to_list(None)
    return {"countries": countries, "cities": cities}


@router.get("/admin/analytics/devices")
async def admin_devices(request: Request, range: str = "7d"):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    async def _group(field: str) -> List[Dict[str, Any]]:
        rows = await db.visitor_sessions.aggregate([
            {"$match": {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}}},
            {"$group": {"_id": f"${field}", "sessions": {"$sum": 1}}},
            {"$project": {"name": {"$ifNull": ["$_id", "Unknown"]}, "sessions": 1, "_id": 0}},
            {"$sort": {"sessions": -1}},
        ]).to_list(None)
        return rows
    return {
        "devices":  await _group("device"),
        "browsers": await _group("browser"),
        "os":       await _group("os"),
    }


@router.get("/admin/analytics/funnel")
async def admin_funnel(request: Request, range: str = "7d"):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    total = await db.visitor_sessions.count_documents({"first_seen": {"$gte": since}, "is_bot": {"$ne": True}})
    saw_property = await db.visitor_events.distinct(
        "session_id", {"ts": {"$gte": since}, "type": "page", "path": {"$regex": "^/propiedades/"}}
    )
    used_chatbot = await db.visitor_events.distinct(
        "session_id", {"ts": {"$gte": since}, "type": "event", "event_name": "chatbot_open"}
    )
    leads = await db.visitor_sessions.count_documents({"first_seen": {"$gte": since},
                                                        "is_bot": {"$ne": True},
                                                        "lead_id": {"$ne": None}})
    return {
        "steps": [
            {"name": "Visit",        "value": total},
            {"name": "Saw property", "value": len(saw_property)},
            {"name": "Used chatbot", "value": len(used_chatbot)},
            {"name": "Became lead",  "value": leads},
        ]
    }


@router.get("/admin/analytics/sessions")
async def admin_sessions_list(
    request: Request, range: str = "7d", limit: int = 50, skip: int = 0,
    country: Optional[str] = None, device: Optional[str] = None,
    has_lead: Optional[bool] = None,
):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    q: Dict[str, Any] = {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}}
    if country:
        q["country_code"] = country.upper()
    if device:
        q["device"] = device
    if has_lead is True:
        q["lead_id"] = {"$ne": None}
    elif has_lead is False:
        q["lead_id"] = None
    cursor = db.visitor_sessions.find(q).sort("first_seen", -1).skip(skip).limit(min(limit, 200))
    rows = []
    async for s in cursor:
        rows.append({
            "session_id": s["_id"],
            "first_seen": _iso(s["first_seen"]) if isinstance(s.get("first_seen"), datetime) else s.get("first_seen"),
            "last_seen":  _iso(s["last_seen"])  if isinstance(s.get("last_seen"),  datetime) else s.get("last_seen"),
            "country": s.get("country"), "country_code": s.get("country_code"),
            "city": s.get("city"), "device": s.get("device"), "browser": s.get("browser"), "os": s.get("os"),
            "pages_count": s.get("pages_count", 0), "events_count": s.get("events_count", 0),
            "landing_path": s.get("landing_path"), "current_path": s.get("current_path"),
            "referrer_host": s.get("referrer_host"),
            "lead_id": s.get("lead_id"),
        })
    total = await db.visitor_sessions.count_documents(q)
    return {"sessions": rows, "total": total, "limit": limit, "skip": skip}


@router.get("/admin/analytics/sessions/{sid}")
async def admin_session_detail(request: Request, sid: str):
    await auth_admin(request)
    db = get_db()
    s = await db.visitor_sessions.find_one({"_id": sid})
    if not s:
        raise HTTPException(404, "Session not found")
    events = await db.visitor_events.find({"session_id": sid}).sort("ts", 1).limit(500).to_list(None)
    for e in events:
        if isinstance(e.get("ts"), datetime):
            e["ts"] = _iso(e["ts"])
    if isinstance(s.get("first_seen"), datetime):
        s["first_seen"] = _iso(s["first_seen"])
    if isinstance(s.get("last_seen"), datetime):
        s["last_seen"] = _iso(s["last_seen"])
    return {"session": s, "events": events}


# ── Snapshot for AI Brain ────────────────────────────────────────────────────

async def get_traffic_snapshot(db) -> Dict[str, Any]:
    """Compact 'now' traffic snapshot for the AI Brain context."""
    await _ensure_once(db)
    now = _now()
    cutoff_live = now - timedelta(seconds=LIVE_WINDOW_SECONDS)
    cutoff_today = now - timedelta(hours=24)
    cutoff_week = now - timedelta(days=7)

    online_now = await db.visitor_sessions.count_documents({"last_seen": {"$gte": cutoff_live}, "is_bot": {"$ne": True}})
    sessions_24h = await db.visitor_sessions.count_documents({"first_seen": {"$gte": cutoff_today}, "is_bot": {"$ne": True}})
    sessions_7d = await db.visitor_sessions.count_documents({"first_seen": {"$gte": cutoff_week}, "is_bot": {"$ne": True}})
    leads_24h = await db.visitor_sessions.count_documents({"first_seen": {"$gte": cutoff_today},
                                                            "lead_id": {"$ne": None}, "is_bot": {"$ne": True}})

    top_countries = await db.visitor_sessions.aggregate([
        {"$match": {"first_seen": {"$gte": cutoff_week}, "is_bot": {"$ne": True}, "country": {"$ne": None}}},
        {"$group": {"_id": "$country", "sessions": {"$sum": 1}}},
        {"$sort": {"sessions": -1}}, {"$limit": 5},
    ]).to_list(None)
    top_pages = await db.visitor_events.aggregate([
        {"$match": {"ts": {"$gte": cutoff_week}, "type": "page"}},
        {"$group": {"_id": "$path", "views": {"$sum": 1}}},
        {"$sort": {"views": -1}}, {"$limit": 5},
    ]).to_list(None)

    return {
        "online_now": online_now,
        "sessions_last_24h": sessions_24h,
        "sessions_last_7d": sessions_7d,
        "leads_from_traffic_24h": leads_24h,
        "top_countries_7d": [{"country": c["_id"], "sessions": c["sessions"]} for c in top_countries],
        "top_pages_7d": [{"path": p["_id"], "views": p["views"]} for p in top_pages],
    }
