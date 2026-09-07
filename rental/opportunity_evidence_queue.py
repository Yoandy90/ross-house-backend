"""Read model for the admin opportunity-evidence review queue."""
from __future__ import annotations

from typing import Any

from bson import ObjectId

from rental.opportunity_evidence import review_evidence_atomic


REVIEW_STATUSES = ("needs_review", "confirmed", "dismissed")

# Queue cards do not need internal reviewer identities, notes or audit history.
# Explicit inclusion also prevents future provider payloads leaking into this view.
QUEUE_EVIDENCE_FIELDS = (
    "evidence_id", "source", "signals", "record_name", "record_address",
    "case_number", "date", "city", "source_url", "confidence", "match_reasons",
    "review_status", "obit_name", "obit_date", "radar_id", "at",
)


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
        {"$unwind": {"path": "$motivation.details", "includeArrayIndex": "evidence_index"}},
        {"$match": {"motivation.details.evidence_id": {"$type": "string"}}},
        {"$set": {
            "evidence": "$motivation.details",
            "evidence_status": {"$ifNull": [
                "$motivation.details.review_status", "needs_review",
            ]},
            "priority_score": {"$round": [{"$add": [
                {"$multiply": [{"$convert": {
                    "input": "$motivation.details.confidence", "to": "double",
                    "onError": 0, "onNull": 0,
                }}, 0.7]},
                {"$multiply": [{"$convert": {
                    "input": "$ai_score", "to": "double",
                    "onError": 0, "onNull": 0,
                }}, 0.3]},
            ]}, 0]},
        }},
        {"$facet": {
            "items": [
                {"$match": item_match},
                # _id alone does not distinguish evidence rows from the same lead.
                # Array index breaks even legacy duplicate-ID ties on unchanged data.
                {"$sort": {"priority_score": -1, "evidence.at": -1, "_id": 1,
                           "evidence.evidence_id": 1, "evidence_index": 1}},
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
                    "priority_score": 1,
                    **{f"evidence.{field}": 1 for field in QUEUE_EVIDENCE_FIELDS},
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


async def bulk_review_evidence(db, items: list[dict[str, str]], status: str,
                               reviewer_id: str, *, note: str = "") -> dict[str, Any]:
    """Review independent evidence facts safely and report partial failures."""
    if status not in REVIEW_STATUSES:
        raise ValueError("opportunity_evidence_status_invalid")
    if not 1 <= len(items) <= 50:
        raise ValueError("opportunity_evidence_bulk_size_invalid")

    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for item in items:
        lead_id = str(item.get("lead_id") or "")
        evidence_id = str(item.get("evidence_id") or "")
        key = (lead_id, evidence_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not ObjectId.is_valid(lead_id):
                raise ValueError("opportunity_evidence_lead_id_invalid")
            oid = ObjectId(lead_id)
            reviewed = await review_evidence_atomic(
                db, oid, evidence_id, status, reviewer_id, note=note)
            outcome = "reviewed" if reviewed else "not_found"
        except (ValueError, TypeError):
            outcome = "invalid"
        results.append({"lead_id": lead_id, "evidence_id": evidence_id,
                        "outcome": outcome})

    return {
        "requested": len(items),
        "unique": len(results),
        "reviewed": sum(row["outcome"] == "reviewed" for row in results),
        "not_found": sum(row["outcome"] == "not_found" for row in results),
        "invalid": sum(row["outcome"] == "invalid" for row in results),
        "results": results,
    }
