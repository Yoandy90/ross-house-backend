import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.admin_contract_mutation_security_router as secure
from rental.auth_metrics import router as security_router
from rental.contracts_router import router as historical_contracts_router


def run(coro):
    return asyncio.run(coro)


class Request:
    def __init__(self, payload=None):
        self.payload = payload or {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self.payload


class Result:
    def __init__(self, matched_count=1, deleted_count=1):
        self.matched_count = matched_count
        self.deleted_count = deleted_count


class Collection:
    def __init__(self, doc=None, matched_count=1, deleted_count=1):
        self.doc = doc
        self.matched_count = matched_count
        self.deleted_count = deleted_count
        self.updates = []
        self.deletes = []

    async def find_one(self, query):
        return self.doc

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return Result(matched_count=self.matched_count)

    async def delete_one(self, query):
        self.deletes.append(query)
        return Result(deleted_count=self.deleted_count)


class DB:
    def __init__(self, contract, matched_count=1, saved_signature=None,
                 prop=None, tenant=None, unit=None, deleted_count=1):
        self.rental_contracts = Collection(contract, matched_count, deleted_count)
        self.admin_signatures = Collection(saved_signature)
        self.properties = Collection(prop)
        self.tenants = Collection(tenant)
        self.property_units = Collection(unit)


async def allow_admin(_request):
    return {"_id": str(ObjectId()), "role": "admin", "email": "admin@example.com", "name": "Admin"}


def _contract(status="draft", **extra):
    doc = {
        "_id": ObjectId(),
        "status": status,
        "tenant_id": str(ObjectId()),
        "property_id": str(ObjectId()),
        "tenant_name": "Tenant One",
        "tenant_signature": None,
        "admin_signature": None,
        "landlord_signature": None,
    }
    doc.update(extra)
    return doc


def test_admin_mutation_security_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_contracts_router, prefix="/api")

    expected = {
        ("/api/admin/rental-contracts/{contract_id}", "PUT"): ("secure_update_contract", "update_contract"),
        ("/api/admin/rental-contracts/{contract_id}", "DELETE"): ("secure_delete_contract", "delete_contract"),
        ("/api/admin/properties/sync-status", "POST"): ("secure_sync_property_status", "sync_property_status"),
        ("/api/admin/rental-contracts/{contract_id}/sign", "POST"): ("secure_admin_contract_sign", "sign_contract"),
        ("/api/admin/rental-contracts/{contract_id}/office-sign", "POST"): ("secure_office_sign_contract", "office_sign_contract"),
    }
    for (path, method), names in expected.items():
        matches = [r for r in app.routes if getattr(r, "path", None) == path and method in getattr(r, "methods", set())]
        assert len(matches) == 2
        assert (matches[0].name, matches[1].name) == names


def test_generic_put_cannot_write_status(monkeypatch):
    contract = _contract("draft")
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(contract))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_contract(str(contract["_id"]), Request({"status": "active"})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "lease_status_lifecycle_managed"


def test_generic_put_locks_relationships_after_draft(monkeypatch):
    contract = _contract("pending_tenant")
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(contract))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_contract(str(contract["_id"]), Request({"tenant_id": str(ObjectId())})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "lease_relationship_locked_after_draft"


def test_draft_property_change_cannot_break_existing_unit_relationship(monkeypatch):
    old_property = str(ObjectId())
    new_property = str(ObjectId())
    unit_id = str(ObjectId())
    contract = _contract("draft", property_id=old_property, unit_id=unit_id)
    db = DB(
        contract,
        prop={"_id": ObjectId(new_property)},
        tenant={"_id": ObjectId(contract["tenant_id"])},
        unit={"_id": ObjectId(unit_id), "property_id": old_property},
    )
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_update_contract(str(contract["_id"]), Request({"property_id": new_property})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "lease_unit_property_mismatch"


def test_active_contract_delete_is_blocked_even_if_legacy_force_would_allow(monkeypatch):
    contract = _contract("active")
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: DB(contract))

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_delete_contract(str(contract["_id"]), Request()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "lease_delete_requires_lifecycle"


def test_draft_delete_fails_closed_when_projection_exists(monkeypatch):
    contract = _contract("draft")
    db = DB(
        contract,
        prop={"_id": ObjectId(contract["property_id"]), "current_contract_id": str(contract["_id"])},
        tenant={"_id": ObjectId(contract["tenant_id"]), "current_contract_id": ""},
    )
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_delete_contract(str(contract["_id"]), Request()))
    assert exc.value.detail == "lease_delete_projection_exists"
    assert db.rental_contracts.deletes == []


def test_draft_delete_uses_exact_status_and_no_claim_cas(monkeypatch):
    contract = _contract("draft")
    db = DB(
        contract,
        prop={"_id": ObjectId(contract["property_id"]), "current_contract_id": ""},
        tenant={"_id": ObjectId(contract["tenant_id"]), "current_contract_id": ""},
    )
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_delete_contract(str(contract["_id"]), Request()))
    assert result["success"] is True
    query = db.rental_contracts.deletes[0]
    assert query["_id"] == contract["_id"]
    assert query["status"] == "draft"
    assert "$or" in query


