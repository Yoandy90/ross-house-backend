from fastapi import FastAPI

from rental.auth_metrics import router as security_router
from rental.tenant_router import router as historical_tenant_router


def test_secure_receipt_route_is_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    matches = [r for r in app.routes
               if getattr(r, "path", None) == "/api/tenant/payment/{payment_id}/receipt"
               and "GET" in getattr(r, "methods", set())]

    assert len(matches) == 2
    assert matches[0].name == "secure_tenant_payment_receipt"
    assert matches[1].name == "tenant_payment_receipt"


def test_receipt_authority_is_canonical_tenant_and_contract_bound():
    source = open("rental/tenant_receipt_security_router.py", encoding="utf-8").read()
    assert "resolve_authenticated_tenant(user)" in source
    assert 'str(payment.get("tenant_id") or "") != tenant_id' in source
    assert 'str(contract.get("tenant_id") or "") != tenant_id' in source
    assert 'receipt_payment_tenant_mismatch' in source
    assert 'receipt_contract_tenant_mismatch' in source
    assert 'receipt_payment_property_mismatch' in source


def test_receipt_route_has_no_client_financial_authority():
    source = open("rental/tenant_receipt_security_router.py", encoding="utf-8").read()
    assert "update_one(" not in source
    assert "insert_one(" not in source
    assert "delete_one(" not in source
    assert "request.json()" not in source
