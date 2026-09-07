from pathlib import Path

import pytest

from rental.opportunity_evidence_queue import (
    build_evidence_queue_pipeline, serialize_evidence_queue,
)


def test_queue_pipeline_is_bounded_and_treats_legacy_evidence_as_pending():
    pipeline = build_evidence_queue_pipeline(status="needs_review", skip=10, limit=20)
    assert pipeline[2]["$match"]["motivation.details.evidence_id"] == {"$type": "string"}
    assert pipeline[3]["$set"]["evidence_status"]["$ifNull"][1] == "needs_review"
    facets = pipeline[4]["$facet"]
    assert facets["items"][0] == {"$match": {"evidence_status": "needs_review"}}
    assert {"$skip": 10} in facets["items"]
    assert {"$limit": 20} in facets["items"]
    assert facets["items"][-1]["$project"]["lead_id"] == {"$toString": "$_id"}


def test_queue_source_filter_is_normalized_and_applies_only_to_rows():
    facets = build_evidence_queue_pipeline(
        status="confirmed", source="  Probate  ")[4]["$facet"]
    assert facets["items"][0]["$match"] == {
        "evidence_status": "confirmed", "evidence.source": "probate"}
    assert facets["status_counts"][0]["$group"]["_id"] == "$evidence_status"


@pytest.mark.parametrize("kwargs", [
    {"status": "unknown"}, {"skip": -1}, {"skip": 10_001},
    {"limit": 0}, {"limit": 101},
])
def test_queue_pipeline_rejects_unbounded_or_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        build_evidence_queue_pipeline(**kwargs)


def test_queue_serializer_returns_stable_counters_and_sources():
    payload = serialize_evidence_queue([{
        "items": [{"lead_id": "lead-1"}],
        "filtered_total": [{"count": 7}],
        "status_counts": [
            {"_id": "needs_review", "count": 7},
            {"_id": "confirmed", "count": 3},
        ],
        "source_counts": [{"_id": "probate", "count": 4}],
    }])
    assert payload == {
        "items": [{"lead_id": "lead-1"}], "total": 7,
        "counts": {"needs_review": 7, "confirmed": 3, "dismissed": 0},
        "sources": {"probate": 4},
    }


def test_admin_router_exposes_authenticated_evidence_queue():
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    assert '@router.get("/admin/deal-finder/evidence-queue")' in source
    assert "await auth_admin(request)" in source
    assert "build_evidence_queue_pipeline(" in source
