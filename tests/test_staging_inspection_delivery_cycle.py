from pathlib import Path


def test_staging_inspection_fixture_is_fail_closed_and_synthetic():
    source = Path("rental/staging_inspection_fixture_router.py").read_text()
    assert "assert_staging_fixture_allowed" in source
    assert '"staging_fixture_marker": value' in source
    assert '"synthetic": True' in source
    assert "@invalid.example" in source
    assert '"failure_code": "provider_not_configured"' in source
    assert "await _delete_exact(db, value)" in source


def test_staging_cycle_validates_target_and_always_cleans_up():
    source = Path("scripts/staging_inspection_delivery_cycle.py").read_text()
    assert '"staging" not in (parsed.hostname or "").lower()' in source
    assert '"staging" not in str(health.get("database_name", "")).lower()' in source
    assert "finally:" in source
    assert 'request("DELETE", fixture_path, confirmation)' in source
    assert '"provider_contacted": False' in source
    assert "/send-email" not in source
    assert "/process-next" not in source


def test_staging_workflow_uses_environment_scoped_values():
    source = Path(".github/workflows/staging-inspection-delivery-cycle.yml").read_text()
    assert "environment: staging" in source
    assert "vars.STAGING_BASE_URL" in source
    assert "secrets.STAGING_ADMIN_TOKEN" in source
    assert "workflow_dispatch" in source
