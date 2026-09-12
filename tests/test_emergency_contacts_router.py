import asyncio

from rental import emergency_contacts_router as emergency


EXPECTED_PHONES = {
    "911",
    "8069353998",
    "8069356435",
    "8069354145",
    "8069357171",
    "18002221222",
    "18008951999",
    "18663228667",
    "8069342018",
}


def test_verified_defaults_are_complete_and_ordered():
    contacts = emergency.VERIFIED_DEFAULT_CONTACTS

    assert [contact["order"] for contact in contacts] == sorted(
        contact["order"] for contact in contacts
    )
    assert {contact["phone"] for contact in contacts} == EXPECTED_PHONES
    assert all(contact["is_active"] for contact in contacts)
    assert all(contact["name_es"] and contact["name_en"] for contact in contacts)


def test_endpoint_falls_back_when_database_is_unavailable(monkeypatch):
    from rental import shared

    def unavailable_db():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(shared, "get_db", unavailable_db)

    payload = asyncio.run(emergency.get_public_emergency_contacts())

    assert payload["success"] is True
    assert payload["total"] == len(EXPECTED_PHONES)
    assert {contact["phone"] for contact in payload["contacts"]} == EXPECTED_PHONES


def test_public_route_contract():
    route = next(route for route in emergency.router.routes if route.path == "")

    assert "GET" in route.methods
    assert emergency.router.prefix == "/public/emergency-contacts"
