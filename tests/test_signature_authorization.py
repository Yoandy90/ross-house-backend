import pytest
from fastapi import FastAPI, HTTPException

from rental import lease_signature_security_router as legacy_guard
from rental import signatures_router


class _Tenants:
    def __init__(self, doc=None):
        self.doc = doc
    async def find_one(self, query):
        return self.doc


class _DB:
    def __init__(self, tenant_doc=None):
        self.tenants = _Tenants(tenant_doc)


def _endpoint_key(route):
    endpoint = getattr(route, "endpoint", None)
    return (getattr(endpoint, "__module__", ""), getattr(endpoint, "__name__", ""))


@pytest.mark.asyncio
async def test_legacy_tenant_must_match_lease_party(monkeypatch):
    actor = {"_id": "actor-1", "email": "tenant@example.com", "role": "tenant"}
    async def fake_auth(_request): return actor
    async def fake_tenant(_actor): return {"_id": "tenant-1", "email": "tenant@example.com"}
    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    monkeypatch.setattr(legacy_guard, "resolve_authenticated_tenant", fake_tenant)
    with pytest.raises(HTTPException) as exc:
        await legacy_guard._authorize_actor(object(), {"tenant_id": "other-tenant"}, "tenant")
    assert exc.value.status_code == 403
    assert exc.value.detail == "lease_tenant_mismatch"


@pytest.mark.asyncio
async def test_legacy_tenant_email_fallback_requires_unique_canonical_tenant(monkeypatch):
    actor = {"_id": "actor-1", "email": "Tenant@Example.com", "role": "tenant"}
    tenant = {"_id": "tenant-1", "email": "tenant@example.com"}
    async def fake_auth(_request): return actor
    async def fake_tenant(_actor): return tenant
    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    monkeypatch.setattr(legacy_guard, "resolve_authenticated_tenant", fake_tenant)
    allowed = await legacy_guard._authorize_actor(
        object(), {"tenant_id": "", "tenant_email": "tenant@example.com"}, "tenant"
    )
    assert allowed is actor

    async def no_tenant(_actor): return None
    monkeypatch.setattr(legacy_guard, "resolve_authenticated_tenant", no_tenant)
    with pytest.raises(HTTPException) as exc:
        await legacy_guard._authorize_actor(
            object(), {"tenant_id": "", "tenant_email": "tenant@example.com"}, "tenant"
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "lease_tenant_mismatch"


@pytest.mark.asyncio
async def test_legacy_tenant_bound_id_uses_canonical_resolution(monkeypatch):
    actor = {"_id": "app-user-1", "email": "tenant@example.com", "role": "tenant"}
    async def fake_auth(_request): return actor
    async def fake_tenant(_actor): return {"_id": "tenant-1", "email": "tenant@example.com"}
    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    monkeypatch.setattr(legacy_guard, "resolve_authenticated_tenant", fake_tenant)
    allowed = await legacy_guard._authorize_actor(object(), {"tenant_id": "tenant-1"}, "tenant")
    assert allowed is actor


@pytest.mark.asyncio
async def test_legacy_landlord_requires_role_and_exact_id(monkeypatch):
    actor = {"_id": "landlord-1", "email": "owner@example.com", "role": "landlord"}
    async def fake_auth(_request): return actor
    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    allowed = await legacy_guard._authorize_actor(object(), {"landlord_id": "landlord-1"}, "landlord")
    assert allowed is actor
    with pytest.raises(HTTPException) as exc:
        await legacy_guard._authorize_actor(object(), {"landlord_id": "landlord-2"}, "landlord")
    assert exc.value.status_code == 403
    assert exc.value.detail == "lease_landlord_mismatch"


@pytest.mark.asyncio
async def test_legacy_admin_role_uses_admin_auth(monkeypatch):
    admin = {"_id": "admin-1", "role": "admin", "email": "admin@example.com"}
    calls = []
    async def fake_admin(request):
        calls.append(request); return admin
    async def must_not_use_marketplace(_request):
        raise AssertionError("admin signing must use auth_admin")
    monkeypatch.setattr(legacy_guard, "auth_admin", fake_admin)
    monkeypatch.setattr(legacy_guard, "auth_marketplace", must_not_use_marketplace)
    request = object()
    assert await legacy_guard._authorize_actor(request, {}, "admin") is admin
    assert calls == [request]


@pytest.mark.asyncio
async def test_modern_contract_signing_rejects_non_signer_roles():
    with pytest.raises(HTTPException) as exc:
        await signatures_router._authorize_contract_signer(
            {"_id": "buyer-1", "role": "buyer", "email": "buyer@example.com"},
            {"tenant_id": "buyer-1"}, _DB())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_modern_landlord_is_bound_to_contract_id():
    with pytest.raises(HTTPException) as exc:
        await signatures_router._authorize_contract_signer(
            {"_id": "landlord-1", "role": "landlord", "email": "owner@example.com"},
            {"landlord_id": "landlord-2"}, _DB())
    assert exc.value.status_code == 403


def test_signatures_never_directly_activate_occupancy():
    from pathlib import Path
    legacy = Path("rental/lease_signature_security_router.py").read_text()
    modern = Path("rental/signatures_router.py").read_text()
    assert '"pending_activation"' in legacy
    assert "update_data['status'] = 'pending_activation'" in modern
    assert "update_data['status'] = 'active'" not in modern
    assert "{'status': 'rented'" not in modern
    assert "startswith('data:image/')" in modern
    assert 'write_filter = {"_id": object_id, "status": expected_status}' in legacy
    assert "resolve_authenticated_tenant" in legacy
    assert 'find_one({\n            "email"' not in legacy


def test_secure_legacy_route_is_registered_before_historical_handler():
    from rental.dnc_registry_router import router as pre_contract_router
    from rental.contracts_router import router as historical_contracts_router
    secure_key = ("rental.lease_signature_security_router", "secure_legacy_lease_sign")
    historical_key = ("rental.contracts_router", "sign_lease")
    secure_routes = [r for r in pre_contract_router.routes if _endpoint_key(r) == secure_key]
    historical_routes = [r for r in historical_contracts_router.routes if _endpoint_key(r) == historical_key]
    assert len(secure_routes) == 1 and len(historical_routes) == 1
    assert getattr(secure_routes[0], "path", None) == "/lease/{lease_id}/sign"
    assert getattr(historical_routes[0], "path", None) == "/lease/{lease_id}/sign"
    assert "POST" in getattr(secure_routes[0], "methods", set())
    assert "POST" in getattr(historical_routes[0], "methods", set())
    app = FastAPI()
    app.include_router(pre_contract_router, prefix="/api")
    app.include_router(historical_contracts_router, prefix="/api")
    names = [getattr(route, "name", "") for route in app.routes]
    assert names.count("secure_legacy_lease_sign") == 1
    assert names.count("sign_lease") == 1
    assert names.index("secure_legacy_lease_sign") < names.index("sign_lease")


def test_server_source_keeps_pre_contract_router_order():
    from pathlib import Path
    source = Path("server.py").read_text()
    dnc = 'app.include_router(dnc_registry_router, prefix="/api")'
    contracts = 'app.include_router(contracts_router, prefix="/api")'
    assert dnc in source and contracts in source
    assert source.index(dnc) < source.index(contracts)
