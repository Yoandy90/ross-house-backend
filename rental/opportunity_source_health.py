"""Operational freshness and coverage read model for opportunity sources."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


SOURCE_DEFINITIONS = (
    ("propertyradar", "PropertyRadar"),
    ("obituary", "Obituarios locales"),
    ("probate", "Probate del condado"),
    ("eviction", "Evicciones"),
    ("divorce", "Divorcios"),
    ("tax_sale", "Tax sales"),
    ("code_violation", "Code violations"),
    ("vacancy", "Vacantes"),
)
SOURCE_IDS = {source_id for source_id, _ in SOURCE_DEFINITIONS}


def build_source_health_pipeline() -> list[dict[str, Any]]:
    """Aggregate evidence coverage without loading lead or evidence payloads."""
    return [
        {"$project": {"details": {"$cond": [
            {"$isArray": "$motivation.details"}, "$motivation.details", [],
        ]}}},
        {"$unwind": "$details"},
        {"$match": {
            "details.evidence_id": {"$type": "string"},
            "details.source": {"$in": sorted(SOURCE_IDS)},
        }},
        {"$set": {"review_status": {"$cond": [
            {"$in": ["$details.review_status", [
                "needs_review", "confirmed", "dismissed"]]},
            "$details.review_status", "needs_review",
        ]}}},
        {"$group": {
            "_id": "$details.source",
            "total": {"$sum": 1},
            "needs_review": {"$sum": {"$cond": [
                {"$eq": ["$review_status", "needs_review"]}, 1, 0]}},
            "confirmed": {"$sum": {"$cond": [
                {"$eq": ["$review_status", "confirmed"]}, 1, 0]}},
            "dismissed": {"$sum": {"$cond": [
                {"$eq": ["$review_status", "dismissed"]}, 1, 0]}},
            "last_evidence_at": {"$max": "$details.at"},
        }},
    ]


def _utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _count(value: Any) -> int:
    """Malformed legacy counters must not prevent other sources from loading."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def serialize_source_health(rows: list[dict[str, Any]], runs_document: dict | None,
                            *, stale_days: int = 30,
                            now: datetime | None = None) -> dict[str, Any]:
    if stale_days < 1 or stale_days > 365:
        raise ValueError("opportunity_source_stale_days_invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("opportunity_source_clock_must_be_aware")
    current = current.astimezone(timezone.utc)
    coverage = {str(row.get("_id")): row for row in rows
                if str(row.get("_id")) in SOURCE_IDS}
    recorded_runs = (runs_document or {}).get("sources") or {}
    sources = []
    for source_id, label in SOURCE_DEFINITIONS:
        row = coverage.get(source_id) or {}
        run = recorded_runs.get(source_id) if isinstance(recorded_runs, dict) else {}
        run = run if isinstance(run, dict) else {}
        last_run_at = str(run.get("last_run_at") or "")[:50]
        last_evidence_at = str(row.get("last_evidence_at") or "")[:50]
        freshness_at = last_run_at or last_evidence_at
        observed = _utc_datetime(freshness_at)
        valid_observation = observed is not None and observed <= current
        age_days = int((current - observed).total_seconds() // 86400) if valid_observation else None
        if not freshness_at:
            status = "never_run"
        elif not valid_observation:
            status = "invalid_timestamp"
        elif current - observed > timedelta(days=stale_days):
            status = "stale"
        elif last_run_at and (run.get("status") != "success" or _count(run.get("errors")) > 0):
            status = "partial"
        elif not last_run_at:
            status = "observed_only"
        else:
            status = "healthy"
        total = _count(row.get("total"))
        pending = _count(row.get("needs_review"))
        confirmed = _count(row.get("confirmed"))
        dismissed = _count(row.get("dismissed"))
        sources.append({
            "source": source_id, "label": label, "status": status,
            "requires_attention": status in {
                "never_run", "invalid_timestamp", "stale", "partial"},
            "last_run_at": last_run_at, "last_evidence_at": last_evidence_at,
            "age_days": age_days, "stale_after_days": stale_days,
            "last_run": {
                "scanned": _count(run.get("scanned")),
                "matched": _count(run.get("matched")),
                "errors": _count(run.get("errors")),
            },
            "evidence": {"total": total, "needs_review": pending,
                         "confirmed": confirmed, "dismissed": dismissed},
        })
    evidence_total = sum(item["evidence"]["total"] for item in sources)
    reviewed_total = sum(item["evidence"]["confirmed"] +
                         item["evidence"]["dismissed"] for item in sources)
    return {
        "generated_at": current.isoformat(),
        "stale_after_days": stale_days,
        "summary": {
            "sources": len(sources),
            "requiring_attention": sum(item["requires_attention"] for item in sources),
            "evidence_total": evidence_total,
            "pending_review": sum(item["evidence"]["needs_review"] for item in sources),
            "reviewed_pct": round(reviewed_total * 100 / evidence_total) if evidence_total else 0,
        },
        "sources": sources,
    }


async def record_source_run(db, source: str, *, scanned: int, matched: int,
                            errors: int = 0, now: datetime | None = None) -> None:
    """Persist a bounded successful/partial run receipt; never stores provider payloads."""
    if source not in SOURCE_IDS:
        raise ValueError("opportunity_source_invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("opportunity_source_clock_must_be_aware")
    values = [int(scanned), int(matched), int(errors)]
    if any(value < 0 for value in values):
        raise ValueError("opportunity_source_run_counts_invalid")
    receipt = {
        "last_run_at": current.astimezone(timezone.utc).isoformat(),
        "status": "partial" if errors else "success",
        "scanned": values[0], "matched": values[1], "errors": values[2],
    }
    await db.app_settings.update_one(
        {"_id": "opportunity_source_health"},
        {"$set": {f"sources.{source}": receipt}}, upsert=True)
