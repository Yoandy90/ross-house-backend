import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.maintenance_security_router as secure
from rental.auth_metrics import router as pre_tenant_router
from rental.tenant_router import router as historical_tenant_router


def run(coro):
    return asyncio.run(coro)


class Collection:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, query):
        oid = query.get("_id")
        return next((d for d in self.docs if d.get("_id") == oid), None)


class DB:
    def __init__(self, properties=(), units=()):
        self.properties = Collection(properties)
        self.property_units = Collection(units)


def test_secure_maintenance_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    post_matches = [r for r in app.routes
                    if getattr(r, "path", None) == "/api/tenant/maintenance-request"
                    and "POST" in getattr(r, "methods", set())]
    get_matches = [r for r in app.routes
                   if getattr(r, "path", None) == "/api/tenant/maintenance-requests"
                   and "GET" in getattr(r, "methods", set())]

    assert len(post_matches) == 2
    assert post_matches[0].name == "secure_create_maintenance_request"
    assert post_matches[1].name == "create_maintenance_request"
    assert len(get_matches) == 2
    assert get_matches[0].name == "secure_list_tenant_maintenance_requests"
    assert get_matches[1].name == "list_tenant_maintenance_requests"


def test_client_relationship_fields_are_not_payload_authority():
    source = open("rental/maintenance_security_router.py", encoding="utf-8").read()
    assert '"property_id": location["property_id"]' in source
    assert '"contract_id": contract_id' in source
    assert '"tenant_id": tenant_id' in source
    assert 'data.get("property_id")' not in source
    assert 'data.get("contract_id")' not in source
    assert 'data.get("tenant_id")' not in source


def test_photo_policy_rejects_non_image_and_unbounded_lists():
    with pytest.raises(HTTPException) as exc:
        secure._validated_photos(["http://example.com/a.jpg"])
    assert exc.value.detail == "maintenance_photo_invalid"

    with pytest.raises(HTTPException) as exc:
        secure._validated_photos(["data:image/png;base64,AA"] * 6)
    assert exc.value.detail == "maintenance_photos_too_many"


def test_contract_unit_must_belong_to_contract_property(monkeypatch):
    property_oid = ObjectId()
    other_property_oid = ObjectId()
    unit_oid = ObjectId()
    contract_oid = ObjectId()
    db = DB(
        properties=[{"_id": property_oid, "address": "121 Oak"}],
        units=[{"_id": unit_oid, "property_id": str(other_property_oid),
                "current_contract_id": str(contract_oid)}],
    )
    monkeypatch.setattr(secure, "get_db", lambda: db)
    contract = {"_id": contract_oid, "property_id": str(property_oid), "unit_id": str(unit_oid)}
    with pytest.raises(HTTPException) as exc:
        run(secure._canonical_lease_location(contract))
    assert exc.value.status_code == 409
    assert exc.value.detail == "maintenance_unit_property_mismatch"


def test_contract_unit_must_not_point_to_another_contract(monkeypatch):
    property_oid = ObjectId()
    unit_oid = ObjectId()
    contract_oid = ObjectId()
    db = DB(
        properties=[{"_id": property_oid, "address": "121 Oak"}],
        units=[{"_id": unit_oid, "property_id": str(property_oid),
                "current_contract_id": str(ObjectId())}],
    )
    monkeypatch.setattr(secure, "get_db", lambda: db)
    contract = {"_id": contract_oid, "property_id": str(property_oid), "unit_id": str(unit_oid)}
    with pytest.raises(HTTPException) as exc:
        run(secure._canonical_lease_location(contract))
    assert exc.value.detail == "maintenance_unit_contract_mismatch"
