import json
import os
from datetime import datetime, timezone

import pytest

from scripts.build_root_evidence_package import (
    build_root_evidence_package,
    write_private_package,
)
from scripts import build_root_evidence_package as builder


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


@pytest.mark.parametrize("approver", ["ＰＲＥＰＡＲＥＲ", "preparer ", "preparer\u200b"])
def test_builder_rejects_disguised_self_approval(approver):
    value = submission()
    value["provenance"]["approved_by"] = approver
    with pytest.raises(ValueError):
        build_root_evidence_package(inventory(), contract(), value, now=NOW)


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


def test_private_writer_completes_short_writes_before_publishing(tmp_path, monkeypatch):
    destination = tmp_path / "private" / "evidence.json"
    package = {"synthetic": "rental-id-1" * 100}
    original_write = os.write

    def short_write(fd, data):
        assert not destination.exists()
        return original_write(fd, data[:7])

    monkeypatch.setattr(builder.os, "write", short_write)
    write_private_package(destination, package, tmp_path / "repo")
    assert json.loads(destination.read_text()) == package
    assert destination.stat().st_mode & 0o777 == 0o600
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("failure", ["write", "zero_write", "fsync", "link"])
def test_private_writer_failure_does_not_publish_partial_package(tmp_path, monkeypatch, failure):
    destination = tmp_path / "private" / "evidence.json"
    original_write = os.write

    def fail(*args):
        raise OSError("synthetic storage failure")

    def partial_failure(fd, data):
        original_write(fd, data[:7])
        raise OSError("synthetic storage failure")

    if failure == "zero_write":
        monkeypatch.setattr(builder.os, "write", lambda *args: 0)
    elif failure == "write":
        monkeypatch.setattr(builder.os, "write", partial_failure)
    else:
        monkeypatch.setattr(builder.os, failure, fail)
    with pytest.raises(OSError):
        write_private_package(destination, {"synthetic": "rental-id-1"}, tmp_path / "repo")
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_private_writer_does_not_follow_dangling_destination_symlink(tmp_path):
    destination = tmp_path / "evidence.json"
    target = tmp_path / "unexpected.json"
    destination.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_private_package(destination, {"synthetic": True}, tmp_path / "repo")
    assert destination.is_symlink()
    assert not target.exists()


def test_private_writer_preserves_concurrent_destination(tmp_path, monkeypatch):
    destination = tmp_path / "private" / "evidence.json"
    original_write = os.write
    created = False

    def competing_write(fd, data):
        nonlocal created
        if not created:
            destination.write_text("existing evidence")
            created = True
        return original_write(fd, data)

    monkeypatch.setattr(builder.os, "write", competing_write)
    with pytest.raises(FileExistsError):
        write_private_package(destination, {"synthetic": True}, tmp_path / "repo")
    assert destination.read_text() == "existing evidence"
    assert list(destination.parent.iterdir()) == [destination]
