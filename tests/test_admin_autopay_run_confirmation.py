"""Exercise the real HTTP boundary without a database or payment provider."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rental import autopay_cron, security
from rental.stripe_pkg import autopay_router


@pytest.fixture
def endpoint(monkeypatch):
    events = []
    stats = {"charged": 2, "skipped": 3, "failed": 1, "configs_checked": 6}
    db = object()

    async def run_once(database):
        assert database is db
        events.append("run")
        return stats

    async def audit(**kwargs):
        events.append(kwargs["action"])

    auth = AsyncMock(return_value={"_id": "admin-123"})
    runner = AsyncMock(side_effect=run_once)
    audit_log = AsyncMock(side_effect=audit)
    database = Mock(return_value=db)
    monkeypatch.setattr(autopay_router, "auth_admin", auth)
    monkeypatch.setattr(autopay_router, "get_db", database)
    monkeypatch.setattr(autopay_cron, "run_once", runner)
    monkeypatch.setattr(security, "audit_log", audit_log)
    app = FastAPI()
    app.include_router(autopay_router.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, auth=auth, runner=runner,
                              audit=audit_log, database=database, events=events,
                              stats=stats, db=db)


@pytest.mark.parametrize("body", [
    b"", b"{", b"\xff", b"null", b"[]", b"true", b"123",
    b'"RUN_AUTOPAY_NOW"', b"{}", b'{"confirmation": null}',
    b'{"confirmation": true}', b'{"confirmation": ["RUN_AUTOPAY_NOW"]}',
    b'{"confirmation": "run_autopay_now"}',
    b'{"confirmation": " RUN_AUTOPAY_NOW "}',
])
def test_invalid_confirmation_never_starts_a_run(endpoint, body):
    response = endpoint.client.post("/admin/autopay/run-now", content=body,
                                    headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)
    endpoint.auth.assert_awaited_once()
    endpoint.runner.assert_not_awaited()
    endpoint.audit.assert_not_awaited()
    endpoint.database.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("body", [b"{", b'{"confirmation":"RUN_AUTOPAY_NOW"}'])
def test_authentication_precedes_confirmation_parsing(endpoint, status, body):
    endpoint.auth.side_effect = HTTPException(status_code=status, detail="Unauthorized")
    response = endpoint.client.post("/admin/autopay/run-now", content=body)
    assert response.status_code == status
    endpoint.runner.assert_not_awaited()
    endpoint.audit.assert_not_awaited()
    endpoint.database.assert_not_called()


@pytest.mark.parametrize("identity", [{"_id": "admin-123"}, {"id": "admin-123"}])
def test_confirmed_run_calls_canonical_runner_once_and_audits_actor(endpoint, identity):
    endpoint.auth.return_value = identity
    response = endpoint.client.post("/admin/autopay/run-now",
                                    json={"confirmation": "RUN_AUTOPAY_NOW"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["stats"] == endpoint.stats
    endpoint.runner.assert_awaited_once_with(endpoint.db)
    assert endpoint.events == ["autopay_manual_run_requested", "run",
                               "autopay_manual_run_completed"]
    for call in endpoint.audit.await_args_list:
        assert call.kwargs["admin_user_id"] == "admin-123"
        assert call.kwargs["resource_type"] == "autopay"
        assert call.kwargs["resource_id"] == "run-now"
    assert endpoint.audit.await_args_list[-1].kwargs["metadata"] == {
        "charged": 2, "skipped": 3, "failed": 1,
    }


def test_runner_failure_is_audited_without_exposing_internal_error(endpoint):
    endpoint.runner.side_effect = RuntimeError("provider-private-diagnostic")
    response = endpoint.client.post("/admin/autopay/run-now",
                                    json={"confirmation": "RUN_AUTOPAY_NOW"})
    assert response.status_code == 500
    assert "provider-private-diagnostic" not in response.text
    assert "Revisa el historial" in response.json()["detail"]
    endpoint.runner.assert_awaited_once_with(endpoint.db)
    assert endpoint.events == ["autopay_manual_run_requested", "autopay_manual_run_failed"]
    failed = endpoint.audit.await_args_list[-1].kwargs
    assert failed["result"] == "error"
    assert failed["admin_user_id"] == "admin-123"
    assert "metadata" not in failed
