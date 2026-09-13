import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.maintenance_technician_router as technician


def run(coro):
    return asyncio.run(coro)


class Request:
    def __init__(self, body=None):
        self.body = body or {}

    async def json(self):
        return self.body


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def __aiter__(self):
        self._iter = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.last_find = None
        self.last_update = None

    async def find_one(self, query):
        self.last_find = query
        for row in self.rows:
            if "email" in query and row.get("email") == query["email"]:
                return row
            if "_id" in query and row.get("_id") == query["_id"]:
                provider_filter = query.get("assigned_provider_id")
                if provider_filter and row.get("assigned_provider_id") not in provider_filter.get("$in", []):
                    continue
                return row
        return None

    def find(self, query):
        self.last_find = query
        provider_values = query.get("assigned_provider_id", {}).get("$in", [])
        return Cursor([r for r in self.rows if r.get("assigned_provider_id") in provider_values])

    async def update_one(self, query, update):
        self.last_update = (query, update)
        return SimpleNamespace(matched_count=1)

    async def insert_one(self, doc):
        oid = ObjectId()
        stored = {"_id": oid, **doc}
        self.rows.append(stored)
        return SimpleNamespace(inserted_id=oid)

    async def delete_one(self, query):
        return SimpleNamespace(deleted_count=1)


def test_non_maintenance_role_is_rejected(monkeypatch):
    async def auth(_request):
        return {"_id": ObjectId(), "role": "tenant"}

    monkeypatch.setattr(technician, "auth_marketplace", auth)
    with pytest.raises(HTTPException) as exc:
        run(technician._maintenance_actor(Request()))
    assert exc.value.status_code == 403
    assert exc.value.detail == "maintenance_role_required"


def test_provider_link_must_belong_to_actor(monkeypatch):
    user_id = ObjectId()
    provider_id = "provider-1"

    async def auth(_request):
        return {"_id": user_id, "role": "maintenance", "service_provider_id": provider_id}

    db = SimpleNamespace(service_providers=Collection([{
        "_id": provider_id,
        "status": "active",
        "app_user_id": str(ObjectId()),
    }]))
    monkeypatch.setattr(technician, "auth_marketplace", auth)
    monkeypatch.setattr(technician, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(technician._maintenance_actor(Request()))
    assert exc.value.detail == "maintenance_provider_link_mismatch"


def test_dashboard_returns_only_assigned_provider_jobs(monkeypatch):
    provider_id = "provider-1"
    jobs = Collection([
        {"_id": ObjectId(), "assigned_provider_id": provider_id, "status": "assigned", "title": "Sink"},
        {"_id": ObjectId(), "assigned_provider_id": "provider-2", "status": "assigned", "title": "Roof"},
    ])
    db = SimpleNamespace(maintenance_requests=jobs)

    async def actor(_request):
        return ({"_id": ObjectId()}, {"name": "Tech One", "worker_type": "contractor"}, provider_id)

    monkeypatch.setattr(technician, "_maintenance_actor", actor)
    monkeypatch.setattr(technician, "get_db", lambda: db)
    result = run(technician.maintenance_dashboard(Request()))
    assert [job["title"] for job in result["jobs"]] == ["Sink"]
    assert result["stats"]["assigned"] == 1
    assert result["provider"]["worker_type"] == "contractor"
    assert jobs.last_find == {"assigned_provider_id": {"$in": [provider_id]}}


def test_foreign_job_id_is_not_disclosed(monkeypatch):
    provider_id = "provider-1"
    job_id = ObjectId()
    db = SimpleNamespace(maintenance_requests=Collection([{
        "_id": job_id,
        "assigned_provider_id": "provider-2",
        "status": "assigned",
    }]))

    async def actor(_request):
        return ({"_id": ObjectId()}, {"name": "Tech One"}, provider_id)

    monkeypatch.setattr(technician, "_maintenance_actor", actor)
    monkeypatch.setattr(technician, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(technician.maintenance_job_detail(str(job_id), Request()))
    assert exc.value.status_code == 404
    assert exc.value.detail == "maintenance_job_not_found"


def test_technician_cannot_reopen_completed_job(monkeypatch):
    provider_id = "provider-1"
    job_id = ObjectId()
    db = SimpleNamespace(maintenance_requests=Collection([{
        "_id": job_id,
        "assigned_provider_id": provider_id,
        "status": "completed",
    }]))

    async def actor(_request):
        return ({"_id": ObjectId()}, {"name": "Tech One"}, provider_id)

    monkeypatch.setattr(technician, "_maintenance_actor", actor)
    monkeypatch.setattr(technician, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(technician.update_maintenance_job(str(job_id), Request({"status": "in_progress"})))
    assert exc.value.status_code == 409
    assert exc.value.detail == "maintenance_job_transition_invalid"


def test_materials_are_bounded_and_totaled():
    rows, total = technician._validated_materials([
        {"name": "Valve", "quantity": 2, "unit_cost": 7.25},
        {"name": "Seal", "quantity": 1, "unit_cost": 1.50},
    ])
    assert total == 16.0
    assert rows[0]["line_total"] == 14.5

    with pytest.raises(HTTPException) as exc:
        technician._validated_materials([{"name": "Valve", "quantity": True, "unit_cost": 1}])
    assert exc.value.detail == "maintenance_material_amount_invalid"


def test_admin_creation_uses_explicit_provider_link(monkeypatch):
    provider_id = "provider-1"
    providers = Collection([{"_id": provider_id, "status": "active", "name": "Tech One"}])
    users = Collection()
    db = SimpleNamespace(service_providers=providers, app_users=users)

    async def admin(_request):
        return {"email": "admin@example.com"}

    monkeypatch.setattr(technician, "auth_admin", admin)
    monkeypatch.setattr(technician, "get_db", lambda: db)
    result = run(technician.create_maintenance_user(Request({
        "name": "Tech One",
        "email": "tech@example.com",
        "phone": "8065551000",
        "password": "StrongPass1!",
        "provider_id": provider_id,
        "worker_type": "employee",
    })))
    assert result["success"] is True
    assert result["user"]["role"] == "maintenance"
    assert users.rows[0]["service_provider_id"] == provider_id
    assert users.rows[0]["maintenance_worker_type"] == "employee"
    assert users.rows[0]["password_hash"] != "StrongPass1!"
    assert providers.last_update[1]["$set"]["app_access_enabled"] is True
    assert providers.last_update[1]["$set"]["worker_type"] == "employee"


def test_router_is_mounted_in_server_source():
    source = open("server.py", encoding="utf-8").read()
    assert "maintenance_technician_router" in source
    assert "app.include_router(maintenance_technician_router" in source


def test_public_provider_registration_is_always_contractor():
    source = open("rental/service_providers_router.py", encoding="utf-8").read()
    assert "data['worker_type'] = 'contractor'" in source
    assert "pattern='^(contractor|employee)$'" in source
