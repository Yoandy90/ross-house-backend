from rental.lease_signature_state import effective_lease_status, lease_has_required_signatures


def test_fully_signed_active_contract_stays_active():
    contract = {"status": "active", "tenant_signature": "tenant", "admin_signature": "admin"}
    assert lease_has_required_signatures(contract) is True
    assert effective_lease_status(contract) == "active"


def test_active_without_signatures_is_reported_as_pending_signatures():
    contract = {"status": "active", "tenant_signature": None, "admin_signature": None}
    assert lease_has_required_signatures(contract) is False
    assert effective_lease_status(contract) == "pending_signatures"


def test_effective_state_identifies_missing_party():
    assert effective_lease_status(
        {"status": "active", "admin_signature": "admin"}
    ) == "pending_tenant"
    assert effective_lease_status({
        "status": "active", "tenant_signature": "tenant", "landlord_id": "owner",
    }) == "pending_landlord"


def test_non_active_states_are_not_rewritten():
    assert effective_lease_status({"status": "terminated"}) == "terminated"
