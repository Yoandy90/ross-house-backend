"""
Analytics AI Insights + Top Movers routes.
Extends the visitor_analytics_router with intelligence powered by Claude Sonnet 4.5.

Adds:
  GET /api/admin/analytics/ai-insights   — LLM-generated executive summary + recos
  GET /api/admin/analytics/top-movers    — biggest week-over-week deltas
"""
from __future__ import annotations

import os
import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException

from .shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-4-5-20250929"

CACHE_COLL = "analytics_ai_cache"
CACHE_TTL_SECONDS = 30 * 60  # 30 min


# ── helpers ────────────────────────────────────────────────────────────────
def _range_to_dt(range_str: str) -> datetime:
    now = datetime.now(timezone.utc)
    mapping = {
        "24h": timedelta(hours=24),
        "7d":  timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
    }
    return now - mapping.get(range_str, timedelta(days=7))


async def _collect_context(db, range_str: str) -> Dict[str, Any]:
    """Aggregate a compact snapshot of analytics for the LLM."""
    since = _range_to_dt(range_str)
    prev_since = since - (datetime.now(timezone.utc) - since)

    # Current + previous sessions overview
    async def _overview(start: datetime, end: Optional[datetime] = None):
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
                "leads":    {"$sum": {"$cond": [{"$ifNull": ["$lead_id", False]}, 1, 0]}},
                "bounces":  {"$sum": {"$cond": [{"$lte": ["$pages_count", 1]}, 1, 0]}},
                "duration_sum": {"$sum": {"$dateDiff": {"startDate": "$first_seen", "endDate": "$last_seen", "unit": "second"}}},
            }},
        ]
        try:
            r = await db.visitor_sessions.aggregate(pipeline).to_list(1)
        except Exception:
            r = []
        if not r:
            return {"sessions": 0, "visitors": 0, "pages": 0, "leads": 0, "bounce_rate": 0, "avg_seconds": 0}
        d = r[0]
        sess = d.get("sessions", 0)
        return {
            "sessions": sess,
            "visitors": len(d.get("visitors", [])),
            "pages": d.get("pages", 0),
            "leads": d.get("leads", 0),
            "bounce_rate": round((d.get("bounces", 0) / sess) * 100, 1) if sess else 0,
            "avg_seconds": int(d.get("duration_sum", 0) / sess) if sess else 0,
        }

    curr = await _overview(since)
    prev = await _overview(prev_since, since)

    async def _top(pipeline, coll="visitor_sessions"):
        try:
            return await db[coll].aggregate(pipeline).to_list(10)
        except Exception:
            return []

    top_countries = await _top([
        {"$match": {"first_seen": {"$gte": since}, "country": {"$ne": None}, "is_bot": {"$ne": True}}},
        {"$group": {"_id": {"country": "$country", "cc": "$country_code"}, "sessions": {"$sum": 1}}},
        {"$sort": {"sessions": -1}}, {"$limit": 8},
    ])
    top_pages = await _top([
        {"$match": {"first_seen": {"$gte": since}, "landing_path": {"$ne": None}, "is_bot": {"$ne": True}}},
        {"$group": {"_id": "$landing_path", "views": {"$sum": 1}}},
        {"$sort": {"views": -1}}, {"$limit": 8},
    ])
    top_referrers = await _top([
        {"$match": {"first_seen": {"$gte": since}, "referrer_host": {"$ne": None}, "is_bot": {"$ne": True}}},
        {"$group": {"_id": "$referrer_host", "sessions": {"$sum": 1}}},
        {"$sort": {"sessions": -1}}, {"$limit": 6},
    ])
    top_devices = await _top([
        {"$match": {"first_seen": {"$gte": since}, "is_bot": {"$ne": True}}},
        {"$group": {"_id": "$device", "sessions": {"$sum": 1}}},
        {"$sort": {"sessions": -1}},
    ])

    def _delta(a: float, b: float) -> Optional[float]:
        if b == 0:
            return None
        return round(((a - b) / b) * 100, 1)

    return {
        "range": range_str,
        "current": curr,
        "previous": prev,
        "deltas": {
            "sessions": _delta(curr["sessions"], prev["sessions"]),
            "visitors": _delta(curr["visitors"], prev["visitors"]),
            "leads":    _delta(curr["leads"], prev["leads"]),
            "pages":    _delta(curr["pages"], prev["pages"]),
        },
        "top_countries": [{"country": c["_id"]["country"], "cc": c["_id"].get("cc"), "sessions": c["sessions"]} for c in top_countries],
        "top_pages": [{"path": p["_id"], "views": p["views"]} for p in top_pages],
        "top_referrers": [{"host": r["_id"], "sessions": r["sessions"]} for r in top_referrers],
        "devices": [{"device": d["_id"] or "Unknown", "sessions": d["sessions"]} for d in top_devices],
    }


