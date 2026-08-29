import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.tenant_login_security_router as secure
from rental.auth_metrics import router as pre_tenant_router
from rental.tenant_router import router as historical_tenant_router


def run(coro):
    return asyncio.run(coro)


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, _limit):
        return list(self.docs)


class Tenants:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, _query):
        return Cursor(self.docs)


class DB:
    def __init__(self, docs):
        self.tenants = Tenants(docs)


class Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def test_secure_tenant_login_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/tenant/login"
               and "POST" in getattr(r, "methods", set())]
    assert len(matches) == 2
    assert matches[0].name == "secure_tenant_login"
    assert matches[1].name == "tenant_login"


def test_last_four_phone_is_rejected(monkeypatch):
    tenant = {"_id": ObjectId(), "email": "tenant@example.com", "phone": "8065551212"}
    monkeypatch.setattr(secure, "get_db", lambda: DB([tenant]))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_tenant_login(Request({"email": "tenant@example.com", "phone": "1212"})))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Credenciales inválidas"


def test_full_normalized_phone_match_succeeds(monkeypatch):
    tenant = {
        "_id": ObjectId(), "email": "Tenant@Example.com", "phone": "(806) 555-1212",
        "name": "Test Tenant", "tenant_number": "INQ-1",
    }
    monkeypatch.setattr(secure, "get_db", lambda: DB([tenant]))
    monkeypatch.setattr(secure, "create_tenant_token", lambda tenant_id, email: f"token:{tenant_id}:{email}")

    result = run(secure.secure_tenant_login(Request({"email": " tenant@example.com ", "phone": "806-555-1212"})))
    assert result["success"] is True
    assert result["tenant"]["id"] == str(tenant["_id"])
    assert result["token"].startswith("token:")


def test_duplicate_normalized_email_fails_closed(monkeypatch):
    monkeypatch.setattr(secure, "get_db", lambda: DB([
        {"_id": ObjectId(), "email": "Tenant@Example.com", "phone": "8065551212"},
        {"_id": ObjectId(), "email": "tenant@example.com", "phone": "8065551212"},
    ]))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_tenant_login(Request({"email": "tenant@example.com", "phone": "8065551212"})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_login_identity_ambiguous"
