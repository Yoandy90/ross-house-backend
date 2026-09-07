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
        "review_evidence", "prepare_contract", "negotiate", "close_out",
        "analyze", "find_contact", "prepare_offer", "follow_up",
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
