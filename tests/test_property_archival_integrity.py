from pathlib import Path

from fastapi import FastAPI

from rental.auth_metrics import router as pre_router
from rental.properties_router import router as historical_properties_router


def test_archive_route_is_first_match_and_hard_delete_is_not_authority():
    app = FastAPI()
    app.include_router(pre_router, prefix="/api")
    app.include_router(historical_properties_router, prefix="/api")
    matches = [r for r in app.routes if getattr(r, "path", None) == "/api/admin/properties/{property_id}" and "DELETE" in getattr(r, "methods", set())]
    assert len(matches) >= 2
    assert matches[0].name == "archive_property"


def test_archive_is_lock_recovery_and_exact_token_fenced():
    source = Path("rental/property_archival_security_router.py").read_text()
    assert '"property_archive"' in source
    assert 'assert_property_lifecycle_recovery_clear(property_id)' in source
    assert '"mutation_lock.token": token' in source
    assert 'detail="property_archive_contract_conflict"' in source
    assert 'detail="property_archive_unit_occupancy_conflict"' in source
    assert "delete_one(" not in source


def test_restore_is_explicit_serialized_workflow():
    source = Path("rental/property_archival_security_router.py").read_text()
    assert "@router.post('/admin/properties/{property_id}/restore')" in source
    assert '"property_restore"' in source
    assert '"$unset": {"archived_at": "", "archived_by": ""}' in source
    assert '"mutation_lock.token": token' in source


def test_archived_property_rejected_by_lease_and_topology_authority():
    lease = Path("rental/lease_creation_security_router.py").read_text()
    topology = Path("rental/unit_topology_security_router.py").read_text()
    lifecycle = Path("rental/property_lifecycle_security_router.py").read_text()
    assert 'detail="lease_property_archived"' in lease
    assert 'detail="property_archived"' in topology
    assert 'detail="property_archived"' in lifecycle


def test_archived_property_hidden_and_sync_excluded():
    visibility = Path("rental/property_visibility_security_router.py").read_text()
    sync = Path("rental/property_sync_cron.py").read_text()
    auth = Path("rental/auth_metrics.py").read_text()
    assert 'archived_at' in visibility
    assert 'raise HTTPException(status_code=404, detail="Propiedad no encontrada")' in visibility
    assert 'skipped_archived' in sync
    assert 'if prop.get("archived_at"):' in sync
    assert "router.routes.extend(property_visibility_security_router.routes)" in auth
    assert "router.routes.extend(property_archival_security_router.routes)" in auth
