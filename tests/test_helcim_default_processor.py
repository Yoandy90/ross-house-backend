import asyncio

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
