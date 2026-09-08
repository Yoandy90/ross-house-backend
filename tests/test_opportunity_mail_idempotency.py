import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from rental import deal_finder_router as router


LEAD_ID = "64b64c2f6f43d82e7f4c1001"
OFFER_HASH = hashlib.sha256(b"Stable offer version").hexdigest()[:24]


def run(coro):
    return asyncio.run(coro)


def lead(mail=None):
    doc = {
        "_id": router.ObjectId(LEAD_ID),
        "property_id": "P-1",
        "address": "123 Main St",
        "owner_name": "Owner",
        "offer_letter": {"letter_en": "Stable offer version"},
    }
    if mail is not None:
        doc["mail"] = mail
    return doc


def setup(monkeypatch, docs, updates):
    collection = SimpleNamespace(
        find_one=AsyncMock(side_effect=docs),
        update_one=AsyncMock(side_effect=[SimpleNamespace(modified_count=n) for n in updates]),
    )
    db = SimpleNamespace(deal_finder_leads=collection)
    monkeypatch.setattr(router, "auth_admin", AsyncMock())
    monkeypatch.setattr(router, "get_db", lambda: db)
    monkeypatch.setattr(router, "_lob_key", lambda: "test_key")
    monkeypatch.setattr(router, "_lead_mail_parts", lambda doc: (["123 Main"], "Dumas", "TX", "79029"))
    monkeypatch.setattr(router, "_sender_info", AsyncMock(return_value={
        "name": "Ross House", "address": "1 Office", "city": "Dumas", "state": "TX", "zip": "79029",
    }))
    monkeypatch.setattr(router, "_lead_photo", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_build_letter_pdf", lambda *args, **kwargs: b"pdf")
    return collection


class Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = "provider detail that must not reach clients"

    def json(self):
        return self._payload


def client_factory(responses, keys):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            if url.endswith("/letters"):
                keys.append(kwargs["headers"]["Idempotency-Key"])
            return responses.pop(0)

    return Client


def test_existing_letter_never_contacts_provider(monkeypatch):
    collection = setup(monkeypatch, [lead({"lob_id": "ltr_existing"})], [])
    provider = AsyncMock()
    monkeypatch.setattr(router.httpx, "AsyncClient", provider)
    with pytest.raises(HTTPException) as exc:
        run(router.mail_letter(object(), LEAD_ID))
    assert exc.value.status_code == 409
    provider.assert_not_called()
    collection.update_one.assert_not_awaited()


def test_atomic_claim_and_stable_provider_idempotency(monkeypatch):
    keys = []
    collection = setup(monkeypatch, [lead()], [1, 1])
    monkeypatch.setattr(router.httpx, "AsyncClient", client_factory([
        Response(400), Response(201, {"id": " ltr_1 ", "expected_delivery_date": "2026-09-10"}),
    ], keys))
    result = run(router.mail_letter(object(), LEAD_ID))
    assert result["mail"]["lob_id"] == "ltr_1"
    assert keys == [f"ross-house-lead-{LEAD_ID}-offer-{OFFER_HASH}"]
    claim_filter, claim_update = collection.update_one.await_args_list[0].args
    assert claim_filter["mail.lob_id"] == {"$exists": False}
    assert claim_update["$set"]["mail_send_claim"]["token"]
    save_filter, save_update = collection.update_one.await_args_list[1].args
    assert save_filter["mail_send_claim.token"] == claim_update["$set"]["mail_send_claim"]["token"]
    assert save_update["$unset"] == {"mail_send_claim": ""}


def test_claim_conflict_does_not_contact_provider(monkeypatch):
    collection = setup(monkeypatch, [lead(), lead()], [0])
    provider = AsyncMock()
    monkeypatch.setattr(router.httpx, "AsyncClient", provider)
    with pytest.raises(HTTPException) as exc:
        run(router.mail_letter(object(), LEAD_ID))
    assert exc.value.status_code == 409
    assert "en curso" in exc.value.detail
    provider.assert_not_called()
    assert collection.find_one.await_count == 2


def test_provider_failure_releases_claim_without_leaking_detail(monkeypatch):
    keys = []
    collection = setup(monkeypatch, [lead()], [1, 1])
    monkeypatch.setattr(router.httpx, "AsyncClient", client_factory([
        Response(400), Response(500),
    ], keys))
    with pytest.raises(HTTPException) as exc:
        run(router.mail_letter(object(), LEAD_ID))
    assert exc.value.status_code == 502
    assert "provider detail" not in exc.value.detail
    release_filter, release_update = collection.update_one.await_args_list[-1].args
    assert "mail_send_claim.token" in release_filter
    assert release_update == {"$unset": {"mail_send_claim": ""}}
    assert keys[0] == f"ross-house-lead-{LEAD_ID}-offer-{OFFER_HASH}"


def test_invalid_provider_confirmation_releases_claim(monkeypatch):
    collection = setup(monkeypatch, [lead()], [1, 1])
    monkeypatch.setattr(router.httpx, "AsyncClient", client_factory([
        Response(400), Response(201, {"id": ""}),
    ], []))
    with pytest.raises(HTTPException) as exc:
        run(router.mail_letter(object(), LEAD_ID))
    assert exc.value.status_code == 502
    assert collection.update_one.await_count == 2
