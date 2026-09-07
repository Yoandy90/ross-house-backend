from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from rental.opportunity_evidence_queue import (
    build_evidence_queue_pipeline, bulk_review_evidence, serialize_evidence_queue,
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
    assert facets["items"][1]["$sort"]["priority_score"] == -1
    priority = pipeline[3]["$set"]["priority_score"]["$round"][0]["$add"]
    assert priority[0]["$multiply"][1] == 0.7
    assert priority[1]["$multiply"][1] == 0.3


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
    assert '@router.patch("/admin/deal-finder/evidence/bulk-review")' in source


def test_queue_projection_excludes_internal_audit_and_unknown_provider_fields():
    projection = build_evidence_queue_pipeline()[4]["$facet"]["items"][-1]["$project"]
    assert "evidence" not in projection
    for field in ("evidence_id", "source", "confidence", "match_reasons", "source_url"):
        assert projection[f"evidence.{field}"] == 1
    for field in ("review_history", "reviewed_by", "review_note", "raw_provider_payload"):
        assert f"evidence.{field}" not in projection
    assert "evidence_index" not in projection


def test_queue_sort_breaks_ties_between_evidence_rows_from_same_lead():
    pipeline = build_evidence_queue_pipeline()
    assert pipeline[1]["$unwind"]["includeArrayIndex"] == "evidence_index"
    order = pipeline[4]["$facet"]["items"][1]["$sort"]
    assert list(order.items()) == [
        ("priority_score", -1), ("evidence.at", -1), ("_id", 1),
        ("evidence.evidence_id", 1), ("evidence_index", 1),
    ]
    rows = [dict(priority_score=98, evidence={"at": "same", "evidence_id": "a"},
                 _id="lead", evidence_index=index) for index in (2, 0, 1)]
    def key(row):
        values = []
        for field, direction in order.items():
            value = row
            for part in field.split("."):
                value = value[part]
            values.append(-value if direction == -1 and isinstance(value, int) else value)
        return tuple(values)
    assert [row["evidence_index"] for row in sorted(rows, key=key)] == [0, 1, 2]
    assert [row["evidence_index"] for row in sorted(reversed(rows), key=key)] == [0, 1, 2]


@pytest.mark.asyncio
async def test_bulk_review_dedupes_and_reports_partial_results():
    items = [
        {"lead_id": "1" * 24, "evidence_id": "a" * 24},
        {"lead_id": "1" * 24, "evidence_id": "a" * 24},
        {"lead_id": "2" * 24, "evidence_id": "b" * 24},
        {"lead_id": "bad", "evidence_id": "c" * 24},
    ]
    reviewer = AsyncMock(side_effect=[{"evidence": {}}, None])
    with patch("rental.opportunity_evidence_queue.review_evidence_atomic", reviewer):
        result = await bulk_review_evidence(
            object(), items, "confirmed", "admin-1", note="County verified")
    assert result["requested"] == 4
    assert result["unique"] == 3
    assert result["reviewed"] == 1
    assert result["not_found"] == 1
    assert result["invalid"] == 1
    assert reviewer.await_count == 2


@pytest.mark.asyncio
async def test_bulk_review_rejects_empty_oversized_and_invalid_status():
    with pytest.raises(ValueError, match="bulk_size_invalid"):
        await bulk_review_evidence(object(), [], "confirmed", "admin")
    with pytest.raises(ValueError, match="bulk_size_invalid"):
        await bulk_review_evidence(
            object(), [{"lead_id": "1", "evidence_id": "a"}] * 51,
            "confirmed", "admin")
    with pytest.raises(ValueError, match="status_invalid"):
        await bulk_review_evidence(
            object(), [{"lead_id": "1", "evidence_id": "a"}],
            "approved", "admin")
