import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from rental import deal_finder_router as router


LEAD_ID = "64b64c2f6f43d82e7f4c1001"


def run(coro):
    return asyncio.run(coro)


def lead(history=None, claim=None):
    doc = {
        "_id": router.ObjectId(LEAD_ID), "property_id": "P-1",
        "owner_name": "OWNER FIRST", "address": "123 Main St",
        "status": "new",
        "offer": {
            "slug": "owner-abcd1234", "mode": "amount", "amount": 75000,
            "expires_at": "2026-10-08T00:00:00Z", "sent_history": history or [],
        },
    }
    if claim is not None:
        doc["offer_send_claim"] = claim
    return doc


def setup(monkeypatch, docs, modified):
    leads = SimpleNamespace(
        find_one=AsyncMock(side_effect=docs),
        update_one=AsyncMock(side_effect=[SimpleNamespace(modified_count=n) for n in modified]),
    )
    db = SimpleNamespace(
        deal_finder_leads=leads,
        rental_config=SimpleNamespace(find_one=AsyncMock(return_value={"phone": "8065550100"})),
    )
    monkeypatch.setattr(router, "auth_admin", AsyncMock())
    monkeypatch.setattr(router, "get_db", lambda: db)
    return leads


def send(monkeypatch, docs, modified, provider=True):
    leads = setup(monkeypatch, docs, modified)
    call = AsyncMock(side_effect=provider if isinstance(provider, Exception) else None,
                     return_value=provider if not isinstance(provider, Exception) else None)
    monkeypatch.setitem(sys.modules, "rental.ai_brain_router",
                        SimpleNamespace(_send_email_branded=call))
    body = router.OfferSendBody(channel="email", to=" Owner@Example.COM ")
    return leads, call, body


def test_confirmed_version_is_not_delivered_again(monkeypatch):
    previous = {"channel": "email", "to": "Owner@Example.com",
                "delivery_version": "version"}
    doc = lead([previous])
    # Use the implementation's deterministic version to model an already confirmed send.
    import hashlib, json
    previous["delivery_version"] = hashlib.sha256(json.dumps({
        "slug": doc["offer"]["slug"], "mode": doc["offer"]["mode"],
        "amount": doc["offer"]["amount"], "expires_at": doc["offer"]["expires_at"],
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    leads, provider, body = send(monkeypatch, [doc], [], True)
    with pytest.raises(HTTPException) as exc:
        run(router.send_offer_link(object(), LEAD_ID, body))
    assert exc.value.status_code == 409
    provider.assert_not_awaited()
    leads.update_one.assert_not_awaited()


def test_claim_marks_provider_and_persists_confirmed_history(monkeypatch):
    leads, provider, body = send(monkeypatch, [lead()], [1, 1, 1], True)
    result = run(router.send_offer_link(object(), LEAD_ID, body))
    assert result["success"] is True
    assert result["sent"]["normalized_to"] == "owner@example.com"
    assert len(result["sent"]["delivery_version"]) == 24
    assert leads.update_one.await_count == 3
    claim_filter, claim_update = leads.update_one.await_args_list[0].args
    assert claim_filter["offer.sent_history"]["$not"]["$elemMatch"]["normalized_to"] == "owner@example.com"
    token = claim_update["$set"]["offer_send_claim"]["token"]
    assert leads.update_one.await_args_list[1].args[0]["offer_send_claim.token"] == token
    save_filter, save_update = leads.update_one.await_args_list[2].args
    assert save_filter["offer_send_claim.token"] == token
    assert save_update["$unset"] == {"offer_send_claim": ""}
    provider.assert_awaited_once()


def test_unknown_previous_provider_attempt_blocks_delivery(monkeypatch):
    current = lead(claim={"provider_started_at": "2026-09-08T00:00:00Z"})
    leads, provider, body = send(monkeypatch, [lead(), current], [0], True)
    with pytest.raises(HTTPException) as exc:
        run(router.send_offer_link(object(), LEAD_ID, body))
    assert exc.value.status_code == 409
    assert "verificación" in exc.value.detail
    provider.assert_not_awaited()
    assert leads.find_one.await_count == 2


def test_known_provider_rejection_releases_claim(monkeypatch):
    leads, provider, body = send(monkeypatch, [lead()], [1, 1, 1], False)
    with pytest.raises(HTTPException) as exc:
        run(router.send_offer_link(object(), LEAD_ID, body))
    assert exc.value.status_code == 502
    assert leads.update_one.await_args_list[-1].args[1] == {"$unset": {"offer_send_claim": ""}}


def test_uncertain_provider_exception_keeps_claim(monkeypatch):
    leads, provider, body = send(monkeypatch, [lead()], [1, 1], RuntimeError("timeout"))
    with pytest.raises(RuntimeError, match="timeout"):
        run(router.send_offer_link(object(), LEAD_ID, body))
    assert leads.update_one.await_count == 2


def test_provider_success_with_failed_history_write_stays_blocked(monkeypatch):
    leads, provider, body = send(monkeypatch, [lead()], [1, 1, 0], True)
    with pytest.raises(HTTPException) as exc:
        run(router.send_offer_link(object(), LEAD_ID, body))
    assert exc.value.status_code == 503
    assert "historial" in exc.value.detail
    assert leads.update_one.await_count == 3
