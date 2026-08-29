import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import BackgroundTasks, FastAPI, HTTPException

import rental.property_lifecycle_security_router as secure
import rental.lease_lifecycle_security_router as lifecycle
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
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class Collection:
    def __init__(self, doc=None, matched_count=1):
        self.doc = doc
        self.matched_count = matched_count
        self.updates = []

    async def find_one(self, query):
        return self.doc

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return Result(self.matched_count)


class DB:
    def __init__(self, prop, contract=None, unit=None, tenant=None, matched_count=1):
        self.properties = Collection(prop, matched_count)
        self.rental_contracts = Collection(contract, matched_count)
        self.property_units = Collection(unit, matched_count)
        self.tenants = Collection(tenant, matched_count)


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
    prop = _property(status="maintenance")
    contract = {"_id": ObjectId(), "property_id": str(prop["_id"]), "status": "active"}
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(prop, contract=contract))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_property(str(prop["_id"]), Request({"status": "available"}), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_active_lease_conflict"


def test_profile_only_update_never_touches_status(monkeypatch):
    prop = _property()
    db = DB(prop)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_update_property(str(prop["_id"]), Request({"notes": "safe"}), BackgroundTasks()))
    assert result["success"] is True
    query, update = db.properties.updates[0]
    assert query == {"_id": prop["_id"]}
    assert update["$set"]["notes"] == "safe"
    assert "status" not in update["$set"]
    assert "$unset" not in update


def test_safe_status_change_uses_no_claim_cas_and_clears_manual_lock(monkeypatch):
    prop = _property(status="available", status_manually_set=True)
    db = DB(prop)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_update_property(str(prop["_id"]), Request({"status": "maintenance"}), BackgroundTasks()))
    assert result["success"] is True
    query, update = db.properties.updates[0]
    assert query["_id"] == prop["_id"]
    assert query["status"] == "available"
    rendered = repr(query)
    assert "current_contract_id" in rendered
    assert "current_tenant_id" in rendered
    assert update["$set"]["status"] == "maintenance"
    assert update["$set"]["status_manually_set"] is False
    assert "status_manually_set_at" in update["$unset"]
    assert "status_manually_set_by" in update["$unset"]


def test_status_cas_loss_fails_closed(monkeypatch):
    prop = _property(status="available")
    db = DB(prop, matched_count=0)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_property(str(prop["_id"]), Request({"status": "maintenance"}), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_state_changed"


def test_hard_delete_is_disabled_even_for_unclaimed_property(monkeypatch):
    prop = _property()
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(prop))
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_delete_property(str(prop["_id"]), Request()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_delete_requires_archival"


def test_whole_property_activation_rejects_maintenance_before_claim(monkeypatch):
    prop = _property(status="maintenance")
    contract = {"_id": ObjectId(), "property_id": str(prop["_id"]), "unit_id": None}
    monkeypatch.setattr(lifecycle, "get_db", lambda: DB(prop))
    with pytest.raises(HTTPException) as exc:
        run(lifecycle._preflight_whole_property_activation(contract, str(contract["_id"])))
    assert exc.value.status_code == 409
    assert exc.value.detail == "lease_property_not_available"


def test_whole_property_activation_rejects_legacy_manual_lock(monkeypatch):
    prop = _property(status="available", status_manually_set=True)
    contract = {"_id": ObjectId(), "property_id": str(prop["_id"]), "unit_id": None}
    monkeypatch.setattr(lifecycle, "get_db", lambda: DB(prop))
    with pytest.raises(HTTPException) as exc:
        run(lifecycle._preflight_whole_property_activation(contract, str(contract["_id"])))
    assert exc.value.detail == "lease_property_manual_status_conflict"


def test_lifecycle_whole_property_claim_requires_available_status():
    source = open(lifecycle.__file__, encoding="utf-8").read()
    assert '"_id": prop_oid, "status": "available", "status_manually_set": {"$ne": True}' in source
    assert '"$set": {"status": "rented", "current_contract_id": contract_id' in source


def test_property_security_has_no_historical_update_or_hard_delete():
    source = open(secure.__file__, encoding="utf-8").read()
    assert "historical_update_property" not in source
    assert "properties.delete_one" not in source
    assert "property_delete_requires_archival" in source
