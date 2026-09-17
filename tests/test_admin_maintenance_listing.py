"""Exercise the real admin list endpoint with isolated Mongo fixtures."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from rental import tenant_router as tenant


@pytest.fixture
def listing(monkeypatch):
    db = AsyncMongoMockClient()["maintenance_listing_test"]
    monkeypatch.setattr(tenant, "get_db", lambda: db)
    monkeypatch.setattr(tenant, "auth_admin", AsyncMock(return_value={"role": "admin"}))
    app = FastAPI()
    app.include_router(tenant.router, prefix="/api")
    return db, app


async def fetch(app, **params):
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        return await client.get("/api/admin/maintenance-requests", params=params)


async def data(app, **params):
    response = await fetch(app, **params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_urgency_ranks_the_entire_queue_before_pagination(listing):
    db, app = listing
    now = datetime(2026, 9, 17)
    await db.maintenance_requests.insert_many([
        {"_id": f"low-{i:03}", "priority": "low", "status": "pending", "created_at": now}
        for i in range(60)
    ] + [
        {"_id": "old-urgent", "priority": "urgent", "status": "en_route", "created_at": now - timedelta(days=2)},
        {"_id": "legacy-emergency", "priority": "emergency", "status": "assigned", "created_at": now - timedelta(days=1)},
        {"_id": "finished-urgent", "priority": "urgent", "status": "resolved", "created_at": now},
    ])
    first = await data(app, sort="urgency", limit=2)
    assert [r["id"] for r in first["requests"]] == ["legacy-emergency", "old-urgent"]
    assert first["total"] == 63 and first["total_pages"] == 32
    assert first["stats"]["urgent"] == 2 and first["stats"]["active"] == 62
    second = await data(app, sort="urgency", limit=2, page=2)
    assert [r["id"] for r in second["requests"]] == ["low-059", "low-058"]
    assert second["stats"] == first["stats"]
    last = await data(app, sort="urgency", limit=2, page=32)
    assert [r["id"] for r in last["requests"]] == ["finished-urgent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("priority,expected", [
    ("urgent", ["emergency", "urgent"]), ("emergency", ["emergency", "urgent"]),
    ("medium", ["medium", "normal"]), ("normal", ["medium", "normal"]),
])
async def test_priority_filters_include_legacy_aliases(listing, priority, expected):
    db, app = listing
    await db.maintenance_requests.insert_many([
        {"_id": p, "priority": p, "status": "pending"} for p in ("low", "normal", "medium", "high", "urgent", "emergency")
    ])
    result = await data(app, priority=priority)
    assert sorted(r["id"] for r in result["requests"]) == expected
    assert result["total"] == 2
    assert result["stats"]["urgent"] == (2 if priority in ("urgent", "emergency") else 0)


@pytest.mark.asyncio
async def test_pending_filter_matches_the_status_shown_for_legacy_rows(listing):
    db, app = listing
    await db.maintenance_requests.insert_many([
        {"_id": str(i), "status": status} for i, status in enumerate(["pending", "open", "", None, "assigned"])
    ] + [{"_id": "missing-status"}])
    for status in ("pending", "open"):
        result = await data(app, status=status)
        assert result["total"] == 5
        assert {r["status"] for r in result["requests"]} == {"pending"}
        assert result["stats"]["pending"] == result["stats"]["open"] == result["stats"]["active"] == 5


@pytest.mark.asyncio
async def test_stats_honor_priority_status_and_literal_search_filters(listing):
    db, app = listing
    await db.maintenance_requests.insert_many([
        {"_id": "low", "priority": "low", "status": "pending", "title": "Leak [A]"},
        {"_id": "urgent", "priority": "urgent", "status": "en_route", "title": "Leak [A]"},
        {"_id": "other", "priority": "urgent", "status": "assigned", "title": "Leak A"},
    ] + [{"_id": s, "priority": "urgent", "status": s, "title": "Leak [A]"}
         for s in ("completed", "resolved", "closed", "cancelled")])
    result = await data(app, search="[A]")
    assert result["total"] == 6
    assert result["stats"]["active"] == 2 and result["stats"]["finished"] == 3
    assert result["stats"]["cancelled"] == 1 and result["stats"]["urgent"] == 1
    assert result["stats"]["en_route"] == 1
    low = await data(app, search="[A]", priority="low")
    assert low["total"] == low["stats"]["active"] == 1
    assert low["stats"]["urgent"] == low["stats"]["finished"] == 0
    for status in ("completed", "resolved", "closed", "cancelled"):
        done = await data(app, search="[A]", priority="urgent", status=status)
        assert done["total"] == 1 and done["stats"]["urgent"] == done["stats"]["active"] == 0


@pytest.mark.asyncio
async def test_date_sort_and_urgency_ties_have_stable_pages(listing):
    db, app = listing
    now = datetime(2026, 9, 17)
    await db.maintenance_requests.insert_many([
        {"_id": "a", "priority": "normal", "created_at": now},
        {"_id": "b", "priority": "medium", "created_at": now},
        {"_id": "c", "priority": "high", "created_at": now - timedelta(days=1)},
    ])
    for sort, expected in [("date", ["b", "a", "c"]), ("urgency", ["c", "b", "a"]), ("", ["b", "a", "c"])]:
        ids = [(await data(app, sort=sort, page=p, limit=1))["requests"][0]["id"] for p in (1, 2, 3)]
        assert ids == expected
    assert (await data(app))["requests"][0]["id"] == "b"


@pytest.mark.asyncio
async def test_empty_and_out_of_range_pages_remain_consistent(listing):
    db, app = listing
    empty = await data(app, page=999, limit=2, sort="urgency")
    assert empty["page"] == empty["total_pages"] == 1
    assert empty["requests"] == [] and empty["total"] == 0
    assert not any(empty["stats"].values())
    await db.maintenance_requests.insert_one({"_id": "only", "status": "needs_review"})
    last = await data(app, page=999, limit=2, sort="urgency")
    assert last["page"] == last["total_pages"] == last["stats"]["active"] == 1


@pytest.mark.asyncio
async def test_failed_statistics_do_not_return_success_with_false_zeroes(listing, monkeypatch):
    db, app = listing
    collection = db.maintenance_requests
    monkeypatch.setattr(tenant, "get_db", lambda: SimpleNamespace(maintenance_requests=collection))
    monkeypatch.setattr(collection, "aggregate", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("test database failure")))
    assert (await fetch(app, sort="date")).status_code == 500


@pytest.mark.asyncio
async def test_admin_authorization_still_precedes_database_access(listing, monkeypatch):
    db, app = listing
    monkeypatch.setattr(tenant, "auth_admin", AsyncMock(side_effect=HTTPException(status_code=401)))
    collection = db.maintenance_requests
    monkeypatch.setattr(tenant, "get_db", lambda: SimpleNamespace(maintenance_requests=collection))
    counter = AsyncMock()
    monkeypatch.setattr(collection, "count_documents", counter)
    assert (await fetch(app)).status_code == 401
    counter.assert_not_awaited()
