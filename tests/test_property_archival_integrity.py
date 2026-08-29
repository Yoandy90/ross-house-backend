import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.property_archival_security_router as archival
from rental.auth_metrics import router as pre_router
from rental.properties_router import router as historical_properties_router


def run(coro):
    return asyncio.run(coro)


class Request:
    client = SimpleNamespace(host="127.0.0.1")


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class Collection:
    def __init__(self, doc=None, matched_count=1):
        self.doc = doc
        self.matched_count = matched_count
        self.updates = []

    async def find_one(self, query, *args, **kwargs):
        return self.doc

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return Result(self.matched_count)


class DB:
    def __init__(self, prop, contract=None, unit=None, matched_count=1):
        self.properties = Collection(prop, matched_count)
        self.rental_contracts = Collection(contract, matched_count)
        self.property_units = Collection(unit, matched_count)


async def allow_admin(_request):
    return {"_id": str(ObjectId()), "role": "admin", "email": "admin@example.com"}


async def fake_acquire(_property_id, _operation, _actor=""):
    return "archive-token"


async def fake_recovery_clear(_property_id):
    return None


def install_archival_stubs(monkeypatch, db, released):
    async def fake_release(property_id, token):
        released.append((property_id, token))
        return True

    monkeypatch.setattr(archival, "auth_admin", allow_admin)
    monkeypatch.setattr(archival, "acquire_property_mutation_lock", fake_acquire)
    monkeypatch.setattr(archival, "assert_property_lifecycle_recovery_clear", fake_recovery_clear)
    monkeypatch.setattr(archival, "release_property_mutation_lock", fake_release)
    monkeypatch.setattr(archival, "get_db", lambda: db)


def active_property(**extra):
    doc = {
        "_id": ObjectId(),
        "status": "available",
        "current_contract_id": None,
        "current_tenant_id": None,
    }
    doc.update(extra)
    return doc


def test_archive_route_is_first_match_and_hard_delete_is_not_authority():
    app = FastAPI()
    app.include_router(pre_router, prefix="/api")
    app.include_router(historical_properties_router, prefix="/api")
    matches = [r for r in app.routes if getattr(r, "path", None) == "/api/admin/properties/{property_id}" and "DELETE" in getattr(r, "methods", set())]
    assert len(matches) >= 2
    assert matches[0].name == "archive_property"


def test_archive_is_lock_recovery_and_exact_token_fenced():
    source = Path("rental/property_archival_security_router.py").read_text()
    assert '"property_archive"' in source
    assert 'assert_property_lifecycle_recovery_clear(property_id)' in source
    assert '"mutation_lock.token": token' in source
    assert 'detail="property_archive_contract_conflict"' in source
    assert 'detail="property_archive_unit_occupancy_conflict"' in source
    assert "delete_one(" not in source


def test_restore_is_explicit_serialized_workflow():
    source = Path("rental/property_archival_security_router.py").read_text()
    assert "@router.post('/admin/properties/{property_id}/restore')" in source
    assert '"property_restore"' in source
    assert '"$unset": {"archived_at": "", "archived_by": ""}' in source
    assert '"mutation_lock.token": token' in source


def test_archived_property_rejected_by_lease_and_topology_authority():
    lease = Path("rental/lease_creation_security_router.py").read_text()
    topology = Path("rental/unit_topology_security_router.py").read_text()
    lifecycle = Path("rental/property_lifecycle_security_router.py").read_text()
    assert 'detail="lease_property_archived"' in lease
    assert 'detail="property_archived"' in topology
    assert 'detail="property_archived"' in lifecycle


def test_archived_property_hidden_and_sync_excluded():
    visibility = Path("rental/property_visibility_security_router.py").read_text()
    sync = Path("rental/property_sync_cron.py").read_text()
    auth = Path("rental/auth_metrics.py").read_text()
    assert 'archived_at' in visibility
    assert 'raise HTTPException(status_code=404, detail="Propiedad no encontrada")' in visibility
    assert 'skipped_archived' in sync
    assert 'if prop.get("archived_at"):' in sync
    assert "router.routes.extend(property_visibility_security_router.routes)" in auth
    assert "router.routes.extend(property_archival_security_router.routes)" in auth


def test_archive_runtime_commits_only_under_exact_lock_and_empty_parent_claims(monkeypatch):
    prop = active_property()
    db = DB(prop)
    released = []
    install_archival_stubs(monkeypatch, db, released)

    result = run(archival.archive_property(str(prop["_id"]), Request()))

    assert result["success"] is True and result["archived"] is True
    query, update = db.properties.updates[0]
    assert query["_id"] == prop["_id"]
    assert query["mutation_lock.token"] == "archive-token"
    assert query["status"] == {"$ne": "rented"}
    rendered = repr(query["$and"])
    assert "current_contract_id" in rendered and "current_tenant_id" in rendered
    assert isinstance(update["$set"]["archived_at"], datetime)
    assert update["$set"]["archived_by"] == "admin@example.com"
    assert released == [(str(prop["_id"]), "archive-token")]


def test_archive_runtime_rejects_nonterminal_contract_and_releases_lock(monkeypatch):
    prop = active_property()
    contract = {"_id": ObjectId(), "property_id": str(prop["_id"]), "status": "pending_activation"}
    db = DB(prop, contract=contract)
    released = []
    install_archival_stubs(monkeypatch, db, released)

    with pytest.raises(HTTPException) as exc:
        run(archival.archive_property(str(prop["_id"]), Request()))

    assert exc.value.status_code == 409
    assert exc.value.detail == "property_archive_contract_conflict"
    assert db.properties.updates == []
    assert released == [(str(prop["_id"]), "archive-token")]


def test_restore_runtime_rejects_lingering_unit_claim_and_releases_lock(monkeypatch):
    prop = active_property(archived_at=datetime.utcnow(), archived_by="admin@example.com")
    unit = {"_id": ObjectId(), "property_id": str(prop["_id"]), "status": "rented", "current_contract_id": str(ObjectId())}
    db = DB(prop, unit=unit)
    released = []
    install_archival_stubs(monkeypatch, db, released)

    with pytest.raises(HTTPException) as exc:
        run(archival.restore_property(str(prop["_id"]), Request()))

    assert exc.value.status_code == 409
    assert exc.value.detail == "property_restore_unit_occupancy_conflict"
    assert db.properties.updates == []
    assert released == [(str(prop["_id"]), "archive-token")]


def test_restore_runtime_exact_token_cas_preserves_history(monkeypatch):
    archived_at = datetime.utcnow()
    prop = active_property(archived_at=archived_at, archived_by="admin@example.com")
    db = DB(prop)
    released = []
    install_archival_stubs(monkeypatch, db, released)

    result = run(archival.restore_property(str(prop["_id"]), Request()))

    assert result["success"] is True and result["archived"] is False
    query, update = db.properties.updates[0]
    assert query["mutation_lock.token"] == "archive-token"
    assert query["archived_at"] == archived_at
    assert query["status"] == {"$ne": "rented"}
    rendered = repr(query["$and"])
    assert "current_contract_id" in rendered and "current_tenant_id" in rendered
    assert update["$unset"] == {"archived_at": "", "archived_by": ""}
    assert isinstance(update["$set"]["restored_at"], datetime)
    assert update["$set"]["restored_by"] == "admin@example.com"
    assert released == [(str(prop["_id"]), "archive-token")]
