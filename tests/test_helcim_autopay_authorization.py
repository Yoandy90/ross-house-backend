"""Durable, idempotent Helcim autopay authorization contract."""
import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId

from rental import helcim_vault_router as vault


METHOD_ID = "507f1f77bcf86cd799439011"
REQUEST_ID = "a" * 32


class _Collection:
    def __init__(self, found=None, result=None):
        self.found = found
        self.result = result or SimpleNamespace(modified_count=1, upserted_id=None)
        self.find_queries = []
        self.update = None

    async def find_one(self, query):
        self.find_queries.append(query)
        return self.found

    async def update_one(self, query, update, upsert=False):
        self.update = (query, update, upsert)
        return self.result


class _DB:
    def __init__(self, method=None, existing=None, result=None):
        self.helcim_saved_methods = _Collection(method)
        self.autopay_config = _Collection(existing, result)


class _Request:
    def __init__(self, body):
        self._body = body
        self.headers = {"user-agent": "Ross House iOS"}
        self.client = SimpleNamespace(host="192.0.2.10")

    async def json(self):
        return self._body


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch, db):
    async def auth(_request):
        return {"_id": "tenant-123"}
    monkeypatch.setattr(vault, "auth_tenant_flex", auth)
    monkeypatch.setattr(vault, "get_db", lambda: db)


def _payload(**changes):
    value = {"enabled": True, "accepted": True, "method_id": METHOD_ID,
             "day_of_month": 5,
             "authorization_version": vault.AUTOPAY_AUTHORIZATION_VERSION,
             "authorization_request_id": REQUEST_ID}
    value.update(changes)
    return value


def test_authorization_is_atomic_append_only_and_contains_no_payment_token(monkeypatch):
    db = _DB(method={"_id": ObjectId(METHOD_ID), "tenant_id": "tenant-123",
                    "card_token": "secret-card-token", "customer_code": "secret-customer",
                    "brand": "Visa", "last4": "1234", "type": "card"})
    _install(monkeypatch, db)

    response = _run(vault.authorize_autopay(_Request(_payload())))

    assert response == {"success": True, "enabled": True,
                        "authorization_id": REQUEST_ID, "duplicate": False}
    query, update, upsert = db.autopay_config.update
    assert upsert is True
    assert query == {"_id": "helcim:tenant-123",
                     "authorization_history.authorization_request_id": {"$ne": REQUEST_ID}}
    event = update["$push"]["authorization_history"]
    assert event["action"] == "authorized"
    assert event["scope"] == "monthly_canonical_rent_balance"
    assert event["method_last4"] == "1234"
    assert len(event["ip_hash"]) == 32
    assert "token" not in repr(event).lower()
    assert "customer" not in repr(event).lower()
    assert update["$set"]["helcim_card_token"] == "secret-card-token"


@pytest.mark.parametrize("change,status", [
    ({"accepted": False}, 400),
    ({"authorization_version": "old"}, 409),
    ({"authorization_request_id": "bad"}, 400),
    ({"enabled": "yes"}, 400),
])
def test_invalid_or_non_explicit_authorization_fails_closed(monkeypatch, change, status):
    db = _DB(method={"_id": ObjectId(METHOD_ID), "tenant_id": "tenant-123"})
    _install(monkeypatch, db)
    with pytest.raises(vault.HTTPException) as exc:
        _run(vault.authorize_autopay(_Request(_payload(**change))))
    assert exc.value.status_code == status
    assert db.autopay_config.update is None


def test_duplicate_request_returns_original_state_without_second_append(monkeypatch):
    db = _DB(existing={"enabled": False, "authorization_history": [
        {"authorization_request_id": REQUEST_ID, "action": "authorized"}]})
    _install(monkeypatch, db)

    response = _run(vault.authorize_autopay(_Request(_payload())))

    assert response["duplicate"] is True
    assert response["enabled"] is True
    assert response["authorization_id"] == REQUEST_ID
    assert db.autopay_config.update is None


def test_revocation_records_event_and_removes_charge_credentials(monkeypatch):
    db = _DB()
    _install(monkeypatch, db)

    response = _run(vault.authorize_autopay(_Request(_payload(
        enabled=False, accepted=False, method_id=""))))

    assert response["enabled"] is False
    _, update, _ = db.autopay_config.update
    assert update["$push"]["authorization_history"]["action"] == "revoked"
    assert set(update["$unset"]) == {
        "helcim_method_id", "helcim_card_token", "helcim_customer_code"}


@pytest.mark.parametrize("enabled,status", [
    (True, 409), ("false", 400), ("true", 400), (1, 400), (0, 400), (None, 400),
])
def test_legacy_endpoint_cannot_enable_without_authorization(monkeypatch, enabled, status):
    db = _DB()
    _install(monkeypatch, db)
    with pytest.raises(vault.HTTPException) as exc:
        _run(vault.set_autopay(_Request({"enabled": enabled, "method_id": METHOD_ID})))
    assert exc.value.status_code == status
    assert db.autopay_config.update is None
    assert db.helcim_saved_methods.find_queries == []


def test_legacy_revocation_clears_credentials_and_preserves_schedule(monkeypatch):
    db = _DB()
    _install(monkeypatch, db)
    writes = []

    async def update_many(query, update):
        writes.append((query, update))
        return SimpleNamespace(modified_count=1)

    db.autopay_config.update_many = update_many
    response = _run(vault.set_autopay(_Request({
        "enabled": False, "day_of_month": "invalid", "method_id": "invalid"})))
    assert response == {"success": True, "enabled": False}
    query, update = writes[0]
    assert query == {"user_id": "tenant-123", "processor": "helcim"}
    assert update["$set"]["enabled"] is False
    assert "day_of_month" not in update["$set"]
    assert set(update["$unset"]) == {
        "helcim_method_id", "helcim_card_token", "helcim_customer_code"}
    event = update["$push"]["authorization_history"]
    assert event == update["$set"]["authorization"]
    assert event["action"] == "revoked"
    assert event["accepted"] is False
    assert event["source"] == "legacy_endpoint"
    assert "token" not in repr(event)
    assert db.helcim_saved_methods.find_queries == []
