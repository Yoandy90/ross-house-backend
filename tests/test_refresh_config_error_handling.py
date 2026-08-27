"""Regression tests for safe HTTP handling of refresh derivation misconfiguration."""

import pytest
from fastapi import HTTPException

import rental.refresh_router as rr


def test_missing_derivation_secrets_maps_to_generic_503(monkeypatch):
    monkeypatch.delenv("REFRESH_DERIVE_KEY", raising=False)
    monkeypatch.delenv("TENANT_JWT_SECRET", raising=False)

    with pytest.raises(HTTPException) as exc:
        rr._next_refresh_or_503("presented-refresh-token")

    assert exc.value.status_code == 503
    assert exc.value.detail == "Servicio temporalmente no disponible"
    # The response must not expose configuration names or token material.
    assert "REFRESH_DERIVE_KEY" not in str(exc.value.detail)
    assert "TENANT_JWT_SECRET" not in str(exc.value.detail)
    assert "presented-refresh-token" not in str(exc.value.detail)


def test_configured_derivation_still_returns_deterministic_next_token(monkeypatch):
    monkeypatch.setenv("REFRESH_DERIVE_KEY", "test-dedicated-key")
    monkeypatch.delenv("TENANT_JWT_SECRET", raising=False)

    first = rr._next_refresh_or_503("token-1")
    second = rr._next_refresh_or_503("token-1")

    assert first == second
    assert first != "token-1"


def test_unexpected_errors_are_not_hidden(monkeypatch):
    def boom(_raw):
        raise ValueError("unexpected bug")

    monkeypatch.setattr(rr, "next_refresh_token", boom)

    with pytest.raises(ValueError, match="unexpected bug"):
        rr._next_refresh_or_503("token-1")
