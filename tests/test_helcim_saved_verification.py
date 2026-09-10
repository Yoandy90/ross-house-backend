"""Synthetic vault flows: no real provider, database, credentials or charges."""
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rental import helcim_saved_verification as verification
from rental import helcim_vault_router as vault
from rental import payment_processors_router as public


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.rows)
        except StopIteration:
            raise StopAsyncIteration


class Collection:
    def __init__(self, rows=()):
        self.rows = {row["_id"]: copy.deepcopy(row) for row in rows}
        self.writes = 0
        self.fail_once = False

    def matching(self, query):
        return [r for r in self.rows.values() if all(r.get(k) == v for k, v in query.items())]

    async def find_one(self, query):
        rows = self.matching(query)
        return copy.deepcopy(rows[0]) if rows else None

    def find(self, query):
        return Cursor(self.matching(query))

    async def insert_one(self, row):
        self.rows[row["_id"]] = copy.deepcopy(row)
        self.writes += 1

    async def update_one(self, query, update, upsert=False):
        await asyncio.sleep(0)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("synthetic interrupted session write")
        rows = self.matching(query)
        if not rows and upsert:
            row = {**query, **copy.deepcopy(update.get("$setOnInsert", {}))}
            self.rows[row["_id"]] = row
            rows = [row]
        for row in rows:
            row.update(copy.deepcopy(update.get("$set", {})))
        self.writes += 1
        return SimpleNamespace(modified_count=len(rows))


def session(kind="ach"):
    return {"_id": "a" * 32, "tenant_id": "tenant-one", "purpose": "verify",
            "payment_method": kind, "amount_cents": 0, "status": "pending",
            "created_at": datetime.now(timezone.utc), "secret_token": "synthetic-secret",
            "checkout_token": "synthetic-checkout"}


def bank_tx(**changes):
    return {"amount": "0.00", "currency": "USD", "bankToken": "synthetic-bank-token",
            "customerCode": "CST_TEST", "bankAccountNumber": "100200300",
            "statusAuth": "PENDING", "statusClearing": "OPENED", **changes}


def signed(ses, tx, wrapped=False):
    encoded = json.dumps(tx, separators=(",", ":"), ensure_ascii=True)
    envelope = {"hash": hashlib.sha256((encoded + ses["secret_token"]).encode()).hexdigest(), "data": tx}
    return json.dumps({"data": envelope} if wrapped else envelope)


def database(ses):
    # No rent ledger or charge collection exists in this fake. Any attempt to
    # access one fails immediately instead of silently simulating a payment.
    return SimpleNamespace(helcim_saved_methods=Collection(),
                           helcim_checkout_sessions=Collection([ses]),
                           autopay_config=Collection())


class Request:
    def __init__(self, body=None, query=None):
        self.payload = body or {}
        self.query_params = query or {}

    async def json(self):
        return self.payload


def authenticate(monkeypatch, db, tenant="tenant-one"):
    async def auth(_request):
        return {"_id": tenant, "name": "Synthetic"}
    monkeypatch.setattr(vault, "auth_tenant_flex", auth)
    monkeypatch.setattr(vault, "get_db", lambda: db)
    monkeypatch.setattr(public, "get_db", lambda: db)


@pytest.mark.asyncio
async def test_bank_save_is_idempotent_and_does_not_authorize_or_charge():
    ses = session()
    db = database(ses)
    raw = signed(ses, bank_tx())
    results = await asyncio.gather(*(verification.complete_saved_verification(db, ses, raw) for _ in range(8)))
    assert all(r == {"status": "verified"} for r in results)
    assert len(db.helcim_saved_methods.rows) == 1
    method = next(iter(db.helcim_saved_methods.rows.values()))
    assert method["type"] == "bank"
    assert method["bank_token"] == "synthetic-bank-token"
    assert method["last4"] == "0300"
    assert method["authorization_status"] == "not_checked"
    assert not ({"card_token", "bankAccountNumber", "routingNumber", "helcim_transaction", "raw_data_response"} & method.keys())
    assert db.autopay_config.writes == 0


@pytest.mark.asyncio
async def test_retry_after_session_write_interruption_reuses_method():
    ses = session()
    db = database(ses)
    db.helcim_checkout_sessions.fail_once = True
    with pytest.raises(RuntimeError):
        await verification.complete_saved_verification(db, ses, signed(ses, bank_tx()))
    await verification.complete_saved_verification(db, ses, signed(ses, bank_tx()))
    assert len(db.helcim_saved_methods.rows) == 1


@pytest.mark.parametrize("wrapped", [True, False])
def test_legacy_card_envelopes_and_unicode(wrapped):
    ses = session("cc")
    ses.pop("payment_method")
    tx = {"amount": "0", "currency": "USD", "status": "APPROVED", "cardToken": "synthetic-card-token",
          "cardNumber": "4000000000001234", "cardHolderName": "José", "cvv": "123", "cardType": "VI"}
    fields = verification.parse_signed_verification(ses, signed(ses, tx, wrapped))
    assert fields["type"] == "card" and fields["last4"] == "1234"
    assert "cvv" not in fields and "cardNumber" not in fields


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"amount": "1"}, {"amount": "NaN"}, {"currency": "CAD"}, {"bankToken": None},
    {"bankToken": {"$ne": None}}, {"statusAuth": "DECLINED"}, {"statusAuth": "CANCELLED"},
    {"statusClearing": "DECLINED"}, {"customerCode": None},
])
async def test_invalid_signed_response_does_not_write(changes):
    ses = session()
    db = database(ses)
    with pytest.raises(HTTPException):
        await verification.complete_saved_verification(db, ses, signed(ses, bank_tx(**changes)))
    assert db.helcim_saved_methods.writes == db.helcim_checkout_sessions.writes == 0


