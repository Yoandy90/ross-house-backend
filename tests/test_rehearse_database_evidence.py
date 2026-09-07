import json
import socket

import pytest

from scripts import rehearse_database_evidence as rehearsal
from scripts.plan_database_isolation import apply_offline_filter_evidence


def test_rehearsal_is_deterministic_offline_and_reports_all_stages(monkeypatch):
    def network_forbidden(*args, **kwargs):
        raise AssertionError("rehearsal must not use the network")
    monkeypatch.setattr(socket, "socket", network_forbidden)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    first = rehearsal.run_rehearsal()
    assert first == rehearsal.run_rehearsal()
    assert first["status"] == "passed"
    assert first["checks_passed"] == first["checks_total"] == 13
    assert [stage["synthetic_collections_validated"] for stage in first["stages"]] == [2, 32, 33, 82]
    assert first["synthetic_only"] is True
    assert first["real_evidence_validated"] is False
    assert first["migration_authorized"] is False
    assert first["production_readiness_assessed"] is False
    rendered = json.dumps(first)
    for private in ("synthetic-only-app_users", "synthetic-only-tenants", "synthetic-preparer",
                    "synthetic-approver", "synthetic-schema-reviewer", "evd-", "ownership_basis"):
        assert private not in rendered
    with pytest.raises(ValueError, match="package_fields_invalid"):
        apply_offline_filter_evidence([], first, "a" * 64, "b" * 64)


def test_rehearsal_detects_a_validator_accepting_foreign_ids(monkeypatch):
    original = rehearsal.build_relationship_evidence_package
    def broken_validator(inventory, contract, roots, submission, **kwargs):
        if submission["collections"][0]["exact_root_ids"] == ["synthetic-only-unrelated-001"]:
            return None
        return original(inventory, contract, roots, submission, **kwargs)
    monkeypatch.setattr(rehearsal, "build_relationship_evidence_package", broken_validator)
    report = rehearsal.run_rehearsal()
    assert report["status"] == "failed"
    assert report["checks_passed"] == 12
    assert [c["name"] for c in report["checks"] if not c["passed"]] == ["reject_foreign_root_id"]


def test_unrelated_validation_errors_do_not_count_as_expected_rejections():
    def wrong_error():
        raise ValueError("unrelated_failure")
    assert rehearsal._rejection_check("test", "expected_failure", wrong_error)["passed"] is False
    def unexpected_error():
        raise RuntimeError("programming failure")
    with pytest.raises(RuntimeError):
        rehearsal._rejection_check("test", "expected_failure", unexpected_error)


def test_cli_saves_only_metadata_privately_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    output = tmp_path / "rehearsal-report.json"
    monkeypatch.setattr("sys.argv", ["rehearsal", "--output", str(output)])
    assert rehearsal.main() == 0
    assert json.loads(output.read_text()) == json.loads(capsys.readouterr().out)
    assert output.stat().st_mode & 0o777 == 0o600
    original = output.read_bytes()
    with pytest.raises(FileExistsError): rehearsal.main()
    assert output.read_bytes() == original
    assert capsys.readouterr().out == ""
    assert list(tmp_path.iterdir()) == [output]


def test_cli_returns_nonzero_on_failed_safety_check(monkeypatch, capsys):
    original = rehearsal.build_relationship_evidence_package
    def broken_validator(inventory, contract, roots, submission, **kwargs):
        if submission["collections"][0]["exact_root_ids"] == ["synthetic-only-unrelated-001"]:
            return None
        return original(inventory, contract, roots, submission, **kwargs)
    monkeypatch.setattr(rehearsal, "build_relationship_evidence_package", broken_validator)
    monkeypatch.setattr("sys.argv", ["rehearsal"])
    assert rehearsal.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


@pytest.mark.parametrize("flag", ["--inventory", "--package", "--mongo-url", "--execute"])
def test_cli_has_no_real_data_or_execution_inputs(flag, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["rehearsal", flag, "unused"])
    with pytest.raises(SystemExit) as caught: rehearsal.main()
    assert caught.value.code == 2
    assert capsys.readouterr().out == ""
