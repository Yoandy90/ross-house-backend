import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import rental.auth_router as auth
import rental.tenant_router as tenant_router
import rental.turnstile_helper as turnstile


def run(coro):
    return asyncio.run(coro)


class Request:
    client = SimpleNamespace(host="127.0.0.1")

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class Collection:
    def __init__(self):
        self.rows = []

    async def find_one(self, query):
        return next((row for row in self.rows if row.get("email") == query.get("email")), None)

    async def insert_one(self, row):
        from bson import ObjectId

        stored = dict(row)
        stored.setdefault("_id", ObjectId())
        self.rows.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def delete_one(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if row.get("_id") != query.get("_id")]
        return SimpleNamespace(deleted_count=before - len(self.rows))


class DB:
    def __init__(self):
        self.app_users = Collection()
        self.service_providers = Collection()
        self.tenants = Collection()


@pytest.fixture
def registration_env(monkeypatch):
    db = DB()

    async def allow_captcha(*_args, **_kwargs):
        return True

    async def token(*_args, **_kwargs):
        return "session-token"

    async def no_email(*_args, **_kwargs):
        return True

    monkeypatch.setattr(auth, "get_db", lambda: db)
    monkeypatch.setattr(auth, "hash_password", lambda value: f"hashed:{value}")
    monkeypatch.setattr(auth, "create_session_token", token)
    monkeypatch.setattr(turnstile, "verify_turnstile_token", allow_captcha)
    monkeypatch.setattr(tenant_router, "_send_welcome_email", no_email)
    auth._rate_limit_store.clear()
    return db


def contractor_payload(**overrides):
    payload = {
        "name": "Pat Contractor",
        "email": "pat@example.com",
        "phone": "8065551212",
        "password": "StrongPass1!",
        "role": "contractor",
        "services": ["plumber", "electrician"],
        "language_pref": "es",
    }
    payload.update(overrides)
    return payload


def test_public_contractor_creates_pending_scoped_maintenance_account(registration_env):
    result = run(auth.marketplace_register(Request(contractor_payload())))

    assert result["user"]["role"] == "maintenance"
    assert result["registration_status"] == "pending_review"
    user = registration_env.app_users.rows[0]
    provider = registration_env.service_providers.rows[0]
    assert user["maintenance_worker_type"] == "contractor"
    assert user["service_provider_id"] == provider["_id"]
    assert provider["app_user_id"] == str(user["_id"])
    assert provider["worker_type"] == "contractor"
    assert provider["status"] == "pending_review"
    assert provider["services"] == ["plumber", "electrician"]


def test_public_contractor_requires_valid_trade(registration_env):
    with pytest.raises(HTTPException) as exc:
        run(auth.marketplace_register(Request(contractor_payload(services=[]))))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        run(auth.marketplace_register(Request(contractor_payload(services=["company_employee"]))))
    assert exc.value.status_code == 400


def test_public_cannot_register_as_maintenance_employee(registration_env):
    with pytest.raises(HTTPException) as exc:
        run(auth.marketplace_register(Request(contractor_payload(role="maintenance"))))
    assert exc.value.status_code == 400
    assert not registration_env.app_users.rows
    assert not registration_env.service_providers.rows
