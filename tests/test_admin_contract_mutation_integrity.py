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
    def __init__(self, payload):
        self.payload = payload
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
    def __init__(self, contract, matched_count=1, saved_signature=None):
        self.rental_contracts = Collection(contract, matched_count)
        self.admin_signatures = Collection(saved_signature)


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
