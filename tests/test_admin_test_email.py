import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rental import admin_2fa_router as twofa
from rental import security


class FakeRequest:
    client = SimpleNamespace(host="127.0.0.1")
    headers = {"user-agent": "pytest"}


def _admin(email="info@rosshouserentals.com"):
    return {"_id": "admin-1", "email": email, "name": "Ross House Admin", "role": "admin"}


def test_route_requires_admin_dependency():
    route = next(route for route in twofa.router.routes if route.path == "/admin/auth/test-email")
    assert any(dep.call is twofa.auth_admin for dep in route.dependant.dependencies)


def test_test_email_uses_authenticated_admin_recipient_and_returns_only_masked_address(monkeypatch):
    delivered = {}
    limits = []
    audits = []

    async def fake_send(to_email, name):
        delivered.update(email=to_email, name=name)
        return True

    async def fake_limit(scope, key, max_requests, window_seconds):
        limits.append((scope, key, max_requests, window_seconds))

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(twofa, "_send_test_email", fake_send)
    monkeypatch.setattr(security, "check_rate_limit_persistent", fake_limit)
    monkeypatch.setattr(security, "client_ip_hash", lambda request: "hashed-ip")
    monkeypatch.setattr(security, "audit_log", fake_audit)

    result = asyncio.run(twofa.admin_test_email(FakeRequest(), admin=_admin()))

    assert result == {
        "success": True,
        "recipient": "in***@rosshouserentals.com",
        "provider": "sendgrid",
    }
    assert delivered == {
        "email": "info@rosshouserentals.com",
        "name": "Ross House Admin",
    }
    assert [item[0] for item in limits] == ["admin-test-email-user", "admin-test-email-ip"]
    assert all(item[2:] == (3, 3600) or item[2:] == (10, 3600) for item in limits)
    assert audits[0]["action"] == "admin_test_email"
    assert audits[0]["result"] == "success"


def test_test_email_rejects_admin_without_email(monkeypatch):
    async def must_not_send(*args, **kwargs):
        raise AssertionError("delivery must not be attempted")

    monkeypatch.setattr(twofa, "_send_test_email", must_not_send)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(twofa.admin_test_email(FakeRequest(), admin=_admin("")))

    assert exc.value.status_code == 400


def test_test_email_reports_provider_failure_without_exposing_details(monkeypatch):
    async def fake_send(*args, **kwargs):
        return False

    async def fake_limit(*args, **kwargs):
        return None

    async def fake_audit(**kwargs):
        return None

    monkeypatch.setattr(twofa, "_send_test_email", fake_send)
    monkeypatch.setattr(security, "check_rate_limit_persistent", fake_limit)
    monkeypatch.setattr(security, "client_ip_hash", lambda request: "hashed-ip")
    monkeypatch.setattr(security, "audit_log", fake_audit)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(twofa.admin_test_email(FakeRequest(), admin=_admin()))

    assert exc.value.status_code == 502
    assert exc.value.detail == "No se pudo enviar el correo de prueba"