# ═══════════════════════════════════════════════════════════════════════════
# AI INSIGHTS — Claude Sonnet 4.5 executive summary
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/admin/analytics/ai-insights")
async def analytics_ai_insights(request: Request, range: str = "7d", refresh: int = 0):
    await auth_admin(request)
    db = get_db()

    ctx = await _collect_context(db, range)

    # ── Cache lookup ─────────────────────────────────────────────────────
    ctx_hash = hashlib.sha256(json.dumps(ctx, sort_keys=True, default=str).encode()).hexdigest()[:24]
    cache_key = f"{range}_{ctx_hash}"

    if not refresh:
        cached = await db[CACHE_COLL].find_one({"_id": cache_key})
        if cached and (datetime.now(timezone.utc) - cached["created_at"].replace(tzinfo=timezone.utc)).total_seconds() < CACHE_TTL_SECONDS:
            return {**cached.get("payload", {}), "cached": True}

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        # Fallback: rule-based summary if the LLM isn't available
        return _rule_based_insights(ctx) | {"cached": False, "source": "fallback"}

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        system_prompt = (
            "Eres el analista senior de marketing digital de Ross House Rentals, "
            "una empresa de renta de casas en Amarillo, TX. Analizas datos de tráfico web "
            "y das insights ejecutivos ACCIONABLES en español, tono profesional pero cálido. "
            "Nunca inventes datos: solo usa los que te dan. Sé conciso y punzante. "
            "Devuelve SOLO JSON válido con esta estructura exacta:\n"
            "{\n"
            '  "headline": "una frase de 8-14 palabras con el titular más importante",\n'
            '  "verdict": "positive" | "neutral" | "warning",\n'
            '  "summary": "2-3 oraciones que resuman el periodo, incluyendo % vs periodo anterior",\n'
            '  "highlights": [{"emoji":"🚀","text":"…"}, …3-4 items],\n'
            '  "recommendations": [{"priority":"high|med|low","action":"…","reason":"…"}, …2-4 items],\n'
            '  "forecast": "1 oración sobre qué esperar la próxima semana basado en la tendencia"\n'
            "}\n"
            "Prohibido usar comillas triples o texto fuera del JSON."
        )

        user_prompt = (
            "Analiza estos datos y devuelve el JSON:\n\n"
            f"```json\n{json.dumps(ctx, ensure_ascii=False, indent=2, default=str)}\n```"
        )

        chat = LlmChat(
            api_key=api_key,
            session_id=f"analytics_insights_{uuid4()}",
            system_message=system_prompt,
        ).with_model(MODEL_PROVIDER, MODEL_NAME)

        raw = await chat.send_message(UserMessage(text=user_prompt))
        text = str(raw or "").strip()
        # Strip ```json fences if present
        if text.startswith("```"):
            text = text.split("```", 2)
            text = text[1] if len(text) > 1 else text[0]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip("` \n")

        try:
            parsed = json.loads(text)
        except Exception:
            # Try to find JSON object substring
            first = text.find("{")
            last = text.rfind("}")
            parsed = json.loads(text[first:last + 1]) if first >= 0 and last > first else _rule_based_insights(ctx)

        # Normalize
        parsed.setdefault("headline", "Resumen del periodo")
        parsed.setdefault("verdict", "neutral")
        parsed.setdefault("summary", "")
        parsed.setdefault("highlights", [])
        parsed.setdefault("recommendations", [])
        parsed.setdefault("forecast", "")

        payload = {
            "range": range,
            "context": ctx,
            "insights": parsed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": MODEL_NAME,
        }
        # Store cache (upsert)
        try:
            await db[CACHE_COLL].update_one(
                {"_id": cache_key},
                {"$set": {"_id": cache_key, "payload": payload, "created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"[ai-insights] cache write failed: {e}")

        return {**payload, "cached": False, "source": "claude"}
    except Exception as e:
        logger.exception(f"[ai-insights] LLM call failed: {e}")
        return _rule_based_insights(ctx) | {"cached": False, "source": "fallback", "error": str(e)[:120]}


def _rule_based_insights(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback deterministic insights if the LLM is unavailable."""
    curr = ctx.get("current", {})
    deltas = ctx.get("deltas", {}) or {}
    ds = deltas.get("sessions")
    dl = deltas.get("leads")

    verdict = "neutral"
    if (ds or 0) >= 15 or (dl or 0) >= 15:
        verdict = "positive"
    elif (ds or 0) <= -15:
        verdict = "warning"

    top_c = (ctx.get("top_countries") or [{}])[0].get("country", "—")
    top_p = (ctx.get("top_pages") or [{}])[0].get("path", "—")

    highlights = []
    if curr.get("sessions", 0) > 0:
        highlights.append({"emoji": "👥", "text": f"{curr['sessions']} sesiones, {curr.get('visitors', 0)} visitantes únicos"})
    if top_c != "—":
        highlights.append({"emoji": "🌍", "text": f"Origen principal: {top_c}"})
    if top_p != "—":
        highlights.append({"emoji": "🔥", "text": f"Página más visitada: {top_p}"})
    if curr.get("bounce_rate") is not None:
        highlights.append({"emoji": "📈", "text": f"Bounce rate: {curr['bounce_rate']}%"})

    return {
        "range": ctx.get("range"),
        "context": ctx,
        "insights": {
            "headline": f"{curr.get('sessions', 0)} sesiones en el periodo",
            "verdict": verdict,
            "summary": (
                f"Registramos {curr.get('sessions', 0)} sesiones y {curr.get('visitors', 0)} visitantes únicos. "
                f"Cambio vs periodo anterior: sesiones {ds}%, leads {dl}%."
            ),
            "highlights": highlights[:4],
            "recommendations": [],
            "forecast": "",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "rule-based",
    }


# ═══════════════════════════════════════════════════════════════════════════
# TOP MOVERS — biggest deltas period vs previous
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/admin/analytics/top-movers")
async def analytics_top_movers(request: Request, range: str = "7d"):
    await auth_admin(request)
    db = get_db()
    since = _range_to_dt(range)
    prev_since = since - (datetime.now(timezone.utc) - since)

    async def _grouped(dimension: str, start: datetime, end: Optional[datetime] = None):
        match: Dict[str, Any] = {"first_seen": {"$gte": start}, dimension: {"$ne": None}, "is_bot": {"$ne": True}}
        if end:
            match["first_seen"]["$lt"] = end
        try:
            return {
                (r["_id"] or ""): r["v"]
                for r in await db.visitor_sessions.aggregate([
                    {"$match": match},
                    {"$group": {"_id": f"${dimension}", "v": {"$sum": 1}}},
                ]).to_list(200)
            }
        except Exception:
            return {}

    async def _movers(dimension: str, limit: int = 4):
        curr = await _grouped(dimension, since)
        prev = await _grouped(dimension, prev_since, since)
        all_keys = set(curr) | set(prev)
        rows = []
        for k in all_keys:
            c = curr.get(k, 0)
            p = prev.get(k, 0)
            if c + p == 0:
                continue
            if p == 0:
                delta_pct = None    # brand new
                delta_abs = c
                kind = "new"
            else:
                delta_pct = round(((c - p) / p) * 100, 1)
                delta_abs = c - p
                kind = "up" if c > p else "down" if c < p else "flat"
            rows.append({"key": k, "current": c, "previous": p, "delta_pct": delta_pct, "delta_abs": delta_abs, "kind": kind})
        # Sort by magnitude of change with new stuff on top
        rows.sort(key=lambda r: (0 if r["kind"] == "new" else 1, -abs(r["delta_abs"])))
        return rows[:limit]

    return {
        "range": range,
        "pages":       await _movers("landing_path", 4),
        "countries":   await _movers("country", 4),
        "referrers":   await _movers("referrer_host", 4),
        "devices":     await _movers("device", 3),
    }


async def ensure_indexes(db) -> None:
    try:
        await db[CACHE_COLL].create_index("created_at", expireAfterSeconds=CACHE_TTL_SECONDS * 2)
    except Exception as e:
        logger.warning(f"[ai-insights] index: {e}")
