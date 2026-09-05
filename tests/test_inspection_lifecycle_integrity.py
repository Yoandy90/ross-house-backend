import asyncio
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.inspection_security_router as secure
from rental.auth_metrics import router as security_router


def run(coro):
    return asyncio.run(coro)


class Result:
    matched_count = 1
    inserted_id = ObjectId()


class Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updates = []
    async def find_one(self, query):
        for row in self.rows:
            if query.get("_id") is not None and row.get("_id") != query["_id"]:
                continue
            if query.get("property_id") is not None and row.get("property_id") != query["property_id"]:
                continue
            if query.get("status") is not None and row.get("status") != query["status"]:
                continue
            return dict(row)
        return None
    async def insert_one(self, document):
        self.rows.append({**document, "_id": Result.inserted_id})
        return Result()
    async def update_one(self, query, update):
        self.updates.append((query, update))
        return Result()


class DB:
    def __init__(self, inspection=None):
        pid = ObjectId()
        self.property_id = pid
        self.properties = Collection([{"_id": pid, "name": "Safe Home"}])
        self.property_units = Collection([])
        self.rental_contracts = Collection([])
        self.inspections = Collection([inspection] if inspection else [])


class Request:
    def __init__(self, body=None): self.body = body or {}
    async def json(self): return self.body


@pytest.fixture(autouse=True)
def auth(monkeypatch):
    async def admin(_request): return {"email": "admin@example.com"}
    monkeypatch.setattr(secure, "auth_admin", admin)


def test_security_routes_win_first_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    expected = {
        ("POST", "/api/admin/inspections"): "create_inspection",
        ("PUT", "/api/admin/inspections/{inspection_id}"): "update_inspection",
        ("DELETE", "/api/admin/inspections/{inspection_id}"): "archive_inspection",
    }
    actual = {}
    for route in app.routes:
        for method in getattr(route, "methods", set()):
            key = (method, route.path)
            if key in expected and key not in actual:
                actual[key] = route.endpoint.__name__
    assert actual == expected


def test_create_derives_identity_and_rejects_client_tenant(monkeypatch):
    db = DB()
    monkeypatch.setattr(secure, "get_db", lambda: db)
    response = run(secure.create_inspection(Request({
        "property_id": str(db.property_id), "property_name": "forged",
        "tenant_name": "forged", "type": "routine",
    })))
    row = response["inspection"]
    assert row["property_name"] == "Safe Home"
    assert row["tenant_name"] == ""
    assert row["status"] == "pending"


def test_completed_inspection_is_immutable(monkeypatch):
    iid = ObjectId()
    db = DB({"_id": iid, "status": "completed", "property_id": "p"})
    monkeypatch.setattr(secure, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(secure.update_inspection(str(iid), Request({"general_notes": "changed"})))
    assert exc.value.detail == "inspection_completed_immutable"


def test_status_cannot_skip_to_completed(monkeypatch):
    iid = ObjectId()
    db = DB({"_id": iid, "status": "pending", "property_id": "p"})
    monkeypatch.setattr(secure, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(secure.update_inspection(str(iid), Request({"status": "completed"})))
    assert exc.value.detail == "inspection_status_transition_invalid"


def test_delete_archives_without_hard_delete(monkeypatch):
    iid = ObjectId()
    db = DB({"_id": iid, "status": "in_progress", "property_id": "p"})
    monkeypatch.setattr(secure, "get_db", lambda: db)
    response = run(secure.archive_inspection(str(iid), Request()))
    assert response == {"success": True, "archived": True}
    assert "archived_at" in db.inspections.updates[0][1]["$set"]
    source = Path("rental/inspection_security_router.py").read_text()
    assert ".delete_one(" not in source and ".delete_many(" not in source


def test_rooms_payload_is_bounded():
    with pytest.raises(HTTPException) as exc:
        secure._rooms({"room": "x" * 100_001})
    assert exc.value.status_code == 413
