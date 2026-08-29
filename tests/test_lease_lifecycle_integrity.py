from pathlib import Path

from fastapi import FastAPI

from rental.auth_metrics import router as pre_contract_router
from rental.contracts_router import router as historical_contracts_router


def test_secure_status_transition_guard_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_contract_router, prefix="/api")
    app.include_router(historical_contracts_router, prefix="/api")
    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/admin/rental-contracts/{contract_id}/status"
               and "PATCH" in getattr(r, "methods", set())]
    assert len(matches) == 3
    assert matches[0].name == "guarded_update_contract_status"
    assert matches[1].name == "secure_update_contract_status"
    assert matches[2].name == "update_contract_status"


def test_server_mounts_security_shim_before_contracts_router():
    source = Path("server.py").read_text()
    pre = 'app.include_router(auth_metrics_router, prefix="/api")'
    legacy = 'app.include_router(contracts_router, prefix="/api")'
    assert source.count(pre) == 1 and source.count(legacy) == 1
    assert source.index(pre) < source.index(legacy)


def test_lifecycle_transition_graph_forbids_skip_activation_and_reopen():
    source = Path("rental/lease_lifecycle_state_guard_router.py").read_text()
    assert '"pending_activation": {"draft", "active"}' in source
    assert '"active": {"terminated", "expired"}' in source
    assert '"terminated": set()' in source
    assert '"expired": set()' in source
    assert 'detail="lease_status_transition_invalid"' in source
    assert 'detail="lease_force_activation_forbidden"' in source
    assert 'detail="lease_signatures_required"' in source


def test_lifecycle_release_is_contract_bound_and_fail_closed():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    assert 'detail="lease_unit_owned_by_other_contract"' in source
    assert '"current_contract_id": contract_id' in source
    assert 'detail="lease_property_owned_by_other_contract"' in source
    assert 'detail="lease_tenant_contract_changed"' in source
    assert 'detail="lease_tenant_property_changed"' in source
    assert 'detail="lease_tenant_unit_changed"' in source
    assert '"status": old_status, "lifecycle_claim_id": claim_id' in source
    assert 'detail="lease_status_changed"' in source


def test_lifecycle_serializes_before_projection_mutation():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    claim_call = 'claim_id = await _claim_lifecycle(contract_oid, old_status, new_status)'
    tenant_call = 'await _claim_tenant_occupancy(contract, contract_id, now)'
    unit_call = 'await mark_unit_rented('
    property_call = 'claim = await db.properties.update_one('
    assert claim_call in source
    assert tenant_call in source
    assert source.index(claim_call) < source.rindex(tenant_call)
    assert source.rindex(tenant_call) < source.rindex(unit_call)
    assert source.rindex(tenant_call) < source.rindex(property_call)
    assert 'detail="lease_lifecycle_busy_or_changed"' in source
    assert '"lifecycle_claim_id": claim_id' in source


def test_tenant_contract_claim_closes_double_activation_race():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    start = source.index("async def _claim_tenant_occupancy")
    end = source.index("@router.patch('/admin/rental-contracts/{contract_id}/status')")
    claim = source[start:end]
    assert '"current_contract_id": contract_id' in claim
    assert '{"current_contract_id": None}' in claim
    assert '{"current_contract_id": {"$exists": False}}' in claim
    assert 'detail="lease_tenant_already_claimed"' in claim
    assert 'detail="lease_tenant_occupancy_changed"' in claim


def test_release_clears_tenant_contract_projection_only_when_owned():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    start = source.index("async def _release_tenant")
    end = source.index("async def _claim_lifecycle")
    release = source[start:end]
    assert '"current_contract_id": contract_id' in release
    assert '"current_contract_id": None' in release
    assert 'detail="lease_tenant_contract_changed"' in release


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


def test_failed_transition_retains_claim_instead_of_blind_retry():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    assert "async def _clear_claim" not in source
    except_block = source[source.index("    except Exception:"):]
    assert "_clear_claim" not in except_block
    assert "Fail closed" in except_block


def test_recovery_inspector_is_read_only_and_exact_claim_bound():
    source = Path("rental/lease_lifecycle_recovery_router.py").read_text()
    assert "@router.get('/admin/rental-contracts/{contract_id}/lifecycle-recovery/{claim_id}')" in source
    assert 'detail="lease_lifecycle_claim_mismatch"' in source
    assert '"read_only": True' in source
    assert '"automatic_retry_allowed": False' in source
    assert "update_one(" not in source
    assert "insert_one(" not in source
    assert "delete_one(" not in source


def test_recovery_has_explicit_partial_failure_classifications():
    source = Path("rental/lease_lifecycle_recovery_router.py").read_text()
    for state in (
        "result_recorded",
        "projection_applied_status_missing",
        "no_projection_detected",
        "partial_projection",
        "ambiguous_state",
    ):
        assert state in source
    assert '"other_contract"' in source
    assert '"different_projection"' in source
    assert 'tenant.get("current_contract_id")' in source


def test_recovery_router_and_transition_guard_are_mounted_in_security_shim():
    source = Path("rental/auth_metrics.py").read_text()
    guard = "router.routes.extend(lease_lifecycle_state_guard_router.routes)"
    lifecycle = "router.routes.extend(lease_lifecycle_security_router.routes)"
    assert "lease_lifecycle_recovery_router" in source
    assert "router.routes.extend(lease_lifecycle_recovery_router.routes)" in source
    assert guard in source and lifecycle in source
    assert source.index(guard) < source.index(lifecycle)
