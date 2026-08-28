from pathlib import Path

from fastapi import FastAPI

from rental.auth_metrics import router as pre_contract_router
from rental.contracts_router import router as historical_contracts_router


def test_secure_status_transition_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_contract_router, prefix="/api")
    app.include_router(historical_contracts_router, prefix="/api")
    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/admin/rental-contracts/{contract_id}/status"
               and "PATCH" in getattr(r, "methods", set())]
    assert len(matches) == 2
    assert matches[0].name == "secure_update_contract_status"
    assert matches[1].name == "update_contract_status"


def test_server_mounts_security_shim_before_contracts_router():
    source = Path("server.py").read_text()
    pre = 'app.include_router(auth_metrics_router, prefix="/api")'
    legacy = 'app.include_router(contracts_router, prefix="/api")'
    assert source.count(pre) == 1 and source.count(legacy) == 1
    assert source.index(pre) < source.index(legacy)


def test_lifecycle_release_is_contract_bound_and_fail_closed():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    assert 'detail="lease_unit_owned_by_other_contract"' in source
    assert '"current_contract_id": contract_id' in source
    assert 'detail="lease_property_owned_by_other_contract"' in source
    assert 'detail="lease_tenant_property_changed"' in source
    assert '"status": old_status, "lifecycle_claim_id": claim_id' in source
    assert 'detail="lease_status_changed"' in source


def test_lifecycle_serializes_before_projection_mutation():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    claim_call = 'claim_id = await _claim_lifecycle(contract_oid, old_status, new_status)'
    unit_call = 'await mark_unit_rented('
    property_call = 'claim = await db.properties.update_one('
    assert claim_call in source
    assert source.index(claim_call) < source.rindex(unit_call)
    assert source.index(claim_call) < source.rindex(property_call)
    assert 'detail="lease_lifecycle_busy_or_changed"' in source
    assert '"lifecycle_claim_id": claim_id' in source


def test_release_checks_other_active_tenant_contract_before_clearing():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    assert '"tenant_id": tenant_id, "status": "active"' in source
    assert '"_id": {"$ne": ObjectId(contract_id)}' in source
    assert 'if other:' in source


def test_activation_rejects_second_active_tenant_contract():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    assert 'detail="lease_tenant_already_active_elsewhere"' in source
    assert 'detail="lease_tenant_occupancy_changed"' in source
    assert '"pending_activation"' in source


def test_activation_property_claim_uses_compare_and_set():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    assert 'detail="lease_property_occupancy_changed"' in source
    assert '{"current_contract_id": contract_id}' in source
    assert '{"current_contract_id": {"$exists": False}}' in source
