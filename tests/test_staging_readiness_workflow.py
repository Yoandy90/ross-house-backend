from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1]
    / ".github"
    / "workflows"
    / "staging-readiness-inspection.yml"
).read_text(encoding="utf-8")


def test_readiness_gate_is_manual_and_main_only():
    assert "workflow_dispatch:" in WORKFLOW
    assert "pull_request:" not in WORKFLOW
    assert "\n  push:" not in WORKFLOW
    assert "github.ref == 'refs/heads/main'" in WORKFLOW


def test_readiness_gate_has_read_only_permissions_and_no_credentials():
    assert "permissions:\n  contents: read" in WORKFLOW
    assert "${{ secrets." not in WORKFLOW
    assert "RAILWAY_TOKEN" not in WORKFLOW
    assert "MONGO_URL" not in WORKFLOW


def test_readiness_gate_builds_fixed_identity_names_only_snapshot():
    for expected in (
        '"project_name": "Ross House"',
        '"environment_name": "staging"',
        '"service_name": "ross_house_staging"',
        '"source_repo": "Yoandy90/ross-house-backend"',
        '"source_branch": "main"',
        '"values_redacted": True',
    ):
        assert expected in WORKFLOW
    assert '"variables":' not in WORKFLOW
    assert "validate_staging_snapshot.py" in WORKFLOW
    assert '--expected-commit "${{ github.sha }}"' in WORKFLOW
