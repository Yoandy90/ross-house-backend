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


class Collection:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, query):
        return self.doc


class DB:
    def __init__(self, prop=None, unit=None):
        self.properties = Collection(prop)
        self.property_units = Collection(unit)


async def allow_admin(_request):
    return {"_id": str(ObjectId()), "role": "admin", "email": "admin@example.com"}


def test_unit_topology_security_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_units_router, prefix="/api")

    expected = {
        ("/api/admin/properties/{property_id}/units", "POST"): ("secure_create_units", "create_units"),
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
