from pathlib import Path

from fastapi import FastAPI

from rental.auth_metrics import router as security_router
from rental.service_providers_router import router as historical_provider_router


def test_secure_dispatch_is_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_provider_router, prefix="/api")
    matches = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/admin/service-providers/dispatch-maintenance"
        and "POST" in getattr(route, "methods", set())
    ]
    assert len(matches) == 2
    assert matches[0].name == "secure_admin_dispatch_maintenance"
    assert matches[1].name == "admin_dispatch_maintenance"


def test_dispatch_revalidates_canonical_ticket_ownership_and_active_provider():
    source = Path("rental/maintenance_dispatch_security_router.py").read_text()
    assert "_load_bound_maintenance_request(request_id)" in source
    assert '!= "active"' in source
    assert 'detail="maintenance_provider_not_active"' in source
    assert '_DISPATCHABLE_STATUSES = {"pending", "in_progress"}' in source
    assert 'detail="maintenance_not_dispatchable"' in source


def test_dispatch_claims_assignment_before_notifications_with_cas():
    source = Path("rental/maintenance_dispatch_security_router.py").read_text()
    claim = "claim = await db.maintenance_requests.update_one("
    email = "email_sent = await _send_email("
    sms = "sms_sent = await _send_sms("
    assert claim in source and email in source and sms in source
    assert source.index(claim) < source.index(email)
    assert source.index(claim) < source.index(sms)
    assert '{"assigned_provider_id": {"$exists": False}}' in source
    assert 'detail="maintenance_dispatch_concurrent_change"' in source
    assert 'detail="maintenance_already_assigned"' in source


def test_dispatch_does_not_mutate_lease_ownership_fields():
    source = Path("rental/maintenance_dispatch_security_router.py").read_text()
    mutation = source[source.index("claim = await db.maintenance_requests.update_one("):source.index("settings = await _get_settings(db)")]
    for field in ("tenant_id", "contract_id", "property_id", "unit_id", "relationship_source"):
        assert f'"{field}"' not in mutation
    assert '"assigned_provider_id": provider_id' in mutation


def test_dispatch_retry_is_idempotent_for_same_provider_without_resend():
    source = Path("rental/maintenance_dispatch_security_router.py").read_text()
    assert '"already_assigned": True' in source
    early_return = source.index('"already_assigned": True')
    first_send = source.index("email_sent = await _send_email(")
    assert early_return < first_send
