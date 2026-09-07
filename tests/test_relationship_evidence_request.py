import copy
import json
from datetime import datetime, timezone

import pytest

from scripts.plan_database_isolation import (
    _canonical_sha256,
    _expected_evidence_id,
)
from scripts.prepare_relationship_evidence_request import (
    EXPECTED_RELATIONSHIP_COLLECTIONS,
    build_relationship_evidence_request,
)


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
RELATIONSHIPS = sorted(EXPECTED_RELATIONSHIP_COLLECTIONS)


def inventory():
    return {
        "database_name": "taxportal",
        "_inventory_sha256": "a" * 64,
        "collections": [
            {"name": "app_users"},
            {"name": "tenants"},
            *({"name": name} for name in RELATIONSHIPS),
            {"name": "loans"},
        ],
    }


def contract():
    return {
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "migration_authorized": False,
        "default_action": "block",
        "_contract_sha256": "b" * 64,
        "requirements": {
            "explicit_root_id_allowlist": {
                "collections": ["app_users", "tenants"]
            },
            "relationship_closure": {"collections": RELATIONSHIPS.copy()},
        },
    }


def root_package():
    entries = [
        {
            "name": "app_users",
            "requirement": "explicit_root_id_allowlist",
            "evidence": {
                "root_ids": ["rental-user-1"],
                "ownership_basis": "Exact Rentals account review",
            },
        },
        {
            "name": "tenants",
            "requirement": "explicit_root_id_allowlist",
            "evidence": {
                "root_ids": ["rental-tenant-1"],
                "ownership_basis": "Exact Rentals lease review",
            },
        },
    ]
    collections_sha256 = _canonical_sha256(entries)
    provenance = {
        "evidence_id": "",
        "prepared_by": "offline-preparer",
        "approved_by": "independent-approver",
        "prepared_at": "2026-09-06T10:00:00Z",
        "approved_at": "2026-09-06T11:00:00Z",
        "expires_at": "2026-10-06T11:00:00Z",
    }
    provenance["evidence_id"] = _expected_evidence_id(
        "a" * 64, "b" * 64, collections_sha256, provenance
    )
    return {
        "version": 2,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
        "default_action": "block",
        "inventory_sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "collections_sha256": collections_sha256,
        "provenance": provenance,
        "collections": entries,
    }


def test_request_is_root_bound_complete_and_contains_no_values_or_queries():
    package = root_package()
    request = build_relationship_evidence_request(
        inventory(), contract(), package, now=NOW
    )
    assert request["approved_root_evidence_id"] == package["provenance"]["evidence_id"]
    assert request["migration_authorized"] is False
    assert request["executable_queries_present"] is False
    assert request["document_values_present"] is False
    assert [row["name"] for row in request["collections"]] == sorted(RELATIONSHIPS)
    rendered = json.dumps(request).casefold()
    assert "rental-user-1" not in rendered
    assert "rental-tenant-1" not in rendered
    assert "loans" not in rendered
    assert "$match" not in rendered
    assert "$in" not in rendered


@pytest.mark.parametrize("missing", RELATIONSHIPS)
def test_missing_inventory_relationship_collection_is_rejected(missing):
    value = inventory()
    value["collections"] = [
        row for row in value["collections"] if row["name"] != missing
    ]
    with pytest.raises(ValueError, match=f"inventory_missing:{missing}"):
        build_relationship_evidence_request(
            value, contract(), root_package(), now=NOW
        )


def test_partial_duplicate_or_extra_roots_are_rejected():
    for mutate in (
        lambda rows: rows.pop(),
        lambda rows: rows.append(copy.deepcopy(rows[0])),
        lambda rows: rows.append(
            {
                "name": "loans",
                "requirement": "explicit_root_id_allowlist",
                "evidence": {"root_ids": ["x"], "ownership_basis": "other"},
            }
        ),
    ):
        package = root_package()
        mutate(package["collections"])
        with pytest.raises(ValueError, match="roots_must_be_exact"):
            build_relationship_evidence_request(
                inventory(), contract(), package, now=NOW
            )


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("inventory_sha256", "c" * 64, "inventory_sha256_mismatch"),
        ("contract_sha256", "c" * 64, "contract_sha256_mismatch"),
        ("migration_authorized", True, "migration_authorized_invalid"),
        ("target_database", "taxportal", "target_database_invalid"),
    ],
)
def test_root_package_tampering_is_rejected(field, value, error):
    package = root_package()
    package[field] = value
    with pytest.raises(ValueError, match=error):
        build_relationship_evidence_request(
            inventory(), contract(), package, now=NOW
        )


def test_expired_root_approval_is_rejected():
    package = root_package()
    package["provenance"]["expires_at"] = "2026-09-07T12:00:00Z"
    with pytest.raises(ValueError, match="expired"):
        build_relationship_evidence_request(
            inventory(), contract(), package, now=NOW
        )


def test_relationship_contract_rejects_duplicates_roots_and_empty_names():
    for names in (
        ["auth_sessions", "auth_sessions"],
        ["auth_sessions", "app_users"],
        ["auth_sessions", ""],
        [],
    ):
        value = contract()
        value["requirements"]["relationship_closure"]["collections"] = names
        with pytest.raises(ValueError, match="collections_must_be_exact"):
            build_relationship_evidence_request(
                inventory(), value, root_package(), now=NOW
            )
