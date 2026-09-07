import copy
from datetime import timedelta
import json
import sys

import pytest

from test_build_complete_evidence_package import inputs, filled, NOW
from scripts import review_database_evidence as review
from scripts.build_complete_evidence_package import build_complete_evidence_package
from scripts.plan_database_isolation import (
    _expected_evidence_id, apply_offline_filter_evidence, load_inventory,
)


def complete(inputs):
    return build_complete_evidence_package(*inputs, filled(inputs), now=NOW)[0]


def test_partial_and_outside_contract_are_distinct_blockers(inputs):
    before = copy.deepcopy(inputs)
    report = review.review_evidence(*inputs, now=NOW)
    assert report["review_blockers"] == ["contract_evidence_incomplete", "outside_contract_ownership_review_required"]
    assert report["contract_collections_pending"] == 50
    assert report["inventory_outside_contract_count"] == 1
    assert inputs == before


def test_complete_contract_does_not_clear_outside_review(inputs):
    report = review.review_evidence(inputs[0], inputs[1], complete(inputs), now=NOW)
    assert report["contract_collections_validated"] == 82
    assert report["review_blockers"] == ["outside_contract_ownership_review_required"]


def test_counts_only_projection_omits_private_values(inputs):
    report = review.review_evidence(inputs[0], inputs[1], complete(inputs), now=NOW)
    rendered = json.dumps(report)
    for value in ["private-synthetic", "app_users", "tenants", "synthetic_outside_contract",
                  "combined-preparer", "combined-approver", "schema-reviewer", "owner.user_id",
                  "Exact Ross House Rentals", "application.source", "evd-"]:
        assert value not in rendered
    assert all(set(group) == {"total", "validated", "pending"} for group in report["requirements"].values())


def test_all_offline_checks_pass_without_migration_authorization(inputs, tmp_path):
    inventory, contract, package = copy.deepcopy(inputs)
    inventory["collections"] = [r for r in inventory["collections"] if r["name"] != "synthetic_outside_contract"]
    inventory["collection_count"] -= 1
    inventory.pop("_inventory_sha256")
    path = tmp_path / "contract-only-inventory.json"
    path.write_text(json.dumps(inventory))
    inventory = load_inventory(path)
    # Rebind this synthetic fixture to the actual bytes before building evidence.
    package["inventory_sha256"] = inventory["_inventory_sha256"]
    package["provenance"]["evidence_id"] = _expected_evidence_id(
        inventory["_inventory_sha256"], contract["_contract_sha256"],
        package["collections_sha256"], package["provenance"])
    synthetic = inventory, contract, package
    report = review.review_evidence(inventory, contract, complete(synthetic), now=NOW)
    assert report["status"] == "offline_contract_checks_passed"
    assert report["review_blockers"] == []
    assert report["migration_authorized"] is False
    assert report["production_readiness_assessed"] is False
    assert report["ownership_independently_verified"] is False


@pytest.mark.parametrize("seconds,blocked", [(86401, False), (86400, True), (1, True)])
def test_expiry_warning_boundary(inputs, seconds, blocked):
    package = complete(inputs)
    expires = review._utc_timestamp(package["provenance"]["expires_at"], "expires_at")
    report = review.review_evidence(inputs[0], inputs[1], package, now=expires-timedelta(seconds=seconds))
    assert ("evidence_expires_within_24_hours" in report["review_blockers"]) is blocked
    assert report["remaining_validity_seconds"] == seconds


@pytest.mark.parametrize("tamper", ["expiry", "hash", "scope", "approval", "naive_clock"])
def test_invalid_evidence_never_produces_review(inputs, tamper):
    package = complete(inputs)
    now = NOW
    if tamper == "expiry":
        now = review._utc_timestamp(package["provenance"]["expires_at"], "expires_at")
    elif tamper == "hash":
        package["inventory_sha256"] = "f" * 64
    elif tamper == "scope":
        package["target_database"] = "other-company"
    elif tamper == "approval":
        package["migration_authorized"] = True
    else:
        now = now.replace(tzinfo=None)
    with pytest.raises(ValueError):
        review.review_evidence(inputs[0], inputs[1], package, now=now)


@pytest.mark.parametrize("field,value", [("version", 2.0), ("migration_authorized", 0)])
def test_shared_validator_rejects_numeric_coercion(inputs, field, value):
    inventory, contract, package = copy.deepcopy(inputs)
    package[field] = value
    with pytest.raises(ValueError, match=f"filter_evidence_{field}_invalid"):
        apply_offline_filter_evidence([], package, inventory["_inventory_sha256"], contract["_contract_sha256"], now=NOW)


def test_cli_invalid_input_has_no_output_or_private_errors(tmp_path, monkeypatch, capsys):
    inventory = tmp_path / "inventory.json"
    inventory.write_text('{"private-key":true,"private-key":false}')
    output = tmp_path / "review.json"
    monkeypatch.setattr(sys, "argv", ["review", str(inventory), "private-package-path", "--output", str(output)])
    assert review.main() == 1
    assert not output.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "offline_evidence_review_failed\n"


@pytest.mark.parametrize("blocked", [False, True])
def test_cli_report_exit_and_private_publication(tmp_path, monkeypatch, capsys, blocked):
    # Isolate CLI routing; full evidence validation is exercised above.
    report = {"review_blockers": ["contract_evidence_incomplete"] if blocked else []}
    monkeypatch.setattr(review, "load_inventory", lambda path: {})
    monkeypatch.setattr(review, "load_filter_contract", lambda path: {})
    monkeypatch.setattr(review, "load_offline_json", lambda path: {})
    monkeypatch.setattr(review, "review_evidence", lambda *args: report)
    output = tmp_path / "review.json"
    monkeypatch.setattr(sys, "argv", ["review", "inventory", "package", "--output", str(output)])
    assert review.main() == (2 if blocked else 0)
    assert json.loads(output.read_text()) == report
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(capsys.readouterr().out) == report
    assert review.main() == 1
    assert output.read_text() == json.dumps(report, indent=2, sort_keys=True) + "\n"
