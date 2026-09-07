from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rental.opportunity_evidence import (
    add_evidence_atomic, address_probe, evidence_detail, evidence_id,
    match_address, match_person, obituary_source_allowed, person_tokens,
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
    assert fields["motivation.details"]["$concatArrays"][1] == [detail]
    assert fields["motivation.updated_at"] == detail["at"]


@pytest.mark.asyncio
async def test_atomic_add_reports_existing_evidence_without_overwrite():
    collection = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=0)))
    db = SimpleNamespace(deal_finder_leads=collection)
    assert await add_evidence_atomic(
        db, "lead-1", "possible_deceased", {"evidence_id": "same", "at": "now"}) is False


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
