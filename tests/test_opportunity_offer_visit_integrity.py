import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import Response
from starlette.requests import Request

from rental import deal_finder_router as router


def run(coro):
    return asyncio.run(coro)


def lead():
    return {
        "_id": router.ObjectId("64b64c2f6f43d82e7f4c1001"),
        "owner_name": "OWNER FIRST",
        "address": "123 Main St",
        "county": "moore",
        "offer": {
            "slug": "owner-abcd1234", "mode": "amount", "amount": 75000,
            "expires_at": "2099-10-08T00:00:00+00:00", "visits": 0,
        },
    }


def request(cookie="", scheme="https"):
    headers = [(b"cookie", f"rh_offer_visitor={cookie}".encode())] if cookie else []
    return Request({
        "type": "http", "method": "GET", "scheme": scheme,
        "path": "/api/public/oferta/owner-abcd1234", "query_string": b"",
        "headers": headers, "server": ("example.test", 443),
        "client": ("203.0.113.10", 1234),
    })


def setup(monkeypatch, modified=1):
    leads = SimpleNamespace(
        find_one=AsyncMock(return_value=lead()),
        update_one=AsyncMock(side_effect=[
            SimpleNamespace(modified_count=modified),
            *([SimpleNamespace(modified_count=1)] if modified else []),
        ]),
    )
    monkeypatch.setattr(router, "get_db", lambda: SimpleNamespace(deal_finder_leads=leads))
    return leads


def test_first_browser_visit_is_counted_atomically_and_sets_private_cookie(monkeypatch):
    leads = setup(monkeypatch)
    response = Response()
    result = run(router.public_offer("owner-abcd1234", request(), response))
    assert result["success"] is True
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "rh_offer_visitor=" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    query, update = leads.update_one.await_args_list[0].args
    visit_key = query["offer.visit_keys"]["$ne"]
    assert len(visit_key) == 32
    assert update["$inc"] == {"offer.visits": 1}
    assert update["$push"]["offer.visit_keys"]["$each"] == [visit_key]
    assert update["$push"]["offer.visit_keys"]["$slice"] == -256
    assert leads.update_one.await_args_list[1].args[0]["offer.first_visit_at"] == {"$exists": False}
    assert "203.0.113.10" not in repr(leads.update_one.await_args_list)


def test_same_cookie_produces_same_anonymous_key_without_resetting_cookie(monkeypatch):
    token = "A" * 32
    keys = []
    for _ in range(2):
        leads = setup(monkeypatch, modified=0)
        response = Response()
        run(router.public_offer("owner-abcd1234", request(token), response))
        keys.append(leads.update_one.await_args.args[0]["offer.visit_keys"]["$ne"])
        assert "set-cookie" not in response.headers
        assert leads.update_one.await_count == 1
    assert keys[0] == keys[1]


def test_invalid_cookie_is_rotated_and_never_stored_raw(monkeypatch):
    leads = setup(monkeypatch)
    response = Response()
    run(router.public_offer("owner-abcd1234", request("bad"), response))
    assert "rh_offer_visitor=" in response.headers["set-cookie"]
    assert "bad" not in repr(leads.update_one.await_args_list)