def test_manual_sync_delegates_only_to_conservative_reconciler(monkeypatch):
    contract = _contract("draft")
    db = DB(contract)
    called = {}

    async def fake_reconcile(actual_db):
        called["db"] = actual_db
        return {"fixed": 1, "skipped_manual": 0, "unchanged": 2, "ambiguous": 0, "conflicts": 0}

    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    monkeypatch.setattr(secure, "reconcile_property_statuses", fake_reconcile)

    result = run(secure.secure_sync_property_status(Request()))
    assert called["db"] is db
    assert result["success"] is True
    assert result["fixed"] == 1


def test_legacy_admin_sign_records_evidence_without_activation(monkeypatch):
    contract = _contract("draft")
    db = DB(contract)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_admin_contract_sign(
        str(contract["_id"]),
        Request({"type": "canvas", "image_data": "data:image/png;base64,AA"}),
    ))
    assert result["new_status"] == "pending_tenant"
    query, update = db.rental_contracts.updates[0]
    assert query["status"] == "draft"
    assert update["$set"]["status"] == "pending_tenant"
    assert update["$set"]["admin_signature"]
    assert "current_contract_id" not in update["$set"]
    assert "current_tenant_id" not in update["$set"]
    assert "current_property_id" not in update["$set"]


def test_legacy_admin_sign_with_tenant_evidence_stops_at_pending_activation(monkeypatch):
    contract = _contract("pending_signature", tenant_signature={"image_data": "data:image/png;base64,T"})
    db = DB(contract)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_admin_contract_sign(
        str(contract["_id"]),
        Request({"image_data": "data:image/png;base64,A"}),
    ))
    assert result["new_status"] == "pending_activation"
    assert db.rental_contracts.updates[0][1]["$set"]["status"] == "pending_activation"


def test_office_sign_both_parties_stops_at_pending_activation_and_uses_cas(monkeypatch):
    contract = _contract("pending_tenant", admin_signature={"image_data": "data:image/png;base64,A"})
    db = DB(contract)
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "get_db", lambda: db)

    result = run(secure.secure_office_sign_contract(
        str(contract["_id"]),
        Request({"signer_role": "tenant", "signature": "data:image/png;base64,T", "type": "canvas"}),
    ))
    assert result["new_status"] == "pending_activation"
    query, update = db.rental_contracts.updates[0]
    assert query == {"_id": contract["_id"], "status": "pending_tenant"}
    assert update["$set"]["status"] == "pending_activation"
    assert "current_contract_id" not in update["$set"]
    assert "current_tenant_id" not in update["$set"]
    assert "current_property_id" not in update["$set"]


def test_admin_signature_shadows_have_no_occupancy_collection_writes():
    source = open("rental/admin_contract_mutation_security_router.py", encoding="utf-8").read()
    sign_part = source[source.index("@router.post('/admin/rental-contracts/{contract_id}/sign')"):]
    assert "db.properties.update_one" not in sign_part
    assert "db.tenants.update_one" not in sign_part
    assert '"status": "active"' not in sign_part
    assert '"status": "rented"' not in sign_part
