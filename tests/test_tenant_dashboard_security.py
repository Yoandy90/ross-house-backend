import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI
from bson import ObjectId
from mongomock_motor import AsyncMongoMockClient

from rental.auth_metrics import router as pre_tenant_router
from rental.tenant_dashboard_security_router import _next_due, _next_unpaid_payment
from rental.tenant_router import router as historical_tenant_router


def test_secure_dashboard_is_first_runtime_match():
    app = FastAPI()
    app.include_router(pre_tenant_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    matches = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/tenant/dashboard"
        and "GET" in getattr(route, "methods", set())
    ]
    assert len(matches) == 2
    assert matches[0].name == "secure_tenant_dashboard"
    assert matches[1].name == "tenant_dashboard"


def test_server_mounts_auth_metrics_before_tenant_router():
    source = open("server.py", encoding="utf-8").read()
    pre = 'app.include_router(auth_metrics_router, prefix="/api")'
    legacy = 'app.include_router(tenant_router, prefix="/api")'
    assert source.count(pre) == 1
    assert source.count(legacy) == 1
    assert source.index(pre) < source.index(legacy)


def test_due_date_clamps_short_months():
    assert _next_due(datetime(2026, 2, 10), 31).strftime("%Y-%m-%d") == "2026-02-28"
    assert _next_due(datetime(2026, 2, 28), 31).strftime("%Y-%m-%d") == "2026-02-28"
    assert _next_due(datetime(2026, 3, 31), 31).strftime("%Y-%m-%d") == "2026-03-31"


def test_due_date_invalid_value_fails_to_safe_day_one():
    assert _next_due(datetime(2026, 8, 2), "bad").strftime("%Y-%m-%d") == "2026-09-01"


def test_unpaid_current_month_remains_next_even_after_due_day():
    async def scenario():
        db = AsyncMongoMockClient()["next_unpaid_current"]
        contract = {
            "_id": ObjectId(), "rent_amount": 1200, "payment_due_day": 1,
            "start_date": "2026-09-01", "end_date": "2027-08-31",
        }
        result = await _next_unpaid_payment(db, contract, datetime(2026, 9, 15))
        assert result["period"] == "2026-09"
        assert result["due_date"] == "2026-09-01"
        assert result["amount"] == 1200
    asyncio.run(scenario())


def test_paid_current_month_advances_to_next_month():
    async def scenario():
        db = AsyncMongoMockClient()["next_unpaid_advance"]
        contract = {
            "_id": ObjectId(), "rent_amount": 1200, "payment_due_day": 1,
            "start_date": "2026-09-01", "end_date": "2027-08-31",
        }
        await db.rental_payments.insert_one({
            "contract_id": str(contract["_id"]), "period": "2026-09",
            "status": "completed", "amount": 1200, "total_due": 1200,
        })
        result = await _next_unpaid_payment(db, contract, datetime(2026, 9, 15))
        assert result["period"] == "2026-10"
        assert result["due_date"] == "2026-10-01"
        assert result["current_month_paid"] is True
    asyncio.run(scenario())


def test_stale_current_attempt_is_exposed_as_review_without_advancing_month():
    async def scenario():
        db = AsyncMongoMockClient()["next_unpaid_review"]
        contract = {
            "_id": ObjectId(), "rent_amount": 1200, "payment_due_day": 1,
            "start_date": "2026-09-01", "end_date": "2027-08-31",
        }
        await db.rental_payments.insert_one({
            "contract_id": str(contract["_id"]), "period": "2026-09",
            "status": "pending", "amount": 1200, "total_due": 1200,
            "charge_attempt": {
                "id": "stuck", "status": "processing",
                "created_at": datetime(2026, 9, 15, 10, tzinfo=timezone.utc),
            },
        })
        result = await _next_unpaid_payment(
            db, contract, datetime(2026, 9, 15, 11, tzinfo=timezone.utc)
        )
        assert result["period"] == "2026-09"
        assert result["in_flight"] is True
        assert result["requires_review"] is True

    asyncio.run(scenario())
