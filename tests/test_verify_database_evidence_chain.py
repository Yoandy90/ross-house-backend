import copy
import json
import sys

import pytest

from scripts import verify_database_evidence_chain as verifier
from scripts.build_complete_evidence_package import (
    DEFAULT_CONTRACT, build_complete_evidence_package, prepare_complete_submission,
)
from scripts.build_relationship_evidence_package import (
    build_relationship_evidence_package, prepare_relationship_submission,
)
from scripts.build_root_evidence_package import build_root_evidence_package
from scripts.plan_database_isolation import (
    _canonical_sha256, _expected_evidence_id, load_filter_contract, load_inventory,
)


NOW = verifier.datetime(2026, 9, 7, 12, tzinfo=verifier.timezone.utc)


def provenance(prefix, prepared, approved, expires):
    return {"prepared_by": f"{prefix}-preparer", "approved_by": f"{prefix}-approver",
            "prepared_at": prepared, "approved_at": approved, "expires_at": expires}


def chain(tmp_path, *, outside=True):
    contract = load_filter_contract(DEFAULT_CONTRACT)
    names = sorted(n for definition in contract["requirements"].values()
                   for n in definition["collections"])
    if outside:
        names.append("synthetic_outside_contract")
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"database_name": "taxportal", "collection_count": len(names),
                                "collections": [{"name": name} for name in names]}))
    inventory = load_inventory(path)
    root_submission = {"version": 1, "source_database": "taxportal",
        "target_database": "ross_house_production", "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False, "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "provenance": provenance("root", "2026-09-06T08:00:00Z", "2026-09-06T09:00:00Z", "2026-09-20T12:00:00Z"),
        "collections": [{"name": name, "root_ids": [f"synthetic-{name}-001"],
                         "ownership_basis": "Synthetic Ross House Rentals chain fixture"}
                        for name in ("app_users", "tenants")]}
    root = build_root_evidence_package(inventory, contract, root_submission, now=NOW)
    relationship_submission = prepare_relationship_submission(inventory, contract, root, now=NOW)
    relationship_submission["provenance"] = provenance(
        "relationship", "2026-09-06T10:00:00Z", "2026-09-06T11:00:00Z", "2026-09-19T12:00:00Z")
    for index, row in enumerate(relationship_submission["collections"]):
        root_name = ("app_users", "tenants")[index % 2]
        row.update(root_collection=root_name, relationship_paths=["owner.root_id"],
                   exact_root_ids=[f"synthetic-{root_name}-001"])
    relationship, _ = build_relationship_evidence_package(
        inventory, contract, root, relationship_submission, now=NOW, require_complete=True)
    complete_submission = prepare_complete_submission(inventory, contract, relationship, now=NOW)
    complete_submission["provenance"] = provenance(
        "complete", "2026-09-06T12:00:00Z", "2026-09-06T13:00:00Z", "2026-09-18T12:00:00Z")
    for row in complete_submission["collections"]:
        row["evidence"] = ({"field_paths": ["application.source"],
                            "allowed_values": ["ross_house_rentals"]}
                           if row["requirement"] == "source_discriminator" else
                           {"field_paths": ["owner.root_id"],
                            "ownership_basis": "Synthetic Ross House Rentals schema fixture",
                            "reviewer": "schema-entry-reviewer",
                            "reviewed_at": "2026-09-06T12:30:00Z"})
    complete, _ = build_complete_evidence_package(
        inventory, contract, relationship, complete_submission, now=NOW, require_complete=True)
    return inventory, contract, root, relationship, complete


def rebind(package):
    package["collections_sha256"] = _canonical_sha256(package["collections"])
    package["provenance"]["evidence_id"] = _expected_evidence_id(
        package["inventory_sha256"], package["contract_sha256"],
        package["collections_sha256"], package["provenance"])


def test_valid_chain_with_outside_inventory_remains_blocked(tmp_path):
    report = verifier.verify_evidence_chain(*chain(tmp_path), now=NOW)
    assert report["root_entries_preserved"] is True
    assert report["relationship_entries_preserved"] is True
    assert report["review_blockers"] == ["outside_contract_ownership_review_required"]
    assert report["migration_authorized"] is False


def test_contract_only_chain_passes_offline_checks_without_authorization(tmp_path):
    report = verifier.verify_evidence_chain(*chain(tmp_path, outside=False), now=NOW)
    assert report["status"] == "offline_chain_checks_passed"
    assert report["review_blockers"] == []
    assert report["ownership_independently_verified"] is False
    assert report["production_readiness_assessed"] is False


def test_reviewer_reuse_across_stages_is_a_visible_blocker(tmp_path):
    values = list(chain(tmp_path, outside=False))
    values[3]["provenance"]["prepared_by"] = values[2]["provenance"]["prepared_by"]
    rebind(values[3])
    report = verifier.verify_evidence_chain(*values, now=NOW)
    assert report["reviewer_identity_reuse_count"] == 1
    assert report["review_blockers"] == ["reviewer_identity_reused_across_stages"]


@pytest.mark.parametrize("stage", ["root", "relationship"])
def test_rejects_changed_entries_even_when_each_package_is_self_consistent(tmp_path, stage):
    values = list(chain(tmp_path, outside=False))
    target = values[3] if stage == "root" else values[4]
    requirement = "explicit_root_id_allowlist" if stage == "root" else "relationship_closure"
    entry = next(row for row in target["collections"] if row["requirement"] == requirement)
    if stage == "root":
        entry["evidence"]["root_ids"] = ["synthetic-other-root-001"]
        for row in target["collections"]:
            if (row["requirement"] == "relationship_closure"
                    and row["evidence"]["root_collection"] == entry["name"]):
                row["evidence"]["root_ids"] = ["synthetic-other-root-001"]
    else:
        entry["evidence"]["relationship_paths"] = ["owner.alternate_root_id"]
    rebind(target)
    with pytest.raises(ValueError, match=f"evidence_chain_{stage}_entries_changed"):
        verifier.verify_evidence_chain(*values, now=NOW)


@pytest.mark.parametrize("change,error", [
    ("timeline", "evidence_chain_stage_timeline_invalid"),
    ("expiry", "evidence_chain_expiry_widened"),
])
def test_rejects_broken_stage_timeline_or_expiry_nesting(tmp_path, change, error):
    values = list(chain(tmp_path, outside=False))
    relationship = values[3]
    if change == "timeline":
        relationship["provenance"].update(prepared_at="2026-09-05T08:00:00Z",
                                           approved_at="2026-09-05T09:00:00Z")
    else:
        relationship["provenance"]["expires_at"] = "2026-09-21T12:00:00Z"
    rebind(relationship)
    with pytest.raises(ValueError, match=error):
        verifier.verify_evidence_chain(*values, now=NOW)


def test_summary_omits_evidence_values_and_identities(tmp_path):
    report = verifier.verify_evidence_chain(*chain(tmp_path), now=NOW)
    rendered = json.dumps(report)
    for private in ("synthetic-app_users", "app_users", "root-preparer", "relationship-approver",
                    "owner.root_id", "schema-entry-reviewer", "evd-"):
        assert private not in rendered


def test_cli_invalid_chain_fails_without_publishing_or_leaking(tmp_path, monkeypatch, capsys):
    inventory = tmp_path / "inventory.json"
    inventory.write_text('{"secret":"value","secret":false}')
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["verify", str(inventory), "root", "relation", "complete",
                                      "--output", str(output)])
    assert verifier.main() == 1
    assert not output.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "offline_evidence_chain_verification_failed\n"
