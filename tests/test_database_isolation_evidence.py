import json

import pytest

from scripts.plan_database_isolation import (
    apply_filter_contract,
    apply_offline_filter_evidence,
    load_inventory,
)


REQUIREMENTS = {
    "explicit_root_id_allowlist": ["app_users"],
    "relationship_closure": ["auth_sessions"],
    "source_discriminator": ["email_events"],
    "manual_schema_review": ["users"],
}


def rows():
    return [
        {"name": name, "migration_strategy": "document_filter_required"}
        for names in REQUIREMENTS.values()
        for name in names
    ]


def contract():
    return {
        "requirements": {
            requirement: {"evidence": "required", "collections": names}
            for requirement, names in REQUIREMENTS.items()
        }
    }


def package(digest, entries=None, **overrides):
    value = {
        "version": 1,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
        "default_action": "block",
        "inventory_sha256": digest,
        "collections": entries or [],
    }
    value.update(overrides)
    return value


def evidence_entries():
    return [
        {"name": "app_users", "requirement": "explicit_root_id_allowlist",
         "evidence": {"root_ids": ["rental-user-1"]}},
        {"name": "auth_sessions", "requirement": "relationship_closure",
         "evidence": {"root_collection": "app_users",
                      "relationship_paths": ["user_id"],
                      "root_ids": ["rental-user-1"]}},
        {"name": "email_events", "requirement": "source_discriminator",
         "evidence": {"field_paths": ["application"],
                      "allowed_values": ["ross_house_rentals"]}},
        {"name": "users", "requirement": "manual_schema_review",
         "evidence": {"field_paths": ["tenant_id"],
                      "ownership_basis": "Exact Rentals tenant relationship",
                      "reviewer": "security-reviewer",
                      "reviewed_at": "2026-09-06T23:30:00Z"}},
    ]


def test_inventory_hash_is_raw_file_bound_and_changes_with_inventory(tmp_path):
    path = tmp_path / "inventory.json"
    payload = {"database_name": "taxportal", "collection_count": 1,
               "collections": [{"name": "app_users"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    first = load_inventory(path)["_inventory_sha256"]
    path.write_text(json.dumps({**payload, "snapshot": "changed"}), encoding="utf-8")
    second = load_inventory(path)["_inventory_sha256"]
    assert len(first) == 64
    assert first != second


def test_all_four_evidence_formats_validate_without_authorizing_migration():
    data = rows()
    apply_filter_contract(data, contract())
    counts = apply_offline_filter_evidence(data, package("a" * 64, evidence_entries()), "a" * 64)
    assert counts == {"submitted": 4, "validated": 4, "still_blocked": 0}
    assert all(row["filter_status"] == "offline_evidence_validated" for row in data)
    assert package("a" * 64)["migration_authorized"] is False
    assert all("query" not in entry and "filter" not in entry for entry in evidence_entries())


def test_partial_evidence_leaves_unsubmitted_collections_blocked():
    data = rows()
    apply_filter_contract(data, contract())
    counts = apply_offline_filter_evidence(data, package("b" * 64, evidence_entries()[:1]), "b" * 64)
    assert counts["still_blocked"] == 3
    assert data[0]["filter_status"] == "offline_evidence_validated"
    assert all(row["filter_status"] == "blocked_pending_evidence" for row in data[1:])


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda value: value.update(inventory_sha256="0" * 64), "inventory_hash_mismatch"),
        (lambda value: value.update(target_database="taxportal"), "target_database_invalid"),
        (lambda value: value.update(migration_authorized=True), "migration_authorized_invalid"),
        (lambda value: value.update(default_action="allow"), "default_action_invalid"),
    ],
)
def test_package_tampering_fails_closed(mutate, error):
    data = rows()
    apply_filter_contract(data, contract())
    value = package("a" * 64, evidence_entries())
    mutate(value)
    with pytest.raises(ValueError, match=error):
        apply_offline_filter_evidence(data, value, "a" * 64)


def test_new_removed_or_duplicate_collections_fail_closed():
    data = rows()
    with pytest.raises(ValueError, match="filter_contract_missing:new_collection"):
        apply_filter_contract(data + [{"name": "new_collection", "migration_strategy": "document_filter_required"}], contract())
    with pytest.raises(ValueError, match="filter_contract_extra:users"):
        apply_filter_contract(data[:-1], contract())
    apply_filter_contract(data, contract())
    duplicate = evidence_entries() + [evidence_entries()[0]]
    with pytest.raises(ValueError, match="collection_duplicate_or_invalid:app_users"):
        apply_offline_filter_evidence(data, package("a" * 64, duplicate), "a" * 64)


@pytest.mark.parametrize("bad_ids", [[], [""], ["*"], ["same", "same"]])
def test_empty_wildcard_or_duplicate_ids_are_rejected(bad_ids):
    data = rows()
    apply_filter_contract(data, contract())
    entry = evidence_entries()[0]
    entry["evidence"]["root_ids"] = bad_ids
    with pytest.raises(ValueError, match="root_ids"):
        apply_offline_filter_evidence(data, package("a" * 64, [entry]), "a" * 64)


def test_incomplete_relationship_and_missing_discriminator_are_rejected():
    data = rows()
    apply_filter_contract(data, contract())
    relationship = evidence_entries()[1]
    relationship["evidence"]["relationship_paths"] = []
    with pytest.raises(ValueError, match="relationship_paths_invalid"):
        apply_offline_filter_evidence(data, package("a" * 64, [relationship]), "a" * 64)
    discriminator = evidence_entries()[2]
    discriminator["evidence"]["allowed_values"] = []
    with pytest.raises(ValueError, match="allowed_values_invalid"):
        apply_offline_filter_evidence(data, package("a" * 64, [discriminator]), "a" * 64)


@pytest.mark.parametrize(
    "external",
    ["taxportal", "Ross Tax", "ross_tax", "loans", "Ross Lending", "ross-lending"],
)
def test_external_system_discriminators_are_prohibited(external):
    data = rows()
    apply_filter_contract(data, contract())
    entry = evidence_entries()[2]
    entry["evidence"]["allowed_values"] = [external]
    with pytest.raises(ValueError, match="external_source_prohibited"):
        apply_offline_filter_evidence(data, package("a" * 64, [entry]), "a" * 64)


def test_requirement_substitution_and_unknown_collection_are_rejected():
    data = rows()
    apply_filter_contract(data, contract())
    wrong = evidence_entries()[0]
    wrong["requirement"] = "source_discriminator"
    with pytest.raises(ValueError, match="requirement_mismatch:app_users"):
        apply_offline_filter_evidence(data, package("a" * 64, [wrong]), "a" * 64)
    unknown = evidence_entries()[0]
    unknown["name"] = "loans"
    with pytest.raises(ValueError, match="unknown_collection:loans"):
        apply_offline_filter_evidence(data, package("a" * 64, [unknown]), "a" * 64)


def test_unexpected_package_fields_and_failed_batch_are_atomic():
    data = rows()
    apply_filter_contract(data, contract())
    unexpected = package("a" * 64, evidence_entries(), query={"$ne": None})
    with pytest.raises(ValueError, match="package_fields_invalid"):
        apply_offline_filter_evidence(data, unexpected, "a" * 64)

    entries = evidence_entries()
    entries[-1]["evidence"]["reviewer"] = ""
    with pytest.raises(ValueError, match="reviewer_invalid"):
        apply_offline_filter_evidence(
            data, package("a" * 64, entries), "a" * 64
        )
    assert all(
        row["filter_status"] == "blocked_pending_evidence" for row in data
    )
