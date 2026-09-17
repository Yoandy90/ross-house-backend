"""Pending-task counts against isolated Mongo fixtures; no external services."""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from rental import admin_nav_router as nav


@pytest.fixture
def dashboard(monkeypatch):
    db = AsyncMongoMockClient()["admin_nav_test"]
    monkeypatch.setattr(nav, "get_db", lambda: db)
    monkeypatch.setattr(nav, "auth_admin", AsyncMock(return_value={"role": "admin"}))
    app = FastAPI()
    app.include_router(nav.router, prefix="/api")
    return db, app


async def summary(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/admin/nav-summary")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_resolved_ticket_is_not_pending_and_reopening_restores_count(dashboard):
    db, app = dashboard
    # Reproduce the staging list: one assigned, one resolved, one completed.
    await db.maintenance_requests.insert_many([
        {"_id": "assigned", "status": "assigned"},
        {"_id": "resolved", "status": "resolved"},
        {"_id": "completed", "status": "completed"},
    ])
    await db.resident_store.insert_one({
        "_id": "resident-store-v1",
        "orders": {"active": {"status": "received"}, "done": {"status": "delivered"}},
    })
    data = await summary(app)
    assert data["open_maintenance"] == 1
    assert data["store_orders"] == 1
    assert data["total"] == 2

    await db.maintenance_requests.update_one(
        {"_id": "resolved"}, {"$set": {"status": "in_progress"}}
    )
    data = await summary(app)
    assert data["open_maintenance"] == 2
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_only_finished_tickets_give_no_maintenance_alert(dashboard):
    db, app = dashboard
    await db.maintenance_requests.insert_many([
        {"status": status} for status in ("completed", "resolved", "cancelled", "closed")
    ])
    data = await summary(app)
    assert data["open_maintenance"] == 0
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_unfinished_and_legacy_tickets_remain_visible(dashboard):
    db, app = dashboard
    await db.maintenance_requests.insert_many([
        {"status": status} for status in (
            "open", "pending", "reviewing", "assigned", "scheduled",
            "en_route", "in_progress", "waiting_parts",
        )
    ])
    # Unknown or missing legacy statuses must still prompt operator review.
    await db.maintenance_requests.insert_many([{}, {"status": "needs_review"}])
    data = await summary(app)
    assert data["open_maintenance"] == 10
    assert data["total"] == 10
