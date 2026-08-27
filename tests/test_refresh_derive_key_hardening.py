"""Security regression tests for refresh-token derivation key handling."""

import base64
import hashlib
import hmac

import pytest

from rental.refresh_tokens import (
    RefreshConfigurationError,
    next_refresh_token,
)


def test_missing_all_secrets_fails_closed(monkeypatch):
    monkeypatch.delenv("REFRESH_DERIVE_KEY", raising=False)
    monkeypatch.delenv("TENANT_JWT_SECRET", raising=False)

    with pytest.raises(RefreshConfigurationError):
        next_refresh_token("presented-refresh-token")


def test_blank_secrets_also_fail_closed(monkeypatch):
    monkeypatch.setenv("REFRESH_DERIVE_KEY", "   ")
    monkeypatch.setenv("TENANT_JWT_SECRET", "")

    with pytest.raises(RefreshConfigurationError):
        next_refresh_token("presented-refresh-token")


def test_dedicated_key_has_priority(monkeypatch):
    monkeypatch.setenv("REFRESH_DERIVE_KEY", "dedicated-key-A")
    monkeypatch.setenv("TENANT_JWT_SECRET", "jwt-secret-A")
    from_dedicated = next_refresh_token("token-1")

    monkeypatch.setenv("TENANT_JWT_SECRET", "jwt-secret-B")
    assert next_refresh_token("token-1") == from_dedicated


def test_jwt_secret_is_valid_compatibility_fallback(monkeypatch):
    monkeypatch.delenv("REFRESH_DERIVE_KEY", raising=False)
    monkeypatch.setenv("TENANT_JWT_SECRET", "jwt-secret-A")

    first = next_refresh_token("token-1")
    second = next_refresh_token("token-1")

    assert first == second
    assert first != "token-1"


def test_jwt_fallback_matches_legacy_formula_byte_for_byte(monkeypatch):
    """Protect existing live chains when only TENANT_JWT_SECRET is configured."""
    raw = "legacy-presented-token"
    jwt_secret = "legacy-compatible-jwt-secret"
    monkeypatch.delenv("REFRESH_DERIVE_KEY", raising=False)
    monkeypatch.setenv("TENANT_JWT_SECRET", jwt_secret)

    legacy_key = hashlib.sha256((jwt_secret + ":refresh-derive-v1").encode()).digest()
    expected_digest = hmac.new(legacy_key, raw.encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode()

    assert next_refresh_token(raw) == expected


def test_different_keys_produce_different_next_tokens(monkeypatch):
    monkeypatch.setenv("REFRESH_DERIVE_KEY", "dedicated-key-A")
    monkeypatch.delenv("TENANT_JWT_SECRET", raising=False)
    first = next_refresh_token("token-1")

    monkeypatch.setenv("REFRESH_DERIVE_KEY", "dedicated-key-B")
    second = next_refresh_token("token-1")

    assert first != second
