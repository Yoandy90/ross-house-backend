import pytest

from scripts import production_readiness_inspection as inspection


def _health():
    return {
        "status": "ok",
        "database": "connected",
        "database_name": "ross_house_production",
    }


def test_deploy_scope_uses_only_health_and_readiness_gets(monkeypatch):
    calls = []

    def fake_get(path, *, auth):
        calls.append((path, auth))
        if path == "/api/health":
            return _health()
        return {"success": True, "safe_to_deploy": True}

    monkeypatch.setattr(inspection, "BASE_URL", "https://api.rosshouserentals.com")
    monkeypatch.setattr(inspection, "TOKEN", "opaque-token")
    monkeypatch.setattr(inspection, "SCOPE", "deploy")
    monkeypatch.setattr(inspection, "get_json", fake_get)

    assert inspection.main() == 0
    assert calls == [
        ("/api/health", False),
        ("/api/admin/operations/production-readiness", True),
    ]


def test_staging_hostname_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(
        inspection, "BASE_URL", "https://ross-house-staging.up.railway.app"
    )
    monkeypatch.setattr(inspection, "TOKEN", "opaque-token")

    with pytest.raises(RuntimeError, match="production_url_not_fail_closed"):
        inspection.validate_target()


def test_staging_database_is_rejected(monkeypatch):
    monkeypatch.setattr(inspection, "BASE_URL", "https://api.example.com")
    monkeypatch.setattr(inspection, "TOKEN", "opaque-token")
    monkeypatch.setattr(inspection, "get_json", lambda path, auth: {
        "status": "ok",
        "database": "connected",
        "database_name": "ross_house_staging",
    })

    with pytest.raises(RuntimeError, match="production_health_or_database_invalid"):
        inspection.validate_target()


def test_delivery_scope_surfaces_only_issue_codes(monkeypatch):
    def fake_get(path, *, auth):
        if path == "/api/health":
            return _health()
        return {
            "success": True,
            "ready_to_enable_inspection_delivery": False,
            "inspection_delivery_blocking_issues": [
                "inspection_worker_is_enabled",
                "sendgrid_api_key_is_present",
            ],
        }

    monkeypatch.setattr(inspection, "BASE_URL", "https://api.example.com")
    monkeypatch.setattr(inspection, "TOKEN", "opaque-token")
    monkeypatch.setattr(inspection, "SCOPE", "inspection-delivery")
    monkeypatch.setattr(inspection, "get_json", fake_get)

    with pytest.raises(RuntimeError, match="ready_to_enable_inspection_delivery_false"):
        inspection.main()
