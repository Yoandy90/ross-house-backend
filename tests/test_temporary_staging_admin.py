import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from rental import admin_2fa_router as twofa
from rental import security


class FakeRequest:
    client = SimpleNamespace(host="127.0.0.1")
    headers = {"user-agent": "pytest", "x-request-id": "test-request"}

    async def json(self):
        return {
            "email": "temporary-admin@example.test",
            "password": "StrongTemporary123!",
            "confirmation": "CREATE_TEMPORARY_STAGING_ADMIN",
        }


def _safe_staging(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DB_NAME", "ross_house_staging")
    monkeypatch.setenv("DISABLE_BACKGROUND_JOBS", "true")
    monkeypatch.setenv("STAGING_FIXTURES_ENABLED", "true")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)


def test_temporary_admin_does_not_require_global_fixture_flag(monkeypatch):
    _safe_staging(monkeypatch)
    monkeypatch.setenv("STAGING_FIXTURES_ENABLED", "false")
    app_users = SimpleNamespace(
        find_one=AsyncMock(return_value=None),
        insert_one=AsyncMock(return_value=SimpleNamespace(inserted_id="temporary-user-id")),
    )
    db = SimpleNamespace(
        app_users=app_users,
        admin_2fa_settings=SimpleNamespace(update_one=AsyncMock()),
        admin_trusted_devices=SimpleNamespace(delete_many=AsyncMock()),
    )
    monkeypatch.setattr(twofa, "get_db", lambda: db)
    monkeypatch.setattr(security, "audit_log", AsyncMock())

    result = asyncio.run(twofa.create_temporary_staging_admin(
        FakeRequest(),
        admin={"_id": "creator-id", "email": "creator@example.test", "role": "admin"},
    ))

    assert result["success"] is True


def test_temporary_admin_route_requires_existing_admin():
    route = next(route for route in twofa.router.routes if route.path == "/admin/staging-access/temporary-admin")
    assert any(dep.call is twofa.auth_admin for dep in route.dependant.dependencies)


def test_temporary_admin_is_staging_only_and_disables_2fa(monkeypatch):
    _safe_staging(monkeypatch)
    app_users = SimpleNamespace(
        find_one=AsyncMock(return_value=None),
        insert_one=AsyncMock(return_value=SimpleNamespace(inserted_id="temporary-user-id")),
    )
    settings = SimpleNamespace(update_one=AsyncMock())
    devices = SimpleNamespace(delete_many=AsyncMock())
    db = SimpleNamespace(
        app_users=app_users,
        admin_2fa_settings=settings,
        admin_trusted_devices=devices,
    )
    monkeypatch.setattr(twofa, "get_db", lambda: db)
    monkeypatch.setattr(security, "audit_log", AsyncMock())

    result = asyncio.run(twofa.create_temporary_staging_admin(
        FakeRequest(),
        admin={"_id": "creator-id", "email": "creator@example.test", "role": "admin"},
    ))

    assert result["success"] is True
    assert result["email"] == "temporary-admin@example.test"
    assert result["two_factor_enabled"] is False
    inserted = app_users.insert_one.await_args.args[0]
    assert inserted["role"] == "admin"
    assert inserted["temporary_staging_admin"] is True
    assert "password" not in inserted
    assert inserted["password_hash"] != "StrongTemporary123!"
    assert settings.update_one.await_args.args[1]["$set"]["enabled"] is False


def test_temporary_admin_fails_closed_outside_staging(monkeypatch):
    _safe_staging(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(twofa.create_temporary_staging_admin(
            FakeRequest(),
            admin={"_id": "creator-id", "email": "creator@example.test", "role": "admin"},
        ))

    assert exc.value.status_code == 403


def test_temporary_admin_never_overwrites_real_account(monkeypatch):
    _safe_staging(monkeypatch)
    db = SimpleNamespace(
        app_users=SimpleNamespace(find_one=AsyncMock(return_value={"_id": "real", "role": "admin"})),
    )
    monkeypatch.setattr(twofa, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(twofa.create_temporary_staging_admin(
            FakeRequest(),
            admin={"_id": "creator-id", "email": "creator@example.test", "role": "admin"},
        ))

    assert exc.value.status_code == 409
