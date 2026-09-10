"""Security and response-contract tests for Helcim save-session polling."""
import asyncio

import pytest

from rental import helcim_vault_router as vault


class _Sessions:
    def __init__(self, document):
        self.document = document
        self.query = None

    async def find_one(self, query):
        self.query = query
        if not self.document:
            return None
        if any(self.document.get(key) != value for key, value in query.items()):
            return None
        return self.document


class _Database:
    def __init__(self, document):
        self.helcim_checkout_sessions = _Sessions(document)


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch, document):
    database = _Database(document)

    async def authenticated_tenant(_request):
        return {"_id": "tenant-123"}

    monkeypatch.setattr(vault, "auth_tenant_flex", authenticated_tenant)
    monkeypatch.setattr(vault, "get_db", lambda: database)
    return database


def test_verified_status_is_tenant_scoped_and_exposes_no_vault_secrets(monkeypatch):
    database = _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
        "status": "verified",
        "checkout_token": "checkout-secret",
        "secret_token": "hash-secret",
        "helcim_transaction": {"cardToken": "card-secret"},
    })

    result = _run(vault.save_method_session_status("session-abc", object()))

    assert database.helcim_checkout_sessions.query == {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
    }
    assert result == {
        "success": True,
        "session_id": "session-abc",
        "status": "verified",
        "method_saved": True,
    }
    assert "secret" not in repr(result).lower()
    assert "token" not in repr(result).lower()


@pytest.mark.parametrize("status", [None, "pending"])
def test_unfinished_session_does_not_claim_method_saved(monkeypatch, status):
    _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
        "status": status,
    })

    result = _run(vault.save_method_session_status("session-abc", object()))

    assert result["status"] == "pending"
    assert result["method_saved"] is False


def test_other_tenant_or_non_verify_session_is_not_disclosed(monkeypatch):
    _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-other",
        "purpose": "payment",
        "status": "verified",
    })

    with pytest.raises(vault.HTTPException) as exc:
        _run(vault.save_method_session_status("session-abc", object()))

    assert exc.value.status_code == 404


def test_unknown_internal_state_fails_closed(monkeypatch):
    _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
        "status": "unexpected-provider-state",
    })

    result = _run(vault.save_method_session_status("session-abc", object()))

    assert result["status"] == "failed"
    assert result["method_saved"] is False
