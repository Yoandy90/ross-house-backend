import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from rental import deal_finder_router as router


LEAD_ID = "64b64c2f6f43d82e7f4c1001"


def run(coro):
    return asyncio.run(coro)


def doc(offer=None):
    return {
        "_id": router.ObjectId(LEAD_ID), "owner_name": "OWNER FIRST",
        "offer": offer or {},
    }


def setup(monkeypatch, reads, matched=1):
    leads = SimpleNamespace(
        find_one=AsyncMock(side_effect=reads),
        update_one=AsyncMock(return_value=SimpleNamespace(matched_count=matched)),
    )
    monkeypatch.setattr(router, "auth_admin", AsyncMock())
    monkeypatch.setattr(router, "get_db", lambda: SimpleNamespace(deal_finder_leads=leads))
    return leads


def test_regeneration_preserves_concurrent_history_response_and_visits(monkeypatch):
    existing = {
        "slug": "owner-abcd1234", "mode": "amount", "amount": 70000,
        "created_at": "2026-09-01T00:00:00Z", "expires_at": "2026-10-01T00:00:00Z",
        "visits": 4, "response": {"action": "call"},
        "sent_history": [{"channel": "email", "to": "owner@example.com"}],
    }
    concurrent = {**existing, "mode": "ask", "amount": 0, "visits": 5,
                  "sent_history": [*existing["sent_history"], {"channel": "sms", "to": "8065550101"}]}
    leads = setup(monkeypatch, [doc(existing), doc(concurrent)])
    result = run(router.create_offer(object(), LEAD_ID, router.OfferBody(mode="ask", amount=0)))
    assert result["offer"]["visits"] == 5
    assert len(result["offer"]["sent_history"]) == 2
    query, update = leads.update_one.await_args.args
    assert query["offer_send_claim"] == {"$exists": False}
    assert "offer" not in update["$set"]
    assert "offer.sent_history" not in update["$set"]
    assert "offer.response" not in update["$set"]
    assert update["$set"]["offer.mode"] == "ask"


def test_new_offer_initializes_only_new_nested_fields(monkeypatch):
    current_offer = {
        "slug": "owner-newslug1", "mode": "amount", "amount": 50000,
        "created_at": "now", "expires_at": "later", "visits": 0,
        "last_visit_at": None, "response": None, "sent_history": [],
    }
    leads = setup(monkeypatch, [doc(), None, doc(current_offer)])
    monkeypatch.setattr(router, "_offer_slug", lambda name: "owner-newslug1")
    result = run(router.create_offer(object(), LEAD_ID, router.OfferBody(mode="amount", amount=50000)))
    assert result["offer"]["slug"] == "owner-newslug1"
    update = leads.update_one.await_args.args[1]["$set"]
    assert update["offer.sent_history"] == []
    assert update["offer.visits"] == 0


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf, 0, -1])
def test_invalid_amount_never_reads_or_writes(monkeypatch, amount):
    leads = setup(monkeypatch, [])
    with pytest.raises(HTTPException) as exc:
        run(router.create_offer(object(), LEAD_ID, router.OfferBody(mode="amount", amount=amount)))
    assert exc.value.status_code == 422
    leads.find_one.assert_not_awaited()
    leads.update_one.assert_not_awaited()


def test_pending_delivery_blocks_offer_change(monkeypatch):
    existing = {"slug": "owner-abcd1234", "created_at": "before"}
    leads = setup(monkeypatch, [doc(existing)], matched=0)
    with pytest.raises(HTTPException) as exc:
        run(router.create_offer(object(), LEAD_ID, router.OfferBody(mode="ask", amount=0)))
    assert exc.value.status_code == 409
    assert "envío" in exc.value.detail
    assert leads.find_one.await_count == 1


def test_slug_collision_exhaustion_does_not_overwrite(monkeypatch):
    leads = setup(monkeypatch, [doc(), *[doc({"slug": "taken"}) for _ in range(5)]])
    monkeypatch.setattr(router, "_offer_slug", lambda name: "taken")
    with pytest.raises(HTTPException) as exc:
        run(router.create_offer(object(), LEAD_ID, router.OfferBody(mode="ask", amount=0)))
    assert exc.value.status_code == 503
    leads.update_one.assert_not_awaited()
