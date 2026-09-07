import json
from datetime import datetime, timezone

import pytest

from scripts.build_root_evidence_package import (
    build_root_evidence_package,
    write_private_package,
)


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def inventory():
    return {
        "database_name": "taxportal",
        "_inventory_sha256": "a" * 64,
    }


def contract():
    return {
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "migration_authorized": False,
        "default_action": "block",
        "requirements": {
            "explicit_root_id_allowlist": {
                "collections": ["app_users", "tenants"]
            }
        },
        "_contract_sha256": "b" * 64,
    }


def submission():
    return {
        "version": 1,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
        "inventory_sha256": "a" * 64,
        "contract_sha256": "b" * 64,
        "provenance": {
            "prepared_by": "preparer",
            "approved_by": "approver",
            "prepared_at": "2026-09-06T10:00:00Z",
            "approved_at": "2026-09-06T11:00:00Z",
            "expires_at": "2026-10-06T11:00:00Z",
        },
        "collections": [
            {
                "name": "app_users",
                "root_ids": ["rental-user-1"],
                "ownership_basis": "Exact Ross House Rentals account review",
            },
            {
                "name": "tenants",
                "root_ids": ["rental-tenant-1"],
                "ownership_basis": "Exact Ross House Rentals lease review",
            },
        ],
    }


def test_builds_self_validated_non_executable_v2_package():
    package = build_root_evidence_package(
        inventory(), contract(), submission(), now=NOW
    )
    assert package["version"] == 2
    assert package["migration_authorized"] is False
    assert package["default_action"] == "block"
    assert package["provenance"]["evidence_id"].startswith("evd-")
    assert [row["name"] for row in package["collections"]] == [
        "app_users",
        "tenants",
    ]
    assert all(
        set(row["evidence"]) == {"root_ids", "ownership_basis"}
        for row in package["collections"]
    )
    assert "query" not in json.dumps(package).casefold()
    assert "filter" not in json.dumps(package).casefold()


@pytest.mark.parametrize("name", ["loans", "users", "admin_tax_returns"])
def test_external_or_extra_collection_is_rejected(name):
    value = submission()
    value["collections"].append(
        {"name": name, "root_ids": ["x"], "ownership_basis": "other"}
    )
    with pytest.raises(ValueError, match="collections_must_be_exact"):
        build_root_evidence_package(inventory(), contract(), value, now=NOW)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("target_database", "taxportal", "target_database_invalid"),
        ("migration_authorized", True, "migration_authorized_invalid"),
        ("inventory_sha256", "c" * 64, "inventory_sha256_mismatch"),
        ("contract_sha256", "c" * 64, "contract_sha256_mismatch"),
    ],
)
def test_rejects_target_authorization_or_hash_changes(field, value, error):
    modified = submission()
    modified[field] = value
    with pytest.raises(ValueError, match=error):
        build_root_evidence_package(
            inventory(), contract(), modified, now=NOW
        )


def test_rejects_wrong_inventory_or_contract_scope():
    wrong_inventory = inventory()
    wrong_inventory["database_name"] = "ross_tax"
    with pytest.raises(ValueError, match="inventory_source_invalid"):
        build_root_evidence_package(
            wrong_inventory, contract(), submission(), now=NOW
        )
    wrong_contract = contract()
    wrong_contract["requirements"]["explicit_root_id_allowlist"][
        "collections"
    ] = ["app_users", "loans"]
    with pytest.raises(ValueError, match="contract_roots_invalid"):
        build_root_evidence_package(
            inventory(), wrong_contract, submission(), now=NOW
        )


def test_rejects_incomplete_or_extra_provenance():
    for key, value in (("approved_by", None), ("ticket", "external")):
        modified = submission()
        if value is None:
            del modified["provenance"][key]
        else:
            modified["provenance"][key] = value
        with pytest.raises(ValueError, match="provenance_invalid"):
            build_root_evidence_package(
                inventory(), contract(), modified, now=NOW
            )


def test_rejects_duplicate_empty_wildcard_and_external_ownership():
    for ids in ([], [""], ["*"], ["same", "same"]):
        value = submission()
        value["collections"][0]["root_ids"] = ids
        with pytest.raises(ValueError, match="root_ids"):
            build_root_evidence_package(
                inventory(), contract(), value, now=NOW
            )
    value = submission()
    value["collections"][0]["ownership_basis"] = "Ross Lending loan record"
    with pytest.raises(ValueError, match="external_source_prohibited"):
        build_root_evidence_package(inventory(), contract(), value, now=NOW)


def test_private_writer_refuses_repo_paths_and_overwrite(tmp_path):
    package = build_root_evidence_package(
        inventory(), contract(), submission(), now=NOW
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(ValueError, match="outside_repository"):
        write_private_package(
            repository / "evidence.json", package, repository
        )
    destination = tmp_path / "private" / "evidence.json"
    write_private_package(destination, package, repository)
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private_package(destination, package, repository)
