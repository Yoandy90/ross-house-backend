from pathlib import Path


def test_lifecycle_core_forbids_force_activation_without_relying_on_outer_guard():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    start = source.index("async def secure_update_contract_status")
    body = source[start:]

    force_check = 'if bool(data.get("force_activate", False)):'
    force_error = 'detail="lease_force_activation_forbidden"'
    signature_error = 'detail="lease_signatures_required"'
    claim_call = 'claim_id = await _claim_lifecycle(contract_oid, old_status, new_status)'

    assert 'if not isinstance(data, dict):' in body
    assert 'detail="lease_status_payload_invalid"' in body
    assert force_check in body
    assert force_error in body
    assert signature_error in body
    assert body.index(force_check) < body.index(signature_error)
    assert body.index(force_error) < body.index(claim_call)


def test_lifecycle_core_contains_no_force_activation_signature_bypass():
    source = Path("rental/lease_lifecycle_security_router.py").read_text()
    start = source.index('    if new_status == "active":')
    end = source.index("\n    claim_id = await _claim_lifecycle", start)
    activation = source[start:end]

    assert 'if not bool(data.get("force_activate", False))' not in activation
    assert 'if bool(data.get("force_activate", False)):' in activation
    assert 'raise HTTPException(status_code=400, detail="lease_force_activation_forbidden")' in activation
