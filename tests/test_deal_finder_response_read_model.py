import asyncio

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.deal_finder_router as deal_finder


def run(coro):
    return asyncio.run(coro)


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)
        self.sort_spec = None

    def sort(self, spec):
        self.sort_spec = spec
        return self

    async def to_list(self, limit):
        return self.docs[:limit]


class Leads:
    def __init__(self, docs):
        self.cursor = Cursor(docs)
        self.query = None

    def find(self, query):
        self.query = query
        return self.cursor


class DB:
    def __init__(self, docs):
        self.deal_finder_leads = Leads(docs)


async def allow_admin(_request):
    return {"role": "admin", "_id": "admin-1"}


def lead(action="accept"):
    return {
        "_id": ObjectId(),
        "county": "moore",
        "property_id": "R123",
        "owner_name": "Owner",
        "address": "121 Oak Ave",
        "offer": {"response": {"action": action, "at": "2026-09-01T00:00:00Z"}},
    }


def test_response_list_is_admin_gated_bounded_and_newest_first(monkeypatch):
    db = DB([lead(), lead("counter")])
    calls = {"auth": 0}

    async def auth(request):
        calls["auth"] += 1
        return await allow_admin(request)

    monkeypatch.setattr(deal_finder, "auth_admin", auth)
    monkeypatch.setattr(deal_finder, "get_db", lambda: db)
    result = run(deal_finder.list_offer_responses(object(), action=None, limit=500))

    assert calls["auth"] == 1
    assert result["success"] is True and result["read_only"] is True
    assert len(result["responses"]) == 2
    assert db.deal_finder_leads.query == {"offer.response.action": {"$exists": True}}
    assert db.deal_finder_leads.cursor.sort_spec == [
        ("offer.response.at", -1), ("_id", -1)
    ]


def test_response_action_filter_is_allowlisted(monkeypatch):
    db = DB([lead("counter")])
    monkeypatch.setattr(deal_finder, "auth_admin", allow_admin)
    monkeypatch.setattr(deal_finder, "get_db", lambda: db)

    result = run(deal_finder.list_offer_responses(object(), action="counter", limit=10))
    assert result["responses"][0]["offer"]["response"]["action"] == "counter"
    assert db.deal_finder_leads.query == {"offer.response.action": "counter"}

    with pytest.raises(HTTPException) as exc:
        run(deal_finder.list_offer_responses(object(), action={"$ne": ""}, limit=10))
    assert exc.value.status_code == 422
    assert exc.value.detail == "deal_finder_response_action_invalid"


def test_auth_failure_prevents_database_access(monkeypatch):
    async def deny(_request):
        raise HTTPException(status_code=401, detail="not_authenticated")

    monkeypatch.setattr(deal_finder, "auth_admin", deny)
    monkeypatch.setattr(
        deal_finder,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be touched")),
    )
    with pytest.raises(HTTPException) as exc:
        run(deal_finder.list_offer_responses(object(), action=None, limit=50))
    assert exc.value.status_code == 401
