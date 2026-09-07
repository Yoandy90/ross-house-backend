from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rental.opportunity_evidence import (
    add_evidence_atomic, address_probe, evidence_detail, evidence_id,
    match_address, match_person, obituary_source_allowed, person_tokens,
    review_evidence_atomic,
)


NOW = datetime(2026, 9, 7, 18, tzinfo=timezone.utc)


def test_person_match_is_order_independent_and_accent_safe():
    match = match_person("José Antonio García", "GARCIA, JOSE ANTONIO ESTATE")
    assert match == {"matched": True, "confidence": 100,
                     "reasons": ["exact_name_tokens"]}
    assert person_tokens("GARCÍA, JOSÉ JR.") == ["GARCIA", "JOSE"]


@pytest.mark.parametrize("record,owner,reason", [
    ("John Smith", "Smith Holdings LLC", "entity_owner"),
    ("John Smith", "Smith Mary", "name_anchors_mismatch"),
    ("Madonna", "Madonna Estate", "insufficient_name_tokens"),
])
def test_person_match_rejects_unsafe_false_positives(record, owner, reason):
    result = match_person(record, owner)
    assert result["matched"] is False
    assert result["reasons"] == [reason]


def test_address_match_requires_house_number_and_real_street_token():
    assert address_probe("305 N Bruce Avenue, Dumas TX") == ("305", "BRUCE")
    assert match_address("305 N Bruce Avenue", "305 BRUCE AVE, DUMAS TX")["matched"] is True
    assert match_address("305 N Bruce Avenue", "306 BRUCE AVE")["matched"] is False
    assert match_address("305 N Bruce Avenue", "305 OAK AVE")["matched"] is False


@pytest.mark.parametrize("url,allowed", [
    ("https://www.echovita.com/us/obituaries/tx/dumas", True),
    ("https://www.morrisonfuneraldirectors.com/obituaries", True),
    ("http://www.echovita.com/us/obituaries/tx/dumas", False),
    ("https://echovita.com.evil.example/", False),
    ("https://user:pass@echovita.com/", False),
    ("https://echovita.com:not-a-port/", False),
    ("https://127.0.0.1/internal", False),
    ("file:///etc/passwd", False),
])
def test_obituary_sources_are_https_allowlisted(url, allowed):
    assert obituary_source_allowed(url) is allowed


def test_evidence_id_is_deterministic_and_uses_case_number_when_available():
    first = evidence_id("probate", {"case_number": "PR-123", "name": "One"})
    second = evidence_id("probate", {"name": "Changed", "case_number": "PR-123"})
    assert first == second
    assert len(first) == 24
    assert first != evidence_id("divorce", {"case_number": "PR-123"})


def test_evidence_detail_is_reviewable_bounded_and_does_not_echo_unsafe_url():
    detail = evidence_detail(
        "obituary", {"name": "A" * 500, "date": "2026-09-01"},
        {"confidence": 92, "reasons": ["first_last_tokens"]},
        source_url="https://169.254.169.254/latest/meta-data", now=NOW,
    )
    assert len(detail["record_name"]) == 300
    assert detail["source_url"] == ""
    assert detail["review_status"] == "needs_review"
    assert detail["at"] == "2026-09-07T18:00:00+00:00"


def test_evidence_detail_rejects_naive_clock():
    with pytest.raises(ValueError, match="clock_must_be_aware"):
        evidence_detail("probate", {"case_number": "1"},
                        {"confidence": 98, "reasons": []},
                        now=datetime(2026, 9, 7))


@pytest.mark.asyncio
async def test_atomic_add_dedupes_by_evidence_id_and_adds_all_signals():
    collection = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=1)))
    db = SimpleNamespace(deal_finder_leads=collection)
    detail = evidence_detail(
        "propertyradar", {"case_number": "radar-7", "address": "305 Bruce"},
        {"confidence": 98, "reasons": ["house_number", "street_token"]}, now=NOW,
    )
    assert await add_evidence_atomic(
        db, "lead-1", ["probate", "preforeclosure", "probate"], detail) is True
    query, pipeline = collection.update_one.await_args.args
    assert query == {"_id": "lead-1", "motivation.details.evidence_id": {
        "$ne": detail["evidence_id"]}}
    assert pipeline[0]["$set"]["motivation"]["$cond"][2] == {}
    fields = pipeline[1]["$set"]
    assert fields["motivation.signals"]["$setUnion"][1] == [
        "probate", "preforeclosure"]
    saved_detail = fields["motivation.details"]["$concatArrays"][1][0]
    assert saved_detail == {**detail, "signals": ["probate", "preforeclosure"]}
    assert fields["motivation.updated_at"] == detail["at"]


