from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rental.opportunity_source_health import (
    SOURCE_DEFINITIONS, build_source_health_pipeline, record_source_run,
    serialize_source_health,
)


NOW = datetime(2026, 9, 7, 20, tzinfo=timezone.utc)


def test_pipeline_is_bounded_and_treats_legacy_reviews_as_pending():
    pipeline = build_source_health_pipeline()
    assert pipeline[0]["$project"]["details"]["$cond"][2] == []
    assert pipeline[2]["$match"]["details.evidence_id"] == {"$type": "string"}
    assert pipeline[3]["$set"]["review_status"]["$cond"][2] == "needs_review"
    group = pipeline[4]["$group"]
    assert group["last_evidence_at"] == {"$max": "$details.at"}
    assert set(group) == {"_id", "total", "needs_review", "confirmed",
                          "dismissed", "last_evidence_at"}


def test_serializer_reports_fresh_stale_partial_and_never_run_sources():
    rows = [
        {"_id": "propertyradar", "total": 4, "needs_review": 1,
         "confirmed": 2, "dismissed": 1,
         "last_evidence_at": "2026-09-07T10:00:00+00:00"},
        {"_id": "probate", "total": 2, "needs_review": 2,
         "last_evidence_at": "2026-08-01T10:00:00+00:00"},
    ]
    runs = {"sources": {
        "propertyradar": {"last_run_at": "2026-09-07T10:00:00+00:00",
                          "status": "success", "scanned": 20, "matched": 4},
        "obituary": {"last_run_at": "2026-09-06T10:00:00+00:00",
                     "status": "partial", "scanned": 5, "matched": 1, "errors": 1},
    }}
    result = serialize_source_health(rows, runs, now=NOW, stale_days=30)
    by_id = {item["source"]: item for item in result["sources"]}
    assert by_id["propertyradar"]["status"] == "healthy"
    assert by_id["obituary"]["status"] == "partial"
    assert by_id["probate"]["status"] == "stale"
    assert by_id["eviction"]["status"] == "never_run"
    assert by_id["probate"]["age_days"] == 37
    assert result["summary"] == {
        "sources": len(SOURCE_DEFINITIONS), "requiring_attention": 7,
        "evidence_total": 6, "pending_review": 3, "reviewed_pct": 50,
    }


def test_serializer_marks_legacy_fresh_evidence_as_observed_only():
    result = serialize_source_health([{
        "_id": "divorce", "total": 1,
        "last_evidence_at": "2026-09-07T19:00:00Z",
    }], None, now=NOW)
    item = next(row for row in result["sources"] if row["source"] == "divorce")
    assert item["status"] == "observed_only"
    assert item["requires_attention"] is False


@pytest.mark.parametrize("days", [0, 366])
def test_serializer_rejects_invalid_stale_window(days):
    with pytest.raises(ValueError, match="opportunity_source_stale_days_invalid"):
        serialize_source_health([], None, stale_days=days, now=NOW)


@pytest.mark.asyncio
async def test_run_receipt_is_safe_bounded_metadata():
    collection = SimpleNamespace(update_one=AsyncMock())
    db = SimpleNamespace(app_settings=collection)
    await record_source_run(
        db, "obituary", scanned=9, matched=2, errors=1, now=NOW)
    query, update = collection.update_one.await_args.args
    assert query == {"_id": "opportunity_source_health"}
    assert update == {"$set": {"sources.obituary": {
        "last_run_at": "2026-09-07T20:00:00+00:00", "status": "partial",
        "scanned": 9, "matched": 2, "errors": 1,
    }}}
    assert collection.update_one.await_args.kwargs == {"upsert": True}


@pytest.mark.asyncio
async def test_run_receipt_rejects_unknown_source_and_negative_counts():
    db = SimpleNamespace(app_settings=SimpleNamespace(update_one=AsyncMock()))
    with pytest.raises(ValueError, match="opportunity_source_invalid"):
        await record_source_run(db, "other", scanned=1, matched=0, now=NOW)
    with pytest.raises(ValueError, match="opportunity_source_run_counts_invalid"):
        await record_source_run(db, "probate", scanned=-1, matched=0, now=NOW)
    db.app_settings.update_one.assert_not_awaited()


def test_router_records_runs_and_exposes_authenticated_health_endpoint():
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    assert '@router.get("/admin/deal-finder/evidence-health")' in source
    assert "build_source_health_pipeline()" in source
    assert 'record_source_run(db, "propertyradar"' in source
    assert "db, body.source_type" in source
    assert 'db, "obituary"' in source
