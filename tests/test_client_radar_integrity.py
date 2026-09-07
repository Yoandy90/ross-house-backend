from pathlib import Path

import pytest

from rental.radar_integrity import (
    ROSS_HOUSE_SOURCE, deterministic_lead_id, require_ross_house_source,
    safe_search_pattern, serialize_radar_client,
)


@pytest.mark.parametrize("value", [None, "", "Ross House Rentals",
                                    "Ross House Rentals LLC", "ross_house_rentals"])
def test_accepts_only_ross_house_sources(value):
    assert require_ross_house_source(value) == ROSS_HOUSE_SOURCE


@pytest.mark.parametrize("value", ["Ross Tax", "Ross Lending", "Other LLC",
                                    " Ross House Rentals", ["Ross House Rentals"]])
def test_rejects_foreign_ambiguous_or_malformed_sources(value):
    with pytest.raises(ValueError):
        require_ross_house_source(value)


def test_search_is_literal_not_regular_expression():
    assert safe_search_pattern("Oak (North)+ [1]") == r"Oak\ \(North\)\+\ \[1\]"
    with pytest.raises(ValueError):
        safe_search_pattern("x" * 81)


def test_serialization_removes_raw_government_identifiers_without_mutation():
    client = {"_id": "client-1", "full_name": "Synthetic Owner",
              "a_number": "A123456789", "passport": "P987654321"}
    result = serialize_radar_client(client)
    assert "a_number" not in result and "passport" not in result
    assert result["a_number_masked"] == "••••6789"
    assert result["passport_masked"] == "••••4321"
    assert client["a_number"] == "A123456789"


def test_lead_id_is_deterministic_nonrevealing_and_rejects_controls():
    first = deterministic_lead_id("507f1f77bcf86cd799439011")
    assert first == deterministic_lead_id("507f1f77bcf86cd799439011")
    assert first.startswith("radar-lead-") and "507f1f77" not in first
    with pytest.raises(ValueError):
        deterministic_lead_id("bad\nidentifier")


def test_router_uses_atomic_idempotent_lead_creation_and_cas_scan():
    source = (Path(__file__).resolve().parents[1] / "rental/client_radar_router.py").read_text()
    assert 'lead["_id"] = lead_id' in source
    assert 'db.leads.update_one(\n        {"_id": lead_id}, {"$setOnInsert": lead}, upsert=True' in source
    assert '"last_scan": client.get("last_scan")' in source
    assert "radar_client_changed_during_scan" in source


def test_router_preserves_all_opportunity_signals():
    source = (Path(__file__).resolve().parents[1] / "rental/client_radar_router.py").read_text()
    for signal in ("ice", "tdcj", "jail", "struckoff", "tax_debt", "deceased", "phone_dead"):
        assert f'"{signal}"' in source


def test_all_existing_candidate_operations_are_scoped_to_ross_house():
    source = (Path(__file__).resolve().parents[1] / "rental/client_radar_router.py").read_text()
    assert 'RADAR_SCOPE = {"source_business": ROSS_HOUSE_SOURCE}' in source
    assert "count_documents(RADAR_SCOPE)" in source
    assert "find(RADAR_SCOPE).limit(300)" in source
    assert source.count("**RADAR_SCOPE") >= 6
