import copy
import json
from datetime import datetime, timezone

import pytest

from scripts import build_complete_evidence_package as builder
from scripts.build_relationship_evidence_package import prepare_relationship_submission, build_relationship_evidence_package
from scripts.build_root_evidence_package import build_root_evidence_package
from scripts.plan_database_isolation import load_filter_contract, load_inventory

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
CONTRACT = load_filter_contract(builder.DEFAULT_CONTRACT)
SUPPLEMENTAL_NAMES = sorted(n for r, d in CONTRACT["requirements"].items()
                            if r in builder.SUPPLEMENTAL for n in d["collections"])


@pytest.fixture
def inputs(tmp_path):
    contract = load_filter_contract(builder.DEFAULT_CONTRACT)
    names = sorted(n for d in contract["requirements"].values() for n in d["collections"])
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"database_name": "taxportal", "collection_count": len(names) + 1,
        "collections": [{"name": n} for n in [*names, "synthetic_outside_contract"]]}))
    inventory = load_inventory(path)
    provenance = {"prepared_by": "synthetic-preparer", "approved_by": "synthetic-approver",
        "prepared_at": "2026-09-06T10:00:00Z", "approved_at": "2026-09-06T11:00:00Z",
        "expires_at": "2026-09-20T11:00:00Z"}
    submission = {"version": 1, "source_database": "taxportal",
        "target_database": "ross_house_production", "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False, "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"], "provenance": provenance,
        "collections": [{"name": n, "root_ids": ["private-synthetic-" + n],
                         "ownership_basis": "Exact Ross House Rentals account review"}
                        for n in ["app_users", "tenants"]]}
    roots = build_root_evidence_package(inventory, contract, submission, now=NOW)
    relation = prepare_relationship_submission(inventory, contract, roots, now=NOW)
    relation["provenance"] = {**provenance, "prepared_at": "2026-09-07T10:00:00Z",
                              "approved_at": "2026-09-07T11:00:00Z", "expires_at": "2026-09-15T11:00:00Z"}
    for row in relation["collections"]:
        row.update(root_collection="app_users", relationship_paths=["owner.user_id"],
                   exact_root_ids=["private-synthetic-app_users"])
    base, _ = build_relationship_evidence_package(inventory, contract, roots, relation, now=NOW)
    return inventory, contract, base


def filled(inputs, names=None):
    submission = builder.prepare_complete_submission(*inputs, now=NOW)
    submission["provenance"] = {"prepared_by": "combined-preparer", "approved_by": "combined-approver",
        "prepared_at": "2026-09-07T11:10:00Z", "approved_at": "2026-09-07T11:30:00Z",
        "expires_at": "2026-09-14T11:00:00Z"}
    if names is not None:
        submission["collections"] = [row for row in submission["collections"] if row["name"] in names]
    for row in submission["collections"]:
        row["evidence"] = ({"field_paths": ["application.source"], "allowed_values": ["ross_house_rentals"]}
            if row["requirement"] == "source_discriminator" else {
                "field_paths": ["owner.user_id"], "ownership_basis": "Exact Ross House Rentals schema review",
                "reviewer": "schema-reviewer", "reviewed_at": "2026-09-07T11:20:00Z"})
    return submission


def test_template_and_report_cover_all_four_requirements(inputs):
    template = builder.prepare_complete_submission(*inputs, now=NOW)
    assert len(template["collections"]) == 50
    report = builder.evidence_coverage_report(*inputs, now=NOW)
    assert report["contract_collections_total"] == 82
    assert report["contract_collections_validated"] == 32
    assert report["requirements"]["source_discriminator"]["total"] == 37
    assert report["requirements"]["manual_schema_review"]["total"] == 13
    assert report["outside_contract_ownership_review_required"] is True
    assert "private-synthetic" not in json.dumps((template, report))
    with pytest.raises(ValueError): builder.build_complete_evidence_package(*inputs, template, now=NOW)


@pytest.mark.parametrize("name", SUPPLEMENTAL_NAMES)
def test_each_supplemental_collection_can_be_completed_independently(inputs, name):
    submission = filled(inputs, [name])
    before = copy.deepcopy((inputs, submission))
    package, report = builder.build_complete_evidence_package(*inputs, submission, now=NOW)
    assert report["contract_collections_validated"] == 33
    assert report["contract_evidence_complete"] is False
    assert (inputs, submission) == before
    assert len(package["collections"]) == 33
    assert "private-synthetic" not in json.dumps(report)
    with pytest.raises(ValueError, match="incomplete_contract"):
        builder.build_complete_evidence_package(*inputs, submission, now=NOW, require_complete=True)


def test_complete_package_is_deterministic_but_never_authorizes_migration(inputs):
    submission = filled(inputs)
    package, report = builder.build_complete_evidence_package(*inputs, submission, now=NOW, require_complete=True)
    assert len(package["collections"]) == 82
    assert report["contract_evidence_complete"] is True
    assert all(not g["pending_collections"] for g in report["requirements"].values())
    assert package["migration_authorized"] is report["migration_authorized"] is False
    assert report["inventory_outside_contract_count"] == 1
    submission["collections"].reverse()
    assert builder.build_complete_evidence_package(*inputs, submission, now=NOW) == (package, report)


