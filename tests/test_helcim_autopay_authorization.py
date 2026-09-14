"""Durable, idempotent Helcim autopay authorization contract."""
import asyncio
from datetime import datetime, timezone
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
        self.updates = []

    async def find_one(self, query):
        self.find_queries.append(query)
        return self.found

    async def update_one(self, query, update, upsert=False):
        self.update = (query, update, upsert)
        self.updates.append(self.update)
        return self.result


class _DB:
    def __init__(self, method=None, existing=None, result=None):
        self.helcim_saved_methods = _Collection(method)
        self.autopay_config = _Collection(existing, result)
        self.autopay_notification_deliveries = _Collection()


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
        return {"_id": "tenant-123", "name": "Tenant One",
                "email": "tenant@example.com", "locale": "es"}
    async def send_email(_db, **_kwargs):
        return True
    monkeypatch.setattr(vault, "auth_tenant_flex", auth)
    monkeypatch.setattr(vault, "get_db", lambda: db)
    monkeypatch.setattr(vault, "send_autopay_changed_email", send_email)


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
                        "authorization_id": REQUEST_ID, "duplicate": False,
                        "email_confirmation_sent": True}
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
    assert db.autopay_notification_deliveries.update is None


def test_revocation_records_event_and_removes_charge_credentials(monkeypatch):
    db = _DB(existing={"_id": "helcim:tenant-123", "enabled": True,
                       "helcim_method_id": METHOD_ID, "day_of_month": 5})
    _install(monkeypatch, db)

    response = _run(vault.authorize_autopay(_Request(_payload(
        enabled=False, accepted=False, method_id=""))))

    assert response["enabled"] is False
    assert response["email_confirmation_sent"] is True
    _, update, _ = db.autopay_config.update
    assert update["$push"]["authorization_history"]["action"] == "revoked"
    assert set(update["$unset"]) == {
        "helcim_method_id", "helcim_card_token", "helcim_customer_code",
        "helcim_method_type", "helcim_customer_id", "helcim_bank_account_id"}


def test_enabled_schedule_change_sends_one_update_email(monkeypatch):
    existing = {"_id": "helcim:tenant-123", "enabled": True,
                "helcim_method_id": METHOD_ID, "day_of_month": 3}
    db = _DB(method={"_id": ObjectId(METHOD_ID), "tenant_id": "tenant-123",
                    "brand": "Mastercard", "last4": "9931", "type": "card"},
             existing=existing)
    calls = []
    _install(monkeypatch, db)

    async def send_email(_db, **kwargs):
        calls.append(kwargs)
        return True
    monkeypatch.setattr(vault, "send_autopay_changed_email", send_email)

    response = _run(vault.authorize_autopay(_Request(_payload(day_of_month=5))))

    assert response["email_confirmation_sent"] is True
    assert len(calls) == 1
    assert calls[0]["change_type"] == "updated"
    assert calls[0]["method_brand"] == "Mastercard"
    assert calls[0]["method_last4"] == "9931"
    audit = db.autopay_notification_deliveries.update[1]["$setOnInsert"]
    assert audit["status"] == "sent"
    assert audit["authorization_request_id"] == REQUEST_ID
    assert "method" not in audit


def test_email_failure_does_not_rollback_authorization(monkeypatch):
    db = _DB(method={"_id": ObjectId(METHOD_ID), "tenant_id": "tenant-123",
                    "brand": "Visa", "last4": "1234", "type": "card"})
    _install(monkeypatch, db)

    async def fail_email(_db, **_kwargs):
        return False
    monkeypatch.setattr(vault, "send_autopay_changed_email", fail_email)

    response = _run(vault.authorize_autopay(_Request(_payload())))

    assert response["success"] is True
    assert response["enabled"] is True
    assert response["email_confirmation_sent"] is False
    audit = db.autopay_notification_deliveries.update[1]["$setOnInsert"]
    assert audit["status"] == "failed"
    assert "sent_at" not in audit


def test_masked_localized_email_contains_no_sensitive_payment_data():
    from rental.security_email import build_autopay_changed_message

    content = build_autopay_changed_message(
        name="Tenant <One>", change_type="activated", method_brand="Mastercard",
        method_last4="9931", day_of_month=5,
        changed_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc), locale="es")

    combined = " ".join(content.values())
    assert "Pagos automáticos activados" in combined
    assert "día 5 de cada mes" in combined
    assert "Mastercard ••••9931" in combined
    assert "Tenant &lt;One&gt;" in content["html"]
    assert "507f1f77" not in combined
    assert "token" not in combined.lower()


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
        "helcim_method_id", "helcim_card_token", "helcim_customer_code",
        "helcim_method_type", "helcim_customer_id", "helcim_bank_account_id"}
    event = update["$push"]["authorization_history"]
    assert event == update["$set"]["authorization"]
    assert event["action"] == "revoked"
    assert event["accepted"] is False
    assert event["source"] == "legacy_endpoint"
    assert "token" not in repr(event)
    assert db.helcim_saved_methods.find_queries == []
