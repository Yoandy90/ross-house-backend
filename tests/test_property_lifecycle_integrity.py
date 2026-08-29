import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import BackgroundTasks, FastAPI, HTTPException

import rental.property_lifecycle_security_router as secure
from rental.auth_metrics import router as security_router
from rental.properties_router import router as historical_properties_router


def run(coro):
    return asyncio.run(coro)


class Request:
    def __init__(self, payload=None):
        self.payload = payload or {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self.payload


class Result:
    def __init__(self, deleted_count=1):
        self.deleted_count = deleted_count


class Collection:
    def __init__(self, doc=None, deleted_count=1):
        self.doc = doc
        self.deleted_count = deleted_count
        self.deletes = []

    async def find_one(self, query):
        return self.doc

    async def delete_one(self, query):
        self.deletes.append(query)
        return Result(self.deleted_count)


class DB:
    def __init__(self, prop, contract=None, unit=None, deleted_count=1):
        self.properties = Collection(prop, deleted_count)
        self.rental_contracts = Collection(contract)
        self.property_units = Collection(unit)


async def allow_admin(_request):
    return {"_id": str(ObjectId()), "role": "admin", "email": "admin@example.com"}


def _property(**extra):
    doc = {
        "_id": ObjectId(),
        "property_number": "PROP-2026-001",
        "status": "available",
        "current_contract_id": None,
        "current_tenant_id": None,
    }
    doc.update(extra)
    return doc


def test_property_security_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_properties_router, prefix="/api")

    expected = {
        ("/api/admin/properties", "POST"): ("secure_create_property", "create_property"),
        ("/api/admin/properties/{property_id}", "PUT"): ("secure_update_property", "update_property"),
        ("/api/admin/properties/{property_id}", "DELETE"): ("secure_delete_property", "delete_property"),
    }
    for (path, method), names in expected.items():
        matches = [r for r in app.routes if getattr(r, "path", None) == path and method in getattr(r, "methods", set())]
        assert len(matches) == 2
        assert (matches[0].name, matches[1].name) == names


def test_create_cannot_start_rented(monkeypatch):
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_create_property(Request({"status": "rented"}), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_rented_status_lifecycle_managed"


def test_update_cannot_set_rented(monkeypatch):
    prop = _property()
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(prop))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_property(str(prop["_id"]), Request({"status": "rented"}), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_rented_status_lifecycle_managed"


def test_update_cannot_release_claimed_property(monkeypatch):
    prop = _property(status="rented", current_contract_id=str(ObjectId()), current_tenant_id=str(ObjectId()))
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(prop))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_property(str(prop["_id"]), Request({"status": "available"}), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_occupancy_claimed"


def test_update_cannot_hide_active_contract_when_projection_missing(monkeypatch):
    prop = _property(status="rented")
    contract = {"_id": ObjectId(), "property_id": str(prop["_id"]), "status": "active"}
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(prop, contract=contract))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_property(str(prop["_id"]), Request({"status": "maintenance"}), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_active_lease_conflict"


def test_safe_profile_update_delegates_after_guard(monkeypatch):
    prop = _property()
    db = DB(prop)
    called = {}

    async def historical(property_id, request, background_tasks):
        called["property_id"] = property_id
        called["payload"] = await request.json()
        return {"success": True}

    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    monkeypatch.setattr(secure, "historical_update_property", historical)
    result = run(secure.secure_update_property(str(prop["_id"]), Request({"notes": "safe"}), BackgroundTasks()))
    assert result["success"] is True
    assert called["property_id"] == str(prop["_id"])
    assert called["payload"] == {"notes": "safe"}


def test_delete_blocks_nonterminal_contract_even_without_projection(monkeypatch):
    prop = _property()
    contract = {"_id": ObjectId(), "property_id": str(prop["_id"]), "status": "pending_activation"}
    db = DB(prop, contract=contract)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_delete_property(str(prop["_id"]), Request()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_delete_contract_exists"
    assert db.properties.deletes == []


def test_delete_blocks_units_to_avoid_orphans(monkeypatch):
    prop = _property()
    unit = {"_id": ObjectId(), "property_id": str(prop["_id"])}
    db = DB(prop, unit=unit)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_delete_property(str(prop["_id"]), Request()))
    assert exc.value.detail == "property_delete_units_exist"
    assert db.properties.deletes == []


def test_delete_uses_no_claim_cas_and_ignores_legacy_force_query(monkeypatch):
    prop = _property(status="rented")
    db = DB(prop)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    result = run(secure.secure_delete_property(str(prop["_id"]), Request()))
    assert result["success"] is True
    query = db.properties.deletes[0]
    assert query["_id"] == prop["_id"]
    assert "$and" in query
    rendered = repr(query)
    assert "current_contract_id" in rendered
    assert "current_tenant_id" in rendered
    assert "force" not in rendered


def test_source_never_writes_rented_or_clears_occupancy_directly():
    source = open(secure.__file__, encoding="utf-8").read()
    assert "properties.update_one" not in source
    assert "current_contract_id': None" not in source
    assert '"current_contract_id": None' not in source
    assert "current_tenant_id': None" not in source
    assert '"current_tenant_id": None' not in source
