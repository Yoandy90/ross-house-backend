import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.tenant_projection_security_router as secure
from rental.auth_metrics import router as pre_tenant_router
from rental.tenant_router import router as historical_tenant_router


def run(coro):
    return asyncio.run(coro)


class Result:
    matched_count = 1


class Tenants:
    def __init__(self, tenant):
        self.tenant = tenant
        self.last_update = None

    async def find_one(self, query):
        return self.tenant if self.tenant and query.get("_id") == self.tenant.get("_id") else None

    async def update_one(self, query, update):
        self.last_update = (query, update)
        return Result()


class AppUsers:
    def __init__(self, user):
        self.user = user

    async def find_one(self, query):
        return self.user if self.user and query.get("_id") == self.user.get("_id") else None


class DB:
    def __init__(self, tenant=None, app_user=None):
        self.tenants = Tenants(tenant)
        self.app_users = AppUsers(app_user)


class Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


async def allow_identity(*_args, **_kwargs):
    return None


def test_secure_admin_tenant_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    create_matches = [r for r in app.routes
                      if getattr(r, "path", None) == "/api/admin/tenants"
                      and "POST" in getattr(r, "methods", set())]
    convert_matches = [r for r in app.routes
                       if getattr(r, "path", None) == "/api/admin/all-users/{user_id}/convert-to-tenant"
                       and "POST" in getattr(r, "methods", set())]
    put_matches = [r for r in app.routes
                   if getattr(r, "path", None) == "/api/admin/tenants/{tenant_id}"
                   and "PUT" in getattr(r, "methods", set())]

    assert len(create_matches) == 2
    assert create_matches[0].name == "secure_create_tenant"
    assert create_matches[1].name == "create_tenant"
    assert len(convert_matches) == 2
    assert convert_matches[0].name == "secure_convert_app_user_to_tenant"
    assert convert_matches[1].name == "convert_app_user_to_tenant"
    assert len(put_matches) == 2
    assert put_matches[0].name == "secure_update_tenant"
    assert put_matches[1].name == "update_tenant"


def test_admin_cannot_seed_occupancy_projection_on_create():
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_create_tenant(Request({
            "first_name": "A",
            "last_name": "B",
            "phone": "8065550101",
            "current_property_id": str(ObjectId()),
        })))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_occupancy_projection_lifecycle_managed"


def test_safe_create_delegates_to_historical_workflow(monkeypatch):
    called = {}

    async def fake_historical(request):
        called["request"] = request
        return {"success": True, "tenant_id": "new"}

    monkeypatch.setattr(secure, "_assert_identity_available", allow_identity)
    monkeypatch.setattr(secure, "historical_create_tenant", fake_historical)
    request = Request({"first_name": "A", "last_name": "B", "phone": "8065550101"})
    result = run(secure.secure_create_tenant(request))
    assert result["success"] is True
    assert called["request"] is request


def test_conversion_checks_identity_before_creating_second_tenant(monkeypatch):
    user_id = ObjectId()
    app_user = {"_id": user_id, "email": "same@example.com", "phone": "8065550101"}

    async def fake_admin(_request):
        return {"role": "admin"}

    async def reject_identity(_data, **_kwargs):
        raise HTTPException(status_code=409, detail="tenant_email_already_linked")

    monkeypatch.setattr(secure, "auth_admin", fake_admin)
    monkeypatch.setattr(secure, "_assert_identity_available", reject_identity)
    monkeypatch.setattr(secure, "get_db", lambda: DB(app_user=app_user))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_convert_app_user_to_tenant(str(user_id), Request({})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_email_already_linked"


def test_admin_cannot_write_occupancy_projection(monkeypatch):
    tenant_id = ObjectId()

    async def fake_admin(_request):
        return {"role": "admin"}

    monkeypatch.setattr(secure, "auth_admin", fake_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(tenant={"_id": tenant_id}))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_tenant(str(tenant_id), Request({"current_property_id": str(ObjectId())})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_occupancy_projection_lifecycle_managed"


def test_profile_update_does_not_mutate_projection(monkeypatch):
    tenant_id = ObjectId()
    db = DB(tenant={"_id": tenant_id, "first_name": "Old", "last_name": "Name"})

    async def fake_admin(_request):
        return {"role": "admin"}

    monkeypatch.setattr(secure, "auth_admin", fake_admin)
    monkeypatch.setattr(secure, "_assert_identity_available", allow_identity)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_update_tenant(str(tenant_id), Request({"first_name": "New", "notes": "ok"})))
    assert result["success"] is True
    update = db.tenants.last_update[1]["$set"]
    assert update["first_name"] == "New"
    assert update["name"] == "New Name"
    assert "current_property_id" not in update
    assert "current_contract_id" not in update
    assert "current_unit_id" not in update


def test_identity_update_persists_normalized_lookup_fields(monkeypatch):
    tenant_id = ObjectId()
    db = DB(tenant={"_id": tenant_id, "first_name": "A", "last_name": "B"})

    async def fake_admin(_request):
        return {"role": "admin"}

    monkeypatch.setattr(secure, "auth_admin", fake_admin)
    monkeypatch.setattr(secure, "_assert_identity_available", allow_identity)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_update_tenant(str(tenant_id), Request({
        "email": " User@Example.COM ",
        "phone": "+1 (806) 555-0100",
    })))
    assert result["success"] is True
    update = db.tenants.last_update[1]["$set"]
    assert update["email"] == "user@example.com"
    assert update["email_normalized"] == "user@example.com"
    assert update["phone_normalized"] == "18065550100"
    assert "identity_normalized_at" in update
