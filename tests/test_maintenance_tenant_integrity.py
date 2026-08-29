import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.maintenance_security_router as secure
import rental.maintenance_ownership_security_router as ownership
from rental.auth_metrics import router as pre_tenant_router
from rental.tenant_router import router as historical_tenant_router
from rental.service_providers_router import router as historical_provider_router


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


def test_secure_provider_help_route_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_provider_router, prefix="/api")

    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/tenant/service-providers/request-help"
               and "POST" in getattr(r, "methods", set())]

    assert len(matches) == 2
    assert matches[0].name == "secure_tenant_request_help"
    assert matches[1].name == "tenant_request_help"


def test_secure_admin_maintenance_update_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/admin/maintenance-requests/{request_id}"
               and "PUT" in getattr(r, "methods", set())]

    assert len(matches) == 2
    assert matches[0].name == "secure_update_maintenance_request"
    assert matches[1].name == "update_maintenance_request"


def test_client_relationship_fields_are_not_payload_authority():
    source = open("rental/maintenance_security_router.py", encoding="utf-8").read()
    assert '"property_id": location["property_id"]' in source
    assert '"contract_id": contract_id' in source
    assert '"tenant_id": tenant_id' in source
    assert 'data.get("property_id")' not in source
    assert 'data.get("contract_id")' not in source
    assert 'data.get("tenant_id")' not in source


def test_provider_help_relationship_fields_are_lease_derived():
    source = open("rental/maintenance_ownership_security_router.py", encoding="utf-8").read()
    assert '"tenant_id": str(tenant["_id"])' in source
    assert '"contract_id": str(contract["_id"])' in source
    assert '"property_id": location["property_id"]' in source
    assert '"unit_id": location["unit_id"]' in source
    assert '"relationship_source": "active_contract"' in source
    assert 'data.get("tenant_id")' not in source
    assert 'data.get("contract_id")' not in source
    assert 'data.get("property_id")' not in source
    assert 'data.get("unit_id")' not in source


def test_provider_help_rejects_cross_tenant_contract(monkeypatch):
    tenant_oid = ObjectId()
    other_tenant_oid = ObjectId()
    contract_oid = ObjectId()

    async def fake_auth(_request):
        return {"sub": "user"}

    async def fake_tenant(_user):
        return {"_id": tenant_oid}

    async def fake_contract(_tenant):
        return {"_id": contract_oid, "tenant_id": str(other_tenant_oid)}

    monkeypatch.setattr(ownership, "auth_marketplace", fake_auth)
    monkeypatch.setattr(ownership, "resolve_authenticated_tenant", fake_tenant)
    monkeypatch.setattr(ownership, "find_active_contract_for_tenant", fake_contract)

    with pytest.raises(HTTPException) as exc:
        run(ownership._active_maintenance_context(SimpleNamespace()))
    assert exc.value.status_code == 409
    assert exc.value.detail == "maintenance_contract_tenant_mismatch"


def test_admin_maintenance_update_keeps_ownership_immutable_and_uses_cas():
    source = open("rental/maintenance_ownership_security_router.py", encoding="utf-8").read()
    assert 'maintenance_ownership_immutable' in source
    for field in ("tenant_id", "contract_id", "property_id", "unit_id"):
        assert f'"{field}"' in source
    assert '{"_id": ticket["_id"], "status": ticket.get("status")}' in source
    assert 'maintenance_concurrent_update' in source
    assert 'maintenance_status_transition_invalid' in source


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


def test_active_contract_unit_must_have_exact_claim(monkeypatch):
    property_oid = ObjectId()
    unit_oid = ObjectId()
    contract_oid = ObjectId()
    db = DB(
        properties=[{"_id": property_oid, "address": "121 Oak"}],
        units=[{"_id": unit_oid, "property_id": str(property_oid),
                "current_contract_id": ""}],
    )
    monkeypatch.setattr(secure, "get_db", lambda: db)
    contract = {"_id": contract_oid, "property_id": str(property_oid), "unit_id": str(unit_oid)}
    with pytest.raises(HTTPException) as exc:
        run(secure._canonical_lease_location(contract))
    assert exc.value.status_code == 409
    assert exc.value.detail == "maintenance_unit_contract_mismatch"
