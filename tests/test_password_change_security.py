import asyncio
from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.auth_router as auth_router
from rental.security_email import (
    SECURITY_FROM_NAME,
    _sendgrid_config,
    build_password_changed_message,
)


class Result:
    def __init__(self, modified_count=0):
        self.modified_count = modified_count


class Collection:
    def __init__(self, modified_count=0):
        self.modified_count = modified_count
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        return Result(1)

    async def update_many(self, query, update):
        self.calls.append((query, update))
        return Result(self.modified_count)


class Database:
    def __init__(self):
        self.app_users = Collection()
        self.auth_sessions = Collection(modified_count=2)


class Request:
    def __init__(self, token, body):
        self.headers = {"Authorization": f"Bearer {token}"}
        self._body = body

    async def json(self):
        return self._body


def _run(coro):
    return asyncio.run(coro)


def test_password_change_keeps_current_session_and_sends_notice(monkeypatch):
    db = Database()
    user_id = ObjectId()
    user = {
        "_id": user_id,
        "name": "Ross <House>",
        "email": "owner@example.com",
        "password_hash": "old-hash",
    }
    secret = "password-change-test-secret"
    token = jwt.encode({"user_id": str(user_id), "sid": "current-sid"}, secret)
    email_call = {}

    async def authenticate(_request):
        return user

    async def send_notice(_db, **kwargs):
        email_call.update(kwargs)
        return True

    monkeypatch.setattr(auth_router, "auth_marketplace", authenticate)
    monkeypatch.setattr(auth_router, "get_db", lambda: db)
    monkeypatch.setattr(auth_router, "TENANT_JWT_SECRET", secret)
    monkeypatch.setattr(auth_router, "verify_password", lambda raw, hashed: True)
    monkeypatch.setattr(auth_router, "hash_password", lambda raw: "new-hash")
    monkeypatch.setattr(auth_router, "send_password_changed_email", send_notice)

    response = _run(auth_router.change_password(Request(token, {
        "current_password": "old-password",
        "new_password": "new-password",
    })))

    assert response == {
        "success": True,
        "message": "Contraseña actualizada",
        "other_sessions_revoked": 2,
        "notification_sent": True,
    }
    password_update = db.app_users.calls[0][1]["$set"]
    assert password_update["password_hash"] == "new-hash"
    assert password_update["password_changed_at"].tzinfo is timezone.utc
    session_query, session_update = db.auth_sessions.calls[0]
    assert session_query == {
        "user_id": str(user_id),
        "revoked_at": None,
        "sid": {"$ne": "current-sid"},
    }
    assert session_update["$set"]["revoked_reason"] == "password_changed"
    assert email_call["to_email"] == "owner@example.com"
    assert set(email_call) == {"to_email", "name", "changed_at"}


def test_wrong_current_password_changes_nothing(monkeypatch):
    db = Database()

    async def authenticate(_request):
        return {"_id": ObjectId(), "email": "owner@example.com", "password_hash": "hash"}

    monkeypatch.setattr(auth_router, "auth_marketplace", authenticate)
    monkeypatch.setattr(auth_router, "get_db", lambda: db)
    monkeypatch.setattr(auth_router, "verify_password", lambda raw, hashed: False)

    with pytest.raises(HTTPException) as error:
        _run(auth_router.change_password(Request("unused", {
            "current_password": "wrong",
            "new_password": "new-password",
        })))

    assert error.value.status_code == 401
    assert db.app_users.calls == []
    assert db.auth_sessions.calls == []


def test_email_failure_does_not_undo_password_change(monkeypatch):
    db = Database()
    user_id = ObjectId()
    secret = "password-change-test-secret"
    token = jwt.encode({"user_id": str(user_id), "sid": "current-sid"}, secret)

    async def authenticate(_request):
        return {"_id": user_id, "email": "owner@example.com", "password_hash": "hash"}

    async def failed_notice(_db, **_kwargs):
        return False

    monkeypatch.setattr(auth_router, "auth_marketplace", authenticate)
    monkeypatch.setattr(auth_router, "get_db", lambda: db)
    monkeypatch.setattr(auth_router, "TENANT_JWT_SECRET", secret)
    monkeypatch.setattr(auth_router, "verify_password", lambda raw, hashed: True)
    monkeypatch.setattr(auth_router, "hash_password", lambda raw: "new-hash")
    monkeypatch.setattr(auth_router, "send_password_changed_email", failed_notice)

    response = _run(auth_router.change_password(Request(token, {
        "current_password": "old-password",
        "new_password": "new-password",
    })))

    assert response["success"] is True
    assert response["notification_sent"] is False
    assert len(db.app_users.calls) == 1
    assert len(db.auth_sessions.calls) == 1


def test_security_email_has_timestamp_contact_and_no_password_value():
    content = build_password_changed_message(
        "Ross <House>", datetime(2026, 9, 12, 18, 30, tzinfo=timezone.utc)
    )

    combined = " ".join(content.values())
    assert "12 de septiembre de 2026" in combined
    assert "info@rosshouserentals.com" in combined
    assert "(806) 934-2018" in combined
    assert "Ross &lt;House&gt;" in content["html"]
    assert "new-password" not in combined


def test_security_sender_is_configurable_from_admin_and_has_priority(monkeypatch):
    registry_source = Path("rental/api_keys_router.py").read_text(encoding="utf-8")
    assert '"key": "SECURITY_FROM_EMAIL"' in registry_source
    assert '"label": "Remitente de Alertas de Seguridad"' in registry_source
    assert '"placeholder": "security@rosshouserentals.com"' in registry_source

    monkeypatch.setenv("SENDGRID_API_KEY", "test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "general@rosshouserentals.com")
    monkeypatch.setenv("SECURITY_FROM_EMAIL", "security@rosshouserentals.com")
    api_key, sender = _run(_sendgrid_config(object()))

    assert api_key == "test-key"
    assert sender == "security@rosshouserentals.com"
    assert SECURITY_FROM_NAME == "Ross House Security"
