import asyncio

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
        return self.tenant if query.get("_id") == self.tenant.get("_id") else None

    async def update_one(self, query, update):
        self.last_update = (query, update)
        return Result()


class DB:
    def __init__(self, tenant):
        self.tenants = Tenants(tenant)


class Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def test_secure_admin_tenant_update_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")
    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/admin/tenants/{tenant_id}"
               and "PUT" in getattr(r, "methods", set())]
    assert len(matches) == 2
    assert matches[0].name == "secure_update_tenant"
    assert matches[1].name == "update_tenant"


def test_admin_cannot_write_occupancy_projection(monkeypatch):
    tenant_id = ObjectId()

    async def fake_admin(_request):
        return {"role": "admin"}

    monkeypatch.setattr(secure, "auth_admin", fake_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB({"_id": tenant_id}))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_tenant(str(tenant_id), Request({"current_property_id": str(ObjectId())})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_occupancy_projection_lifecycle_managed"


def test_profile_update_does_not_mutate_projection(monkeypatch):
    tenant_id = ObjectId()
    db = DB({"_id": tenant_id, "first_name": "Old", "last_name": "Name"})

    async def fake_admin(_request):
        return {"role": "admin"}

    monkeypatch.setattr(secure, "auth_admin", fake_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_update_tenant(str(tenant_id), Request({"first_name": "New", "notes": "ok"})))
    assert result["success"] is True
    update = db.tenants.last_update[1]["$set"]
    assert update["first_name"] == "New"
    assert update["name"] == "New Name"
    assert "current_property_id" not in update
    assert "current_contract_id" not in update
    assert "current_unit_id" not in update
