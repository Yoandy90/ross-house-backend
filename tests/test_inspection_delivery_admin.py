import asyncio
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

import rental.inspection_delivery_router as delivery


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, row=None):
        self.row = row


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: row.get(key) or datetime.min.replace(tzinfo=timezone.utc), reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, count):
        return self.rows[:count]


class Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, query):
        return next((row for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)

    def find(self, query):
        return Cursor([row for row in self.rows if all(row.get(k) == v for k, v in query.items())])

    async def find_one_and_update(self, query, update, return_document=None):
        row = await self.find_one(query)
        if not row:
            return None
        row.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            row.pop(key, None)
        return row

    async def insert_one(self, document):
        self.rows.append(dict(document))
        return Result(document)


class DB:
    pass


def make_db(status="failed", failure_code="provider_http_503"):
    intent = {
        "_id": ObjectId(),
        "inspection_id": str(ObjectId()),
        "tenant_id": str(ObjectId()),
        "status": status,
        "failure_code": failure_code,
        "attempts": 3,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "email": "must-not-leak@example.com",
    }
    db = DB()
    db.inspection_delivery_outbox = Collection([intent])
    db.admin_audit_logs = Collection()
    return db, intent


def test_admin_list_filters_and_never_exposes_recipient():
    db, intent = make_db()
    response = run(delivery.list_delivery_outbox(status="failed", limit=50, admin={"_id": "a"}, db=db))
    assert len(response["items"]) == 1
    assert response["items"][0]["_id"] == str(intent["_id"])
    assert "email" not in response["items"][0]


def test_manual_retry_requires_reason_and_resets_only_safe_failure():
    db, intent = make_db()
    payload = delivery.ManualDeliveryRetryRequest(reason="SendGrid service restored")
    response = run(delivery.retry_failed_delivery(str(intent["_id"]), payload, admin={"email": "admin@example.com"}, db=db))
    assert response["item"]["status"] == "pending"
    assert response["item"]["attempts"] == 0
    assert len(db.admin_audit_logs.rows) == 1
    assert db.admin_audit_logs.rows[0]["action"] == "inspection_delivery_manual_retry"


def test_ambiguous_result_cannot_be_retried():
    db, intent = make_db("ambiguous_provider_result", "provider_transport_or_timeout")
    payload = delivery.ManualDeliveryRetryRequest(reason="Tenant says no message arrived")
    try:
        run(delivery.retry_failed_delivery(str(intent["_id"]), payload, admin={"_id": "a"}, db=db))
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "ambiguous_delivery_manual_review_required"


def test_terminal_data_failure_cannot_be_retried():
    db, intent = make_db("failed", "tenant_binding_changed")
    payload = delivery.ManualDeliveryRetryRequest(reason="Try terminal failure again")
    try:
        run(delivery.retry_failed_delivery(str(intent["_id"]), payload, admin={"_id": "a"}, db=db))
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "delivery_not_safely_retryable"
