from pathlib import Path

from fastapi import FastAPI

from rental.auth_metrics import router as pre_contract_router
from rental.contracts_router import router as historical_contracts_router


def test_secure_create_contract_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_contract_router, prefix="/api")
    app.include_router(historical_contracts_router, prefix="/api")
    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/admin/rental-contracts"
               and "POST" in getattr(r, "methods", set())]
    assert len(matches) == 2
    assert matches[0].name == "secure_create_rental_contract"
    assert matches[1].name == "create_contract"


def test_creation_cannot_bypass_activation_or_client_identity_metadata():
    source = Path("rental/lease_creation_security_router.py").read_text()
    assert 'detail="lease_creation_cannot_bypass_activation"' in source
    assert 'tenant.get("name", "")' in source
    assert 'tenant.get("email", "")' in source
    assert 'prop.get("address")' in source
    assert 'relationship_source": "canonical_records"' in source


def test_unit_and_landlord_must_match_property_authority():
    source = Path("rental/lease_creation_security_router.py").read_text()
    assert 'detail="lease_unit_property_mismatch"' in source
    assert 'detail="lease_unit_already_claimed"' in source
    assert 'detail="lease_landlord_property_owner_mismatch"' in source


def test_financial_terms_are_bounded_before_insert():
    source = Path("rental/lease_creation_security_router.py").read_text()
    assert 'not 1 <= due_day <= 31' in source
    assert 'rent_amount < 0' in source
    assert 'deposit_amount < 0' in source
    assert 'detail="lease_financial_terms_invalid"' in source
