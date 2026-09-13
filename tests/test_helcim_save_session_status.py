"""Security and response-contract tests for Helcim save-session polling."""
import asyncio

import pytest

from rental import helcim_vault_router as vault
from rental import payment_processors_core as processors


class _Sessions:
    def __init__(self, document):
        self.document = document
        self.query = None

    async def find_one(self, query):
        self.query = query
        if not self.document:
            return None
        if any(self.document.get(key) != value for key, value in query.items()):
            return None
        return self.document


class _Database:
    def __init__(self, document):
        self.helcim_checkout_sessions = _Sessions(document)


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch, document):
    database = _Database(document)

    async def authenticated_tenant(_request):
        return {"_id": "tenant-123"}

    monkeypatch.setattr(vault, "auth_tenant_flex", authenticated_tenant)
    monkeypatch.setattr(vault, "get_db", lambda: database)
    return database


def test_verified_status_is_tenant_scoped_and_exposes_no_vault_secrets(monkeypatch):
    database = _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
        "status": "verified",
        "checkout_token": "checkout-secret",
        "secret_token": "hash-secret",
        "helcim_transaction": {"cardToken": "card-secret"},
    })

    result = _run(vault.save_method_session_status("session-abc", object()))

    assert database.helcim_checkout_sessions.query == {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
    }
    assert result == {
        "success": True,
        "session_id": "session-abc",
        "status": "verified",
        "method_saved": True,
    }
    assert "secret" not in repr(result).lower()
    assert "token" not in repr(result).lower()


@pytest.mark.parametrize("status", [None, "pending"])
def test_unfinished_session_does_not_claim_method_saved(monkeypatch, status):
    _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
        "status": status,
    })

    result = _run(vault.save_method_session_status("session-abc", object()))

    assert result["status"] == "pending"
    assert result["method_saved"] is False


def test_other_tenant_or_non_verify_session_is_not_disclosed(monkeypatch):
    _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-other",
        "purpose": "payment",
        "status": "verified",
    })

    with pytest.raises(vault.HTTPException) as exc:
        _run(vault.save_method_session_status("session-abc", object()))

    assert exc.value.status_code == 404


def test_unknown_internal_state_fails_closed(monkeypatch):
    _install(monkeypatch, {
        "_id": "session-abc",
        "tenant_id": "tenant-123",
        "purpose": "verify",
        "status": "unexpected-provider-state",
    })

    result = _run(vault.save_method_session_status("session-abc", object()))

    assert result["status"] == "failed"
    assert result["method_saved"] is False


def test_payment_public_base_url_prefers_branded_domain(monkeypatch):
    monkeypatch.setenv("PAYMENTS_PUBLIC_BASE_URL", "https://payments.rosshouserentals.com/")
    monkeypatch.setenv("PUBLIC_API_URL", "https://rosshousestaging-staging.up.railway.app")

    assert processors._public_base_url() == "https://payments.rosshouserentals.com"


def test_helcim_bridge_is_branded_and_never_claims_to_store_raw_data():
    page = processors._HELCIM_BRIDGE_TEMPLATE

    assert "Ross House Rentals" in page
    assert "Pago seguro protegido por Helcim" in page
    assert "Nunca almacenamos el número completo" in page
    assert "secure.helcim.app/helcim-pay/services/start.js" in page
    assert "appendHelcimPayIframe(checkoutToken, true)" in page


def test_customer_request_uses_profile_and_hides_no_payment_data():
    customer = vault._helcim_customer_request({
        "name": "Prueba Inquilino",
        "email": "tenant@example.com",
        "phone": "(806) 555-0199",
        "address": "999 Staging Test Ave, Dumas, TX 79029",
    })

    assert customer == {
        "contactName": "Prueba Inquilino",
        "cellPhone": "8065550199",
        "billingAddress": {
            "name": "Prueba Inquilino",
            "street1": "999 Staging Test Ave, Dumas, TX 79029",
            "postalCode": "79029",
            "email": "tenant@example.com",
            "phone": "8065550199",
        },
    }
    assert "card" not in repr(customer).lower()
    assert "cvv" not in repr(customer).lower()


def test_customer_request_omits_incomplete_billing_address():
    customer = vault._helcim_customer_request({
        "name": "Tenant",
        "email": "tenant@example.com",
        "phone": "806-555-0199",
        "address": "Address without postal code",
    })

    assert customer == {"contactName": "Tenant", "cellPhone": "8065550199"}


def test_existing_helcim_customer_is_reused_instead_of_duplicated():
    class _Methods:
        async def find_one(self, query, sort=None):
            assert query["tenant_id"] == "tenant-123"
            assert sort == [("created_at", -1)]
            return {"customer_code": "CST1001"}

    class _Db:
        helcim_saved_methods = _Methods()

    context = _run(vault._helcim_customer_context(
        _Db(), {"_id": "tenant-123", "name": "Tenant"}))

    assert context == {"customerCode": "CST1001"}