@pytest.mark.asyncio
async def test_atomic_add_reports_existing_evidence_without_overwrite():
    collection = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=0)))
    db = SimpleNamespace(deal_finder_leads=collection)
    assert await add_evidence_atomic(
        db, "lead-1", "possible_deceased", {"evidence_id": "same", "at": "now"}) is False


def review_database(status="confirmed"):
    evidence = {
        "evidence_id": "a" * 24, "signals": ["possible_deceased"],
        "review_status": status,
    }
    collection = SimpleNamespace(
        find_one_and_update=AsyncMock(return_value={
            "motivation": {"details": [evidence], "signals": ["possible_deceased"]}}),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
        find_one=AsyncMock(return_value={
            "motivation": {"details": [evidence], "signals": ["possible_deceased"]}}),
    )
    return SimpleNamespace(deal_finder_leads=collection), collection


@pytest.mark.asyncio
async def test_confirm_review_is_audited_bounded_and_restores_signal():
    db, collection = review_database("confirmed")
    result = await review_evidence_atomic(
        db, "lead-1", "a" * 24, "confirmed", "admin-7",
        note="Confirmed in county file", now=NOW)
    assert result["evidence"]["review_status"] == "confirmed"
    query, update = collection.find_one_and_update.await_args.args
    assert query["motivation.details"]["$elemMatch"] == {"evidence_id": "a" * 24}
    history = update["$push"]["motivation.details.$[evidence].review_history"]
    assert history["$slice"] == -50
    assert history["$each"][0] == {
        "status": "confirmed", "reviewer_id": "admin-7",
        "note": "Confirmed in county file", "at": "2026-09-07T18:00:00+00:00",
    }
    assert collection.find_one_and_update.await_args.kwargs["array_filters"] == [
        {"evidence.evidence_id": "a" * 24}]
    restore = collection.update_one.await_args.args[1]
    assert restore == {"$addToSet": {"motivation.signals": {
        "$each": ["possible_deceased"]}}}


@pytest.mark.asyncio
async def test_dismiss_review_only_pulls_signal_without_active_or_legacy_support():
    db, collection = review_database("dismissed")
    await review_evidence_atomic(
        db, "lead-1", "a" * 24, "dismissed", "admin-7", now=NOW)
    query, update = collection.update_one.await_args.args
    assert query["_id"] == "lead-1"
    assert len(query["$and"]) == 2
    assert update == {"$pull": {"motivation.signals": "possible_deceased"}}


@pytest.mark.parametrize("evidence,status", [
    ("bad", "confirmed"), ("a" * 24, "approved"),
])
@pytest.mark.asyncio
async def test_review_rejects_invalid_inputs_before_database_access(evidence, status):
    db, collection = review_database()
    with pytest.raises(ValueError):
        await review_evidence_atomic(
            db, "lead-1", evidence, status, "admin-7", now=NOW)
    collection.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_returns_none_when_evidence_does_not_exist():
    db, collection = review_database()
    collection.find_one_and_update.return_value = None
    assert await review_evidence_atomic(
        db, "lead-1", "a" * 24, "confirmed", "admin-7", now=NOW) is None
    collection.update_one.assert_not_awaited()


def test_all_opportunity_sources_use_verified_atomic_evidence_boundary():
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    assert source.count("add_evidence_atomic(") == 3
    assert "source_not_allowlisted" in source
    assert "match_person(o[\"name\"]" in source
    assert "_find_verified_address_lead(db, addr)" in source
    assert ").limit(10)" in source
    assert '{"$set": {"motivation": motivation}}' not in source
    assert '"review_status": detail["review_status"]' in source
    assert '@router.patch("/admin/deal-finder/leads/{lead_id}/evidence/{evidence_id_value}")' in source
    assert "review_evidence_atomic(" in source
