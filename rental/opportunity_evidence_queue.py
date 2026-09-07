"""Read model for the admin opportunity-evidence review queue."""
from __future__ import annotations

from typing import Any


REVIEW_STATUSES = ("needs_review", "confirmed", "dismissed")


def build_evidence_queue_pipeline(*, status: str = "needs_review", source: str = "",
                                  skip: int = 0, limit: int = 25) -> list[dict[str, Any]]:
    """Build one bounded aggregation that returns queue rows and global counters."""
    if status not in REVIEW_STATUSES:
        raise ValueError("opportunity_evidence_status_invalid")
    if not 0 <= skip <= 10_000:
        raise ValueError("opportunity_evidence_skip_invalid")
    if not 1 <= limit <= 100:
        raise ValueError("opportunity_evidence_limit_invalid")

    normalized_source = str(source or "").strip().lower()[:50]
    item_match: dict[str, Any] = {"evidence_status": status}
    if normalized_source:
        item_match["evidence.source"] = normalized_source

    return [
        {"$match": {"motivation.details": {"$elemMatch": {
            "evidence_id": {"$type": "string"},
        }}}},
        {"$unwind": "$motivation.details"},
        {"$match": {"motivation.details.evidence_id": {"$type": "string"}}},
        {"$set": {
            "evidence": "$motivation.details",
            "evidence_status": {"$ifNull": [
                "$motivation.details.review_status", "needs_review",
            ]},
        }},
        {"$facet": {
            "items": [
                {"$match": item_match},
                {"$sort": {"evidence.at": -1, "_id": 1}},
                {"$skip": skip},
                {"$limit": limit},
                {"$project": {
                    "_id": 0,
                    "lead_id": {"$toString": "$_id"},
                    "property_id": {"$ifNull": ["$property_id", ""]},
                    "owner_name": {"$ifNull": ["$owner_name", ""]},
                    "address": {"$ifNull": ["$address", ""]},
                    "county": {"$ifNull": ["$county", ""]},
                    "ai_score": 1,
                    "lead_status": {"$ifNull": ["$status", "new"]},
                    "evidence": 1,
                }},
            ],
            "filtered_total": [
                {"$match": item_match}, {"$count": "count"},
            ],
            "status_counts": [
                {"$group": {"_id": "$evidence_status", "count": {"$sum": 1}}},
            ],
            "source_counts": [
                {"$group": {"_id": "$evidence.source", "count": {"$sum": 1}}},
                {"$sort": {"count": -1, "_id": 1}},
            ],
        }},
    ]


def serialize_evidence_queue(result: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize an aggregate result so the client always receives stable keys."""
    facet = result[0] if result else {}
    counts = {status: 0 for status in REVIEW_STATUSES}
    for row in facet.get("status_counts") or []:
        if row.get("_id") in counts:
            counts[row["_id"]] = int(row.get("count") or 0)
    sources = {
        str(row.get("_id")): int(row.get("count") or 0)
        for row in (facet.get("source_counts") or []) if row.get("_id")
    }
    total_rows = facet.get("filtered_total") or []
    filtered_total = int(total_rows[0].get("count") or 0) if total_rows else 0
    return {
        "items": facet.get("items") or [],
        "total": filtered_total,
        "counts": counts,
        "sources": sources,
    }
