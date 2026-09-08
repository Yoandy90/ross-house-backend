import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from rental import deal_finder_router as router


LEAD_ID = "64b64c2f6f43d82e7f4c1001"
FUTURE = "2099-10-08T00:00:00+00:00"


def run(coro):
    return asyncio.run(coro)


def lead(response=None, expires=FUTURE):
    return {
        "_id": router.ObjectId(LEAD_ID),
        "owner_name": "OWNER FIRST",
        "address": "123 Main St",
        "offer": {
            "slug": "owner-abcd1234",
            "mode": "amount",
            "amount": 75000,
            "expires_at": expires,
            "response": response,
        },
    }


def setup(monkeypatch, docs, modified=1):
    leads = SimpleNamespace(
        find_one=AsyncMock(side_effect=docs),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=modified)),
    )
    monkeypatch.setattr(router, "get_db", lambda: SimpleNamespace(deal_finder_leads=leads))

    def discard(coro):
        coro.close()

    monkeypatch.setattr(router.asyncio, "create_task", discard)
    return leads


@pytest.mark.parametrize("price", [math.nan, math.inf, -math.inf, -1])
def test_nonfinite_or_negative_price_never_reads_or_writes(monkeypatch, price):
    leads = setup(monkeypatch, [])
    with pytest.raises(HTTPException) as exc:
        run(router.public_offer_respond(
            "owner-abcd1234", router.OfferResponseBody(action="call", price=price), object()))
    assert exc.value.status_code == 422
    leads.find_one.assert_not_awaited()
    leads.update_one.assert_not_awaited()


@pytest.mark.parametrize("expires", ["2020-01-01T00:00:00+00:00", "not-a-date", ""])
def test_expired_or_invalid_offer_cannot_be_answered(monkeypatch, expires):
    leads = setup(monkeypatch, [lead(expires=expires)])
    with pytest.raises(HTTPException) as exc:
        run(router.public_offer_respond(
            "owner-abcd1234", router.OfferResponseBody(action="call"), object()))
    assert exc.value.status_code == 410
    leads.update_one.assert_not_awaited()


def test_response_is_committed_with_offer_version_and_empty_response_guard(monkeypatch):
    leads = setup(monkeypatch, [lead()])
    result = run(router.public_offer_respond(
        "owner-abcd1234",
        router.OfferResponseBody(action="counter", price=82500, message="Call me"),
        object(),
    ))
    assert result == {"success": True}
    query, update = leads.update_one.await_args.args
    assert query["offer.slug"] == "owner-abcd1234"
    assert query["offer.expires_at"] == FUTURE
    assert {"offer.response": None} in query["$and"][0]["$or"]
    assert update["$set"]["offer.response"]["price"] == 82500
    assert update["$set"]["status"] == "interested"


def test_concurrent_second_response_cannot_overwrite_first(monkeypatch):
    first = {"action": "accept", "at": "2026-09-08T00:00:00+00:00"}
    leads = setup(monkeypatch, [lead(), lead(response=first)], modified=0)
    contract = AsyncMock()
    monkeypatch.setattr(router, "_generate_contract_for_lead", contract)
    with pytest.raises(HTTPException) as exc:
        run(router.public_offer_respond(
            "owner-abcd1234", router.OfferResponseBody(action="reject"), object()))
    assert exc.value.status_code == 409
    assert "ya tiene" in exc.value.detail
    contract.assert_not_awaited()
    assert leads.update_one.await_count == 1


def test_accepted_offer_generates_at_most_one_contract(monkeypatch):
    leads = setup(monkeypatch, [lead()])
    contract = AsyncMock(return_value=(b"pdf", {}))
    monkeypatch.setattr(router, "_generate_contract_for_lead", contract)
    result = run(router.public_offer_respond(
        "owner-abcd1234", router.OfferResponseBody(action="accept"), object()))
    assert result == {"success": True}
    contract.assert_awaited_once()
    assert contract.await_args.kwargs["price"] == 75000
