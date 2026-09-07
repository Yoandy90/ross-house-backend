from pathlib import Path

import pytest

from rental.opportunity_action_queue import (
    ACTIONS, build_action_queue_pipeline, serialize_action_queue,
)


def test_action_queue_encodes_safe_business_precedence():
    pipeline = build_action_queue_pipeline(action="negotiate", skip=20, limit=10)
    branches = pipeline[2]["$set"]["next_action"]["$switch"]["branches"]
    outcomes = [branch["then"] for branch in branches]
    assert outcomes == [
        "close_out", "review_evidence", "monitor", "prepare_contract", "negotiate",
        "follow_up", "analyze", "find_contact", "prepare_offer",
    ]
    facets = pipeline[3]["$facet"]
    assert facets["items"][0] == {"$match": {"next_action": "negotiate"}}
    assert facets["items"][1]["$sort"]["action_priority"] == -1
    assert {"$skip": 20} in facets["items"]
    assert {"$limit": 10} in facets["items"]


def test_action_queue_handles_missing_arrays_and_legacy_pending_evidence():
    derived = build_action_queue_pipeline()[1]["$set"]
    evidence = derived["pending_evidence"]["$size"]["$filter"]
    assert evidence["input"]["$cond"][2] == []
    assert evidence["cond"]["$and"][1]["$eq"][0]["$ifNull"][1] == "needs_review"
    assert derived["has_contact"]["$or"][0]["$gt"][0]["$size"]["$cond"][2] == []


@pytest.mark.parametrize("kwargs", [
    {"action": "delete"}, {"skip": -1}, {"skip": 10_001},
    {"limit": 0}, {"limit": 101},
])
def test_action_queue_rejects_invalid_or_unbounded_inputs(kwargs):
    with pytest.raises(ValueError):
        build_action_queue_pipeline(**kwargs)


def test_action_queue_serializer_has_stable_counts():
    payload = serialize_action_queue([{
        "items": [{"lead_id": "1", "next_action": "analyze"}],
        "filtered_total": [{"count": 4}],
        "action_counts": [{"_id": "analyze", "count": 4}],
    }])
    assert payload["total"] == 4
    assert payload["counts"]["analyze"] == 4
    assert payload["counts"]["negotiate"] == 0
    assert set(payload["counts"]) == set(ACTIONS)


def test_admin_router_exposes_authenticated_action_queue():
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    assert '@router.get("/admin/deal-finder/action-queue")' in source
    assert "build_action_queue_pipeline(" in source
    assert "serialize_action_queue(rows)" in source


def evaluate(expr, document, variables=None):
    """Small expression interpreter for synthetic cases, not a Mongo integration test."""
    variables = variables or {}
    if isinstance(expr, str) and expr.startswith("$"):
        root = variables if expr.startswith("$$") else document
        for part in expr.lstrip("$").split("."):
            root = root.get(part) if isinstance(root, dict) else None
        return root
    if isinstance(expr, list):
        return [evaluate(value, document, variables) for value in expr]
    if not isinstance(expr, dict):
        return expr
    op, value = next(iter(expr.items()))
    if op == "$switch":
        for branch in value["branches"]:
            if evaluate(branch["case"], document, variables):
                return branch["then"]
        return value["default"]
    if op == "$filter":
        return [item for item in evaluate(value["input"], document, variables)
                if evaluate(value["cond"], document, {**variables, value["as"]: item})]
    args = evaluate(value, document, variables)
    if op == "$ifNull": return args[0] if args[0] is not None else args[1]
    if op == "$eq": return args[0] == args[1]
    if op == "$ne": return args[0] != args[1]
    if op == "$gt": return args[0] > args[1]
    if op == "$in": return args[0] in args[1]
    if op == "$not": return not args[0]
    if op == "$and": return all(args)
    if op == "$or": return any(args)
    if op == "$isArray": return isinstance(args, list)
    if op == "$size": return len(args)
    if op == "$cond": return args[1] if args[0] else args[2]
    raise AssertionError(f"Unsupported expression: {op}")


@pytest.mark.parametrize("changes,expected", [
    ({}, "monitor"),  # draft, not sent
    ({"offer": None}, "prepare_offer"),
    ({"has_contact": False}, "find_contact"),
    ({"ai_score": None}, "analyze"),
    ({"response_action": "accept"}, "prepare_contract"),
    ({"response_action": "accept", "contract": {"generated_at": "2026-09-07"}}, "monitor"),
    ({"response_action": "accept", "contract": {}}, "prepare_contract"),
    ({"response_action": "counter"}, "negotiate"),
    ({"response_action": "call"}, "negotiate"),
    ({"response_action": "reject", "pending_evidence": 3}, "close_out"),
    ({"response_action": "accept", "pending_evidence": 1}, "review_evidence"),
    ({"has_sent_offer": True, "ai_score": None, "has_contact": False}, "follow_up"),
    ({"has_sent_offer": True, "pending_evidence": 1}, "review_evidence"),
])
def test_actual_action_expression_on_synthetic_leads(changes, expected):
    lead = {"offer": {"slug": "draft"}, "ai_score": 70, "has_contact": True,
            "pending_evidence": 0, "response_action": "", "has_sent_offer": False,
            **changes}
    expression = build_action_queue_pipeline()[2]["$set"]["next_action"]
    assert evaluate(expression, lead) == expected


@pytest.mark.parametrize("lead,expected", [
    ({}, False),
    ({"status": "offer_sent", "offer": {"slug": "draft"}}, False),
    ({"offer": {"sent_history": None}}, False),
    ({"offer": {"sent_history": "invalid"}}, False),
    ({"offer": {"sent_history": [{"channel": "email", "at": "2026-09-07"}]}}, True),
    ({"offer": {"sent_history": [{"channel": "sms", "at": "2026-09-07", "status": "accepted"}]}}, True),
    ({"offer": {"sent_history": [{"channel": "sms", "at": "2026-09-07", "status": "failed"}]}}, False),
    ({"offer": {"sent_history": [{"channel": "sms"}]}}, False),
    ({"mail": {"mode": "test", "lob_id": "test", "mailed_at": "2026-09-07"}}, False),
    ({"mail": {"mode": "live", "lob_id": "letter", "mailed_at": "2026-09-07"}}, True),
])
def test_dispatch_expression_excludes_drafts_failed_and_test_sends(lead, expected):
    expression = build_action_queue_pipeline()[1]["$set"]["has_sent_offer"]
    assert evaluate(expression, lead) is expected
