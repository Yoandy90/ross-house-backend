import asyncio

import pytest

from rental import payment_processors_core as core


class _RentalConfig:
    async def find_one(self, _query):
        return None


class _Database:
    rental_config = _RentalConfig()


def test_new_payment_configuration_defaults_to_helcim(monkeypatch):
    monkeypatch.setattr(core, "get_db", lambda: _Database())

    document = asyncio.run(core._get_doc())

    assert document["active_processor"] == "helcim"
    assert document["processors"]["helcim"]["environment"] == "sandbox"


def test_public_and_masked_views_default_to_helcim(monkeypatch):
    monkeypatch.setattr(core, "_public_base_url", lambda: "https://staging.example")

    view = core._masked_view({"processors": {}, "three_ds": {}})

    assert view["active_processor"] == "helcim"
    assert view["processors"]["helcim"]["webhook_endpoint"].endswith(
        "/api/webhooks/hpay"
    )

def test_public_base_url_prefers_explicit_configuration(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.test/")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "service.up.railway.app")

    assert core._public_base_url() == "https://api.example.test"


def test_public_base_url_uses_current_railway_service_domain(monkeypatch):
    monkeypatch.delenv("PUBLIC_API_URL", raising=False)
    monkeypatch.setenv(
        "RAILWAY_PUBLIC_DOMAIN",
        "rosshousestaging-staging.up.railway.app",
    )

    assert core._public_base_url() == (
        "https://rosshousestaging-staging.up.railway.app"
    )


def test_public_base_url_does_not_duplicate_railway_scheme(monkeypatch):
    monkeypatch.delenv("PUBLIC_API_URL", raising=False)
    monkeypatch.setenv(
        "RAILWAY_PUBLIC_DOMAIN",
        "https://rosshousestaging-staging.up.railway.app/",
    )

    assert core._public_base_url() == (
        "https://rosshousestaging-staging.up.railway.app"
    )



def test_stripe_activation_is_blocked_without_touching_stored_config(monkeypatch):
    async def fake_auth_admin(_request):
        return {"id": "test-admin"}

    def fail_if_database_is_touched():
        raise AssertionError("Stripe guard must run before reading or writing configuration")

    monkeypatch.setattr(core, "auth_admin", fake_auth_admin)
    monkeypatch.setattr(core, "get_db", fail_if_database_is_touched)

    with pytest.raises(core.HTTPException) as exc:
        asyncio.run(core.activate_processor("stripe", object()))

    assert exc.value.status_code == 409
    assert "Helcim" in exc.value.detail
