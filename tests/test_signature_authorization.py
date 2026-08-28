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


@pytest.mark.asyncio
async def test_legacy_tenant_must_match_lease_party(monkeypatch):
    actor = {"_id": "actor-1", "email": "tenant@example.com", "role": "tenant"}

    async def fake_auth(_request):
        return actor

    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    monkeypatch.setattr(legacy_guard, "get_db", lambda: _DB())

    with pytest.raises(HTTPException) as exc:
        await legacy_guard._authorize_actor(object(), {"tenant_id": "other-tenant"}, "tenant")
    assert exc.value.status_code == 403
    assert exc.value.detail == "lease_tenant_mismatch"


@pytest.mark.asyncio
async def test_legacy_tenant_email_fallback_only_when_lease_id_missing(monkeypatch):
    actor = {"_id": "actor-1", "email": "Tenant@Example.com", "role": "tenant"}

    async def fake_auth(_request):
        return actor

    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    monkeypatch.setattr(legacy_guard, "get_db", lambda: _DB())

    allowed = await legacy_guard._authorize_actor(
        object(), {"tenant_id": "", "tenant_email": "tenant@example.com"}, "tenant"
    )
    assert allowed is actor

    with pytest.raises(HTTPException) as exc:
        await legacy_guard._authorize_actor(
            object(), {"tenant_id": "bound-id", "tenant_email": "tenant@example.com"}, "tenant"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_legacy_landlord_requires_role_and_exact_id(monkeypatch):
    actor = {"_id": "landlord-1", "email": "owner@example.com", "role": "landlord"}

    async def fake_auth(_request):
        return actor

    monkeypatch.setattr(legacy_guard, "auth_marketplace", fake_auth)
    allowed = await legacy_guard._authorize_actor(
        object(), {"landlord_id": "landlord-1"}, "landlord"
    )
    assert allowed is actor

    with pytest.raises(HTTPException) as exc:
        await legacy_guard._authorize_actor(
            object(), {"landlord_id": "landlord-2"}, "landlord"
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "lease_landlord_mismatch"


@pytest.mark.asyncio
async def test_legacy_admin_role_uses_admin_auth(monkeypatch):
    admin = {"_id": "admin-1", "role": "admin", "email": "admin@example.com"}
    calls = []

    async def fake_admin(request):
        calls.append(request)
        return admin

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
            {"tenant_id": "buyer-1"},
            _DB(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_modern_landlord_is_bound_to_contract_id():
    with pytest.raises(HTTPException) as exc:
        await signatures_router._authorize_contract_signer(
            {"_id": "landlord-1", "role": "landlord", "email": "owner@example.com"},
            {"landlord_id": "landlord-2"},
            _DB(),
        )
    assert exc.value.status_code == 403


def test_secure_legacy_route_is_registered_before_historical_handler():
    # Mirrors server.py's intentional order: dnc router immediately precedes
    # contracts_router. FastAPI resolves the first matching path+method.
    from rental.dnc_registry_router import router as pre_contract_router
    from rental.contracts_router import router as historical_contracts_router

    app = FastAPI()
    app.include_router(pre_contract_router, prefix="/api")
    app.include_router(historical_contracts_router, prefix="/api")

    matches = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/lease/{lease_id}/sign"
        and "POST" in getattr(route, "methods", set())
    ]
    assert len(matches) >= 2, "guard and historical route should both be visible during migration"
    assert matches[0].endpoint is legacy_guard.secure_legacy_lease_sign
    assert matches[0].endpoint is not historical_contracts_router.routes[3].endpoint


def test_server_source_keeps_pre_contract_router_order():
    from pathlib import Path
    source = Path("server.py").read_text()
    dnc = 'app.include_router(dnc_registry_router, prefix="/api")'
    contracts = 'app.include_router(contracts_router, prefix="/api")'
    assert dnc in source and contracts in source
    assert source.index(dnc) < source.index(contracts)
