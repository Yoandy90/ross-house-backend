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
    def __init__(self, existing=True, matched_count=1, fail_update=False):
        self.existing = existing
        self.matched_count = matched_count
        self.fail_update = fail_update
        self.updates = []

    async def update_one(self, query, update):
        if self.fail_update:
            raise RuntimeError("temporary database error")
        self.updates.append((query, update))
        return Result(self.matched_count)

    async def find_one(self, query, *args, **kwargs):
        return {"_id": query.get("_id")} if self.existing else None


class Contracts:
    def __init__(self, pending=None):
        self.pending = pending
        self.queries = []

    async def find_one(self, query, *args, **kwargs):
        self.queries.append(query)
        return self.pending


class DB:
    def __init__(self, properties, contracts=None):
        self.properties = properties
        self.rental_contracts = contracts or Contracts()


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


def test_recovery_fence_blocks_retained_lifecycle_claim(monkeypatch):
    property_id = str(ObjectId())
    contracts = Contracts({"_id": ObjectId(), "property_id": property_id, "lifecycle_claim_id": "claim"})
    monkeypatch.setattr(lock, "get_db", lambda: DB(Properties(), contracts))
    with pytest.raises(HTTPException) as exc:
        run(lock.assert_property_lifecycle_recovery_clear(property_id))
    assert exc.value.status_code == 409
    assert exc.value.detail == "property_lifecycle_recovery_pending"
    assert contracts.queries[0]["property_id"] == property_id
    assert "lifecycle_claim_id" in contracts.queries[0]


def test_recovery_fence_allows_clear_property(monkeypatch):
    monkeypatch.setattr(lock, "get_db", lambda: DB(Properties(), Contracts(None)))
    assert run(lock.assert_property_lifecycle_recovery_clear(str(ObjectId()))) is None


def test_release_only_unsets_exact_owned_token(monkeypatch):
    props = Properties(existing=True, matched_count=1)
    monkeypatch.setattr(lock, "get_db", lambda: DB(props))
    property_id = str(ObjectId())
    released = run(lock.release_property_mutation_lock(property_id, "owned-token"))
    assert released is True
    query, update = props.updates[0]
    assert query["_id"] == ObjectId(property_id)
    assert query["mutation_lock.token"] == "owned-token"
    assert update == {"$unset": {"mutation_lock": ""}}


def test_release_failure_is_non_throwing_after_committed_write(monkeypatch):
    props = Properties(existing=True, fail_update=True)
    monkeypatch.setattr(lock, "get_db", lambda: DB(props))
    released = run(lock.release_property_mutation_lock(str(ObjectId()), "owned-token"))
    assert released is False
