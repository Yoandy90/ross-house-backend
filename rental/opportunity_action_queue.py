"""Deterministic next-best-action queue for acquisition opportunities."""
from __future__ import annotations

from typing import Any


ACTIONS = (
    "review_evidence", "prepare_contract", "negotiate", "close_out",
    "analyze", "find_contact", "prepare_offer", "follow_up", "monitor",
)


def build_action_queue_pipeline(*, action: str = "", skip: int = 0,
                                limit: int = 20) -> list[dict[str, Any]]:
    if action and action not in ACTIONS:
        raise ValueError("opportunity_action_invalid")
    if not 0 <= skip <= 10_000:
        raise ValueError("opportunity_action_skip_invalid")
    if not 1 <= limit <= 100:
        raise ValueError("opportunity_action_limit_invalid")

    details = {"$cond": [
        {"$isArray": "$motivation.details"}, "$motivation.details", [],
    ]}
    phones = {"$cond": [{"$isArray": "$contact.phones"}, "$contact.phones", []]}
    emails = {"$cond": [{"$isArray": "$contact.emails"}, "$contact.emails", []]}
    item_match = {"next_action": action} if action else {}

    return [
        {"$match": {"status": {"$nin": ["acquired", "discarded"]}}},
        {"$set": {
            "pending_evidence": {"$size": {"$filter": {
                "input": details, "as": "detail", "cond": {"$and": [
                    {"$eq": [{"$type": "$$detail.evidence_id"}, "string"]},
                    {"$eq": [{"$ifNull": [
                        "$$detail.review_status", "needs_review",
                    ]}, "needs_review"]},
                ]},
            }}},
            "has_contact": {"$or": [
                {"$gt": [{"$size": phones}, 0]},
                {"$gt": [{"$size": emails}, 0]},
            ]},
            "response_action": {"$ifNull": ["$offer.response.action", ""]},
            "normalized_ai_score": {"$convert": {
                "input": "$ai_score", "to": "double", "onError": 0, "onNull": 0,
            }},
        }},
        {"$set": {
            "next_action": {"$switch": {"branches": [
                {"case": {"$gt": ["$pending_evidence", 0]}, "then": "review_evidence"},
                {"case": {"$eq": ["$response_action", "accept"]}, "then": "prepare_contract"},
                {"case": {"$in": ["$response_action", ["counter", "call"]]}, "then": "negotiate"},
                {"case": {"$eq": ["$response_action", "reject"]}, "then": "close_out"},
                {"case": {"$eq": [{"$ifNull": ["$ai_score", None]}, None]}, "then": "analyze"},
                {"case": {"$not": ["$has_contact"]}, "then": "find_contact"},
                {"case": {"$eq": [{"$ifNull": ["$offer", None]}, None]}, "then": "prepare_offer"},
                {"case": {"$eq": ["$response_action", ""]}, "then": "follow_up"},
            ], "default": "monitor"}},
            "action_priority": {"$round": [{"$add": [
                "$normalized_ai_score",
                {"$multiply": ["$pending_evidence", 10]},
                {"$cond": [{"$in": ["$response_action", ["accept", "counter", "call"]]}, 25, 0]},
            ]}, 0]},
        }},
        {"$facet": {
            "items": [
                {"$match": item_match},
                {"$sort": {"action_priority": -1, "last_synced_at": -1, "_id": 1}},
                {"$skip": skip}, {"$limit": limit},
                {"$project": {
                    "_id": 0, "lead_id": {"$toString": "$_id"},
                    "property_id": {"$ifNull": ["$property_id", ""]},
                    "owner_name": {"$ifNull": ["$owner_name", ""]},
                    "address": {"$ifNull": ["$address", ""]},
                    "county": {"$ifNull": ["$county", ""]},
                    "lead_status": {"$ifNull": ["$status", "new"]},
                    "ai_score": 1, "tax_due_total": 1, "appraised_value": 1,
                    "pending_evidence": 1, "has_contact": 1,
                    "response_action": 1, "next_action": 1, "action_priority": 1,
                }},
            ],
            "filtered_total": [{"$match": item_match}, {"$count": "count"}],
            "action_counts": [
                {"$group": {"_id": "$next_action", "count": {"$sum": 1}}},
            ],
        }},
    ]


def serialize_action_queue(result: list[dict[str, Any]]) -> dict[str, Any]:
    facet = result[0] if result else {}
    counts = {action: 0 for action in ACTIONS}
    for row in facet.get("action_counts") or []:
        if row.get("_id") in counts:
            counts[row["_id"]] = int(row.get("count") or 0)
    total_rows = facet.get("filtered_total") or []
    return {
        "items": facet.get("items") or [],
        "total": int(total_rows[0].get("count") or 0) if total_rows else 0,
        "counts": counts,
    }
