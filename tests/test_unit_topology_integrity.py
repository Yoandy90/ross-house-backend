import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.unit_topology_security_router as secure
from rental.auth_metrics import router as security_router
from rental.units_router import router as historical_units_router


def run(coro):
    return asyncio.run(coro)


class Request:
    client = SimpleNamespace(host="127.0.0.1")

    def __init__(self, payload=None):
        self.payload = payload or {}

    async def json(self):
        return self.payload


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class Collection:
    def __init__(self, doc, matched_count=1):
        self.doc = doc
        self.matched_count = matched_count
        self.updates = []

    async def find_one(self, query):
        return self.doc

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return Result(self.matched_count)


class DB:
    def __init__(self, prop=None, unit=None, matched_count=1):
        self.properties = Collection(prop, matched_count)
        self.property_units = Collection(unit, matched_count)


async def allow_admin(_request):
    return {"_id": str(ObjectId()), "role": "admin", "email": "admin@example.com"}


async def no_sync(_property_id):
    return None


def test_unit_topology_security_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_units_router, prefix="/api")

    expected = {
        ("/api/admin/properties/{property_id}/units", "POST"): ("secure_create_units", "create_units"),
        ("/api/admin/units/{unit_id}", "PUT"): ("secure_update_unit", "update_unit"),
        ("/api/admin/units/{unit_id}", "DELETE"): ("secure_delete_unit", "delete_unit"),
    }
    for (path, method), names in expected.items():
        matches = [r for r in app.routes if getattr(r, "path", None) == path and method in getattr(r, "methods", set())]
        assert len(matches) == 2
        assert (matches[0].name, matches[1].name) == names


def test_unit_creation_is_frozen_until_managed_topology_workflow(monkeypatch):
    prop = {"_id": ObjectId(), "name": "Multi-unit"}
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(prop=prop))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_create_units(str(prop["_id"]), Request()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_topology_requires_managed_workflow"


def test_unit_delete_is_frozen_until_managed_topology_workflow(monkeypatch):
    unit = {"_id": ObjectId(), "property_id": str(ObjectId()), "status": "available"}
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(unit=unit))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_delete_unit(str(unit["_id"]), Request()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_topology_requires_managed_workflow"


def test_manual_unit_rented_status_is_lifecycle_managed(monkeypatch):
    unit = {"_id": ObjectId(), "property_id": str(ObjectId()), "status": "available"}
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(unit=unit))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_unit(str(unit["_id"]), Request({"status": "rented"})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_rented_status_lifecycle_managed"


def test_claimed_unit_cannot_be_moved_to_maintenance(monkeypatch):
    unit = {
        "_id": ObjectId(), "property_id": str(ObjectId()), "status": "rented",
        "current_contract_id": str(ObjectId()), "current_tenant_id": str(ObjectId()),
    }
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(unit=unit))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_unit(str(unit["_id"]), Request({"status": "maintenance"})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_occupancy_claimed"


def test_safe_unit_status_change_uses_no_claim_cas(monkeypatch):
    unit = {
        "_id": ObjectId(), "property_id": str(ObjectId()), "status": "available",
        "current_contract_id": None, "current_tenant_id": None,
    }
    db = DB(unit=unit)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    monkeypatch.setattr(secure, "sync_property_from_units", no_sync)
    result = run(secure.secure_update_unit(str(unit["_id"]), Request({"status": "maintenance"})))
    assert result["success"] is True
    query, update = db.property_units.updates[0]
    assert query["_id"] == unit["_id"]
    assert query["status"] == "available"
    rendered = repr(query)
    assert "current_contract_id" in rendered
    assert "current_tenant_id" in rendered
    assert update["$set"]["status"] == "maintenance"
    assert "current_contract_id" not in update["$set"]
    assert "current_tenant_id" not in update["$set"]


def test_unit_status_cas_loss_fails_closed(monkeypatch):
    unit = {
        "_id": ObjectId(), "property_id": str(ObjectId()), "status": "available",
        "current_contract_id": None, "current_tenant_id": None,
    }
    db = DB(unit=unit, matched_count=0)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    monkeypatch.setattr(secure, "sync_property_from_units", no_sync)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_unit(str(unit["_id"]), Request({"status": "maintenance"})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_state_changed"


def test_lease_creation_requires_unit_for_multi_unit_property():
    source = open("rental/lease_creation_security_router.py", encoding="utf-8").read()
    assert 'existing_unit = await db.property_units.find_one({"property_id": property_id})' in source
    assert 'prop.get("is_multi_unit") or existing_unit' in source
    assert 'detail="lease_unit_required_for_multi_unit_property"' in source


def test_legacy_whole_property_activation_rechecks_unit_topology():
    source = open("rental/lease_lifecycle_security_router.py", encoding="utf-8").read()
    assert 'existing_unit = await db.property_units.find_one({"property_id": property_id})' in source
    assert 'prop.get("is_multi_unit") or existing_unit' in source
    assert 'detail="lease_unit_required_for_multi_unit_property"' in source
