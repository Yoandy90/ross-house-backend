import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import build_relationship_evidence_package as builder
from scripts.build_root_evidence_package import build_root_evidence_package
from scripts.plan_database_isolation import load_inventory, load_filter_contract
from scripts.prepare_relationship_evidence_request import EXPECTED_RELATIONSHIP_COLLECTIONS


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
NAMES = sorted(EXPECTED_RELATIONSHIP_COLLECTIONS)


@pytest.fixture
def inputs(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"database_name": "taxportal", "collection_count": 32,
        "collections": [{"name": n} for n in ["app_users", "tenants", *NAMES]]}))
    inventory = load_inventory(path)
    contract = load_filter_contract(Path(__file__).resolve().parents[1] /
                                    "config/database_isolation_filter_contract.json")
    provenance = {"prepared_by": "synthetic-preparer", "approved_by": "synthetic-approver",
        "prepared_at": "2026-09-06T10:00:00Z", "approved_at": "2026-09-06T11:00:00Z",
        "expires_at": "2026-09-20T11:00:00Z"}
    submission = {"version": 1, "source_database": "taxportal",
        "target_database": "ross_house_production", "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False, "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"], "provenance": provenance,
        "collections": [{"name": n, "root_ids": ["synthetic-" + n],
                         "ownership_basis": "Exact Ross House Rentals ownership review"}
                        for n in ["app_users", "tenants"]]}
    roots = build_root_evidence_package(inventory, contract, submission, now=NOW)
    return inventory, contract, roots


def filled(inputs, names=None, root="app_users"):
    submission = builder.prepare_relationship_submission(*inputs, now=NOW)
    submission["provenance"] = {
        "prepared_by": "relationship-preparer", "approved_by": "relationship-approver",
        "prepared_at": "2026-09-07T10:00:00Z", "approved_at": "2026-09-07T11:00:00Z",
        "expires_at": "2026-09-15T11:00:00Z",
    }
    submission["collections"] = [
        {"name": n, "root_collection": root, "relationship_paths": ["owner.root_id"],
         "exact_root_ids": ["synthetic-" + root]}
        for n in (NAMES if names is None else names)
    ]
    return submission


def test_template_has_all_placeholders_and_no_root_values(inputs):
    template = builder.prepare_relationship_submission(*inputs, now=NOW)
    assert len(template["collections"]) == 30
    assert all(row["exact_root_ids"] == [] for row in template["collections"])
    assert "synthetic-app_users" not in json.dumps(template)
    with pytest.raises(ValueError):
        builder.build_relationship_evidence_package(*inputs, template, now=NOW)


@pytest.mark.parametrize("root", ["app_users", "tenants"])
@pytest.mark.parametrize("name", NAMES)
def test_each_relationship_builds_partial_package_for_either_root(inputs, name, root):
    submission = filled(inputs, [name], root)
    before = copy.deepcopy((inputs, submission))
    package, summary = builder.build_relationship_evidence_package(*inputs, submission, now=NOW)
    assert len(package["collections"]) == 3
    assert summary["relationship_collections_validated"] == 1
    assert len(summary["pending_collections"]) == 29
    assert summary["relationship_evidence_complete"] is False
    assert summary["migration_authorized"] is False
    assert "synthetic-" not in json.dumps(summary)
    assert (inputs, submission) == before
    with pytest.raises(ValueError, match="incomplete"):
        builder.build_relationship_evidence_package(*inputs, submission, now=NOW,
                                                     require_complete=True)


def test_complete_package_is_deterministic_and_bound_to_new_review(inputs):
    submission = filled(inputs)
    package, summary = builder.build_relationship_evidence_package(*inputs, submission,
                                                                  now=NOW, require_complete=True)
    assert len(package["collections"]) == 32
    assert summary["relationship_evidence_complete"] is True
    assert summary["pending_collections"] == []
    assert package["provenance"]["evidence_id"] != inputs[2]["provenance"]["evidence_id"]
    submission["collections"].reverse()
    assert builder.build_relationship_evidence_package(*inputs, submission, now=NOW) == (package, summary)


@pytest.mark.parametrize("field", ["request_sha256", "root_package_sha256",
    "approved_root_evidence_id", "inventory_sha256", "contract_sha256", "target_owner"])
