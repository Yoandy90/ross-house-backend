"""Regression tests for safe HTTP handling of refresh derivation misconfiguration."""

import pytest
from fastapi import HTTPException

import rental.refresh_router as rr


@pytest.mark.asyncio
async def test_missing_derivation_secrets_maps_to_generic_503_and_counts_metric(monkeypatch):
    monkeypatch.delenv("REFRESH_DERIVE_KEY", raising=False)
    monkeypatch.delenv("TENANT_JWT_SECRET", raising=False)
    seen = []

    async def fake_bump(metric):
        seen.append(metric)

    monkeypatch.setattr(rr, "bump", fake_bump)

    with pytest.raises(HTTPException) as exc:
        await rr._next_refresh_or_503("presented-refresh-token")

    assert exc.value.status_code == 503
    assert exc.value.detail == "Servicio temporalmente no disponible"
    assert seen == ["refresh_config_error"]
    assert "REFRESH_DERIVE_KEY" not in str(exc.value.detail)
    assert "TENANT_JWT_SECRET" not in str(exc.value.detail)
    assert "presented-refresh-token" not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_configured_derivation_still_returns_deterministic_next_token(monkeypatch):
    monkeypatch.setenv("REFRESH_DERIVE_KEY", "test-dedicated-key")
    monkeypatch.delenv("TENANT_JWT_SECRET", raising=False)
    seen = []

    async def fake_bump(metric):
        seen.append(metric)

    monkeypatch.setattr(rr, "bump", fake_bump)

    first = await rr._next_refresh_or_503("token-1")
    second = await rr._next_refresh_or_503("token-1")

    assert first == second
    assert first != "token-1"
    assert seen == []


@pytest.mark.asyncio
async def test_unexpected_errors_are_not_hidden_or_counted(monkeypatch):
    seen = []

    def boom(_raw):
        raise ValueError("unexpected bug")

    async def fake_bump(metric):
        seen.append(metric)

    monkeypatch.setattr(rr, "next_refresh_token", boom)
    monkeypatch.setattr(rr, "bump", fake_bump)

    with pytest.raises(ValueError, match="unexpected bug"):
        await rr._next_refresh_or_503("token-1")

    assert seen == []