@pytest.mark.asyncio
async def test_forged_hash_expired_or_wrong_purpose_never_saves():
    ses = session()
    db = database(ses)
    for invalid_session, raw in [
        (ses, signed(ses, bank_tx()).replace("0300", "9999")),
        ({**ses, "created_at": datetime.now(timezone.utc) - timedelta(hours=1)}, signed(ses, bank_tx())),
        ({**ses, "purpose": "purchase"}, signed(ses, bank_tx())),
    ]:
        with pytest.raises(HTTPException):
            await verification.complete_saved_verification(db, invalid_session, raw)
    assert db.helcim_saved_methods.writes == 0


@pytest.mark.asyncio
async def test_versioned_listing_hides_banks_from_old_clients_and_never_exposes_tokens(monkeypatch):
    ses = session()
    db = database(ses)
    authenticate(monkeypatch, db)
    await verification.complete_saved_verification(db, ses, signed(ses, bank_tx()))
    legacy = await vault.list_methods(Request())
    assert legacy["methods"] == []
    modern = await vault.list_methods(Request(query={"include_bank": "true"}))
    assert len(modern["methods"]) == 1
    assert modern["methods"][0]["can_pay"] is False
    assert modern["methods"][0]["can_autopay"] is False
    assert "synthetic-bank-token" not in json.dumps(modern)
    assert "CST_TEST" not in json.dumps(modern)
    assert "100200300" not in json.dumps(modern)
    authenticate(monkeypatch, db, "another-tenant")
    assert (await vault.list_methods(Request(query={"include_bank": "true"})))["methods"] == []


@pytest.mark.asyncio
async def test_banks_cannot_enter_card_charge_or_autopay_routes(monkeypatch):
    ses = session()
    db = database(ses)
    authenticate(monkeypatch, db)
    await verification.complete_saved_verification(db, ses, signed(ses, bank_tx()))
    mid = str(next(iter(db.helcim_saved_methods.rows)))
    for route in [vault.pay_with_method, vault.set_autopay]:
        with pytest.raises(HTTPException) as error:
            await route(Request({"enabled": True, "method_id": mid}))
        assert error.value.status_code == 409
    assert db.autopay_config.writes == 0


@pytest.mark.asyncio
async def test_session_status_is_tenant_scoped_and_contains_no_secret(monkeypatch):
    ses = session()
    db = database(ses)
    authenticate(monkeypatch, db)
    assert await vault.save_method_status(ses["_id"], Request()) == {"success": True, "status": "pending"}
    db.helcim_checkout_sessions.rows[ses["_id"]]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert (await vault.save_method_status(ses["_id"], Request()))["status"] == "expired"
    db.helcim_checkout_sessions.rows[ses["_id"]]["status"] = "verified"
    assert (await vault.save_method_status(ses["_id"], Request()))["status"] == "verified"
    authenticate(monkeypatch, db, "another-tenant")
    with pytest.raises(HTTPException) as error:
        await vault.save_method_status(ses["_id"], Request())
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [({}, "cc"), ({"payment_method": "ach"}, "ach")])
async def test_initialize_is_always_zero_dollar_verify(monkeypatch, payload, expected):
    db = database(session())
    authenticate(monkeypatch, db)
    async def config():
        return {"api_token": "synthetic-api-token"}
    monkeypatch.setattr(vault, "_helcim_cfg", config)
    monkeypatch.setattr(public, "_public_base_url", lambda: "https://example.test")
    posted = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            posted.append((url, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: {"checkoutToken": "synthetic-new-checkout", "secretToken": "synthetic-new-secret"})
    monkeypatch.setattr(vault.httpx, "AsyncClient", Client)
    result = await vault.save_method_session(Request(payload))
    assert len(posted) == 1
    url, options = posted[0]
    assert url.endswith("/helcim-pay/initialize")
    assert options["json"]["paymentType"] == "verify"
    assert options["json"]["amount"] == 0
    assert options["json"]["paymentMethod"] == expected
    assert "secret" not in json.dumps(result)
    assert result["session_id"] in db.helcim_checkout_sessions.rows


@pytest.mark.asyncio
async def test_public_route_replaces_only_verification_and_delegates_purchase(monkeypatch):
    route = [r for r in public.router.routes if r.path == "/public/helcim-complete"]
    assert len(route) == 1 and route[0].endpoint is public.helcim_complete
    ses = {**session(), "purpose": "purchase"}
    db = database(ses)
    authenticate(monkeypatch, db)
    calls = []
    async def original(request):
        calls.append(request)
        return {"status": "existing-purchase-handler"}
    monkeypatch.setattr(public.core, "helcim_complete", original)
    result = await public.helcim_complete(Request({"checkout_token": ses["checkout_token"]}))
    assert result["status"] == "existing-purchase-handler" and len(calls) == 1
    db.helcim_checkout_sessions.rows[ses["_id"]]["purpose"] = "verify"
    result = await public.helcim_complete(Request({"checkout_token": ses["checkout_token"], "raw_data_response": signed(ses, bank_tx())}))
    assert result == {"status": "verified"} and len(calls) == 1
