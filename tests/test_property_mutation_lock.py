import asyncio

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.property_mutation_lock as lock


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class Properties:
    def __init__(self, existing=True, matched_count=1):
        self.existing = existing
        self.matched_count = matched_count
        self.updates = []

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return Result(self.matched_count)

    async def find_one(self, query, *args, **kwargs):
        return {"_id": query.get("_id")} if self.existing else None


class DB:
    def __init__(self, properties):
        self.properties = properties


def test_acquire_uses_single_property_cas_and_bounded_expiry(monkeypatch):
    props = Properties(existing=True, matched_count=1)
    monkeypatch.setattr(lock, "get_db", lambda: DB(props))
    property_id = str(ObjectId())
    token = run(lock.acquire_property_mutation_lock(property_id, "unit_topology_create", "admin@example.com"))
    assert token
    query, update = props.updates[0]
    assert query["_id"] == ObjectId(property_id)
    rendered_query = repr(query)
    assert "mutation_lock" in rendered_query
    assert "expires_at" in rendered_query
    claim = update["$set"]["mutation_lock"]
    assert claim["token"] == token
    assert claim["operation"] == "unit_topology_create"
    assert claim["actor"] == "admin@example.com"
    assert claim["expires_at"] > claim["acquired_at"]


def test_acquire_conflict_fails_closed(monkeypatch):
    props = Properties(existing=True, matched_count=0)
    monkeypatch.setattr(lock, "get_db", lambda: DB(props))
    with pytest.raises(HTTPException) as exc:
        run(lock.acquire_property_mutation_lock(str(ObjectId()), "lease_creation"))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_mutation_in_progress"


def test_acquire_missing_property_is_404(monkeypatch):
    props = Properties(existing=False, matched_count=0)
    monkeypatch.setattr(lock, "get_db", lambda: DB(props))
    with pytest.raises(HTTPException) as exc:
        run(lock.acquire_property_mutation_lock(str(ObjectId()), "lease_creation"))
    assert exc.value.status_code == 404
    assert exc.value.detail == "lease_property_not_found"


def test_release_only_unsets_exact_owned_token(monkeypatch):
    props = Properties(existing=True, matched_count=1)
    monkeypatch.setattr(lock, "get_db", lambda: DB(props))
    property_id = str(ObjectId())
    run(lock.release_property_mutation_lock(property_id, "owned-token"))
    query, update = props.updates[0]
    assert query["_id"] == ObjectId(property_id)
    assert query["mutation_lock.token"] == "owned-token"
    assert update == {"$unset": {"mutation_lock": ""}}
