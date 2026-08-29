import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

import rental.property_lifecycle_security_router as secure


def run(coro):
    return asyncio.run(coro)


class Request:
    def __init__(self, payload):
        self.payload = payload
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self.payload


async def allow_admin(_request):
    return {"role": "admin", "email": "admin@example.com"}


def test_create_rejects_non_object_payload(monkeypatch):
    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_create_property(Request(["available"]), BackgroundTasks()))
    assert exc.value.status_code == 400
    assert exc.value.detail == "property_payload_invalid"


@pytest.mark.parametrize("status", ["Available", " available", "available ", "MAINTENANCE"])
def test_create_rejects_noncanonical_status_before_historical_write(monkeypatch, status):
    delegated = []

    async def historical(_request, _background_tasks):
        delegated.append(True)
        return {"success": True}

    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "historical_create_property", historical)

    with pytest.raises(HTTPException) as exc:
        run(secure.secure_create_property(Request({"name": "Oak", "status": status}), BackgroundTasks()))
    assert exc.value.status_code == 400
    assert exc.value.detail == "property_status_not_canonical"
    assert delegated == []


def test_create_accepts_canonical_manual_status(monkeypatch):
    seen = []

    async def historical(request, _background_tasks):
        seen.append(await request.json())
        return {"success": True}

    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "historical_create_property", historical)

    result = run(secure.secure_create_property(
        Request({"name": "Oak", "status": "maintenance"}),
        BackgroundTasks(),
    ))
    assert result == {"success": True}
    assert seen == [{"name": "Oak", "status": "maintenance"}]


def test_create_without_status_preserves_historical_default(monkeypatch):
    seen = []

    async def historical(request, _background_tasks):
        seen.append(await request.json())
        return {"success": True}

    monkeypatch.setattr(secure, "auth_admin", allow_admin)
    monkeypatch.setattr(secure, "historical_create_property", historical)

    result = run(secure.secure_create_property(Request({"name": "Oak"}), BackgroundTasks()))
    assert result == {"success": True}
    assert seen == [{"name": "Oak"}]


def test_create_still_rejects_rented_and_unknown_status(monkeypatch):
    monkeypatch.setattr(secure, "auth_admin", allow_admin)

    with pytest.raises(HTTPException) as rented:
        run(secure.secure_create_property(Request({"status": "rented"}), BackgroundTasks()))
    assert rented.value.status_code == 409
    assert rented.value.detail == "property_rented_status_lifecycle_managed"

    with pytest.raises(HTTPException) as unknown:
        run(secure.secure_create_property(Request({"status": "unavailable"}), BackgroundTasks()))
    assert unknown.value.status_code == 400
    assert unknown.value.detail == "property_status_invalid"