def test_submission_cannot_be_reused_against_other_context(inputs, field):
    submission = filled(inputs)
    submission[field] = "unrelated"
    with pytest.raises(ValueError, match=field):
        builder.build_relationship_evidence_package(*inputs, submission, now=NOW)


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "empty", "query", "wrong_root",
    "wrong_id", "operator", "self_approval", "expired", "extend_root", "before_root", "extra",
    "numeric_authorization", "boolean_version"])
def test_invalid_submission_fails_without_mutating_inputs(inputs, mutation):
    submission = filled(inputs, ["auth_sessions"])
    row = submission["collections"][0]
    if mutation == "unknown": row["name"] = "loans"
    elif mutation == "duplicate": submission["collections"].append(copy.deepcopy(row))
    elif mutation == "empty": submission["collections"] = []
    elif mutation == "query": row["query"] = {"$where": "never execute"}
    elif mutation == "wrong_root": row["root_collection"] = "loans"
    elif mutation == "wrong_id": row["exact_root_ids"] = ["synthetic-tenants"]
    elif mutation == "operator": row["relationship_paths"] = ["owner.$ne"]
    elif mutation == "self_approval": submission["provenance"]["approved_by"] = "RELATIONSHIP-PREPARER"
    elif mutation == "expired": submission["provenance"]["expires_at"] = "2026-09-07T11:30:00Z"
    elif mutation == "extend_root": submission["provenance"]["expires_at"] = "2026-09-21T11:00:00Z"
    elif mutation == "before_root": submission["provenance"]["prepared_at"] = "2026-09-05T11:00:00Z"
    elif mutation == "extra": submission["query"] = {}
    elif mutation == "numeric_authorization": submission["migration_authorized"] = 0
    elif mutation == "boolean_version": submission["version"] = True
    before = copy.deepcopy((inputs, submission))
    with pytest.raises(ValueError):
        builder.build_relationship_evidence_package(*inputs, submission, now=NOW)
    assert (inputs, submission) == before


def test_expired_roots_and_changed_root_content_are_rejected(inputs):
    submission = filled(inputs)
    inventory, contract, roots = copy.deepcopy(inputs)
    roots["provenance"]["expires_at"] = "2026-09-07T11:30:00Z"
    with pytest.raises(ValueError, match="expired"):
        builder.build_relationship_evidence_package(inventory, contract, roots, submission, now=NOW)
    inventory, contract, roots = copy.deepcopy(inputs)
    roots["provenance"]["expires_at"] = "2026-09-19T11:00:00Z"
    with pytest.raises(ValueError, match="root_package_sha256"):
        builder.build_relationship_evidence_package(inventory, contract, roots, submission, now=NOW)


def test_cli_prepare_build_and_failure_publish_only_private_valid_output(inputs, tmp_path, monkeypatch, capsys):
    class Clock:
        @staticmethod
        def now(tz): return NOW
    monkeypatch.setattr(builder, "datetime", Clock)
    root_path = tmp_path / "roots.json"
    root_path.write_text(json.dumps(inputs[2]))
    inventory_path = tmp_path / "inventory.json"
    template_path = tmp_path / "template.json"
    base = ["builder", "prepare", str(inventory_path), str(root_path), "--output", str(template_path)]
    monkeypatch.setattr("sys.argv", base)
    assert builder.main() == 0
    assert json.loads(template_path.read_text()) == builder.prepare_relationship_submission(*inputs, now=NOW)
    assert "synthetic-" not in capsys.readouterr().out
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(json.dumps(filled(inputs)))
    output = tmp_path / "combined.json"
    args = ["builder", "build", str(inventory_path), str(root_path), "--submission",
            str(submission_path), "--output", str(output), "--require-complete"]
    monkeypatch.setattr("sys.argv", args)
    assert builder.main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["relationship_evidence_complete"] is True
    assert output.stat().st_mode & 0o777 == 0o600
    expected = output.read_bytes()
    with pytest.raises(FileExistsError): builder.main()
    assert output.read_bytes() == expected
    assert capsys.readouterr().out == ""
    bad = filled(inputs)
    bad["collections"][0]["exact_root_ids"] = ["synthetic-not-allowed"]
    submission_path.write_text(json.dumps(bad))
    failed = tmp_path / "failed.json"
    args[args.index(str(output))] = str(failed)
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(ValueError): builder.main()
    assert not failed.exists()
    assert capsys.readouterr().out == ""