def test_batches_preserve_existing_manual_reviews_with_new_combined_review(inputs):
    first, _ = builder.build_complete_evidence_package(*inputs, filled(inputs, ["users"]), now=NOW)
    next_inputs = (inputs[0], inputs[1], first)
    submission = filled(next_inputs)
    submission["provenance"].update(prepared_at="2026-09-07T11:35:00Z", approved_at="2026-09-07T11:45:00Z",
                                    approved_by="next-approver", expires_at="2026-09-13T11:00:00Z")
    assert "users" not in [r["name"] for r in submission["collections"]]
    package, report = builder.build_complete_evidence_package(*next_inputs, submission, now=NOW, require_complete=True)
    assert report["contract_collections_validated"] == 82
    assert next(r for r in package["collections"] if r["name"] == "users") == next(r for r in first["collections"] if r["name"] == "users")


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "wrong_requirement", "replace_root", "query",
    "foreign_marker", "generic_marker", "source_operator", "manual_operator", "future_review", "stale_review",
    "invalid_reviewer", "self_approval", "base_hash", "base_id", "inventory_hash", "contract_hash",
    "extend_validity", "before_base", "numeric_authorization", "boolean_version", "empty"])
def test_invalid_extensions_fail_closed(inputs, mutation):
    submission = filled(inputs, ["email_events", "users"])
    source, manual = submission["collections"]
    if mutation == "unknown": source["name"] = "loans"
    elif mutation == "duplicate": submission["collections"].append(copy.deepcopy(source))
    elif mutation == "wrong_requirement": source["requirement"] = "manual_schema_review"
    elif mutation == "replace_root": source["name"] = "app_users"
    elif mutation == "query": source["evidence"]["query"] = {"$where": "unused"}
    elif mutation == "foreign_marker": source["evidence"]["allowed_values"] = ["ross_house_rentals", "ross_tax"]
    elif mutation == "generic_marker": source["evidence"]["allowed_values"] = ["main"]
    elif mutation == "source_operator": source["evidence"]["field_paths"] = ["source.$ne"]
    elif mutation == "manual_operator": manual["evidence"]["field_paths"] = ["owner[0]"]
    elif mutation == "future_review": manual["evidence"]["reviewed_at"] = "2026-09-08T00:00:00Z"
    elif mutation == "stale_review": manual["evidence"]["reviewed_at"] = "2025-01-01T00:00:00Z"
    elif mutation == "invalid_reviewer": manual["evidence"]["reviewer"] = "reviewer\u200b"
    elif mutation == "self_approval": submission["provenance"]["approved_by"] = "COMBINED-PREPARER"
    elif mutation == "base_hash": submission["base_package_sha256"] = "c" * 64
    elif mutation == "base_id": submission["base_evidence_id"] = "wrong"
    elif mutation == "inventory_hash": submission["inventory_sha256"] = "c" * 64
    elif mutation == "contract_hash": submission["contract_sha256"] = "c" * 64
    elif mutation == "extend_validity": submission["provenance"]["expires_at"] = "2026-09-16T11:00:00Z"
    elif mutation == "before_base": submission["provenance"]["prepared_at"] = "2026-09-06T11:00:00Z"
    elif mutation == "numeric_authorization": submission["migration_authorized"] = 0
    elif mutation == "boolean_version": submission["version"] = True
    elif mutation == "empty": submission["collections"] = []
    before = copy.deepcopy((inputs, submission))
    with pytest.raises(ValueError): builder.build_complete_evidence_package(*inputs, submission, now=NOW)
    assert (inputs, submission) == before


def test_report_revalidates_hashes_expiration_and_inventory_scope(inputs):
    package, _ = builder.build_complete_evidence_package(*inputs, filled(inputs), now=NOW)
    changed = copy.deepcopy(package)
    changed["collections"][0]["evidence"]["field_paths"] = ["tampered"]
    with pytest.raises(ValueError): builder.evidence_coverage_report(inputs[0], inputs[1], changed, now=NOW)
    with pytest.raises(ValueError, match="expired"):
        builder.evidence_coverage_report(inputs[0], inputs[1], package, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    inventory = copy.deepcopy(inputs[0]); inventory["collections"].pop(0)
    with pytest.raises(ValueError, match="missing_contract"):
        builder.evidence_coverage_report(inventory, inputs[1], package, now=NOW)


def test_cli_prepare_build_report_and_failed_publication(inputs, tmp_path, monkeypatch, capsys):
    class Clock:
        @staticmethod
        def now(tz): return NOW
    monkeypatch.setattr(builder, "datetime", Clock)
    base = tmp_path / "base.json"; base.write_text(json.dumps(inputs[2]))
    inventory = tmp_path / "inventory.json"
    template = tmp_path / "template.json"
    monkeypatch.setattr("sys.argv", ["builder", "prepare", str(inventory), str(base), "--output", str(template)])
    assert builder.main() == 0
    assert len(json.loads(template.read_text())["collections"]) == 50
    assert "private-synthetic" not in capsys.readouterr().out
    submission = tmp_path / "submission.json"; submission.write_text(json.dumps(filled(inputs)))
    output = tmp_path / "combined.json"
    args = ["builder", "build", str(inventory), str(base), "--submission", str(submission),
            "--output", str(output), "--require-complete"]
    monkeypatch.setattr("sys.argv", args)
    assert builder.main() == 0
    assert json.loads(capsys.readouterr().out)["contract_evidence_complete"] is True
    assert output.stat().st_mode & 0o777 == 0o600
    original = output.read_bytes()
    with pytest.raises(FileExistsError): builder.main()
    assert output.read_bytes() == original
    assert capsys.readouterr().out == ""
    monkeypatch.setattr("sys.argv", ["builder", "report", str(inventory), str(output)])
    assert builder.main() == 0
    report_text = capsys.readouterr().out
    assert json.loads(report_text)["contract_collections_validated"] == 82
    assert "private-synthetic" not in report_text and "schema-reviewer" not in report_text
    bad = filled(inputs); bad["collections"][0]["evidence"]["query"] = {}
    submission.write_text(json.dumps(bad))
    failed = tmp_path / "failed.json"; args[args.index(str(output))] = str(failed)
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(ValueError): builder.main()
    assert not failed.exists() and capsys.readouterr().out == ""
