import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.tenant_integrity as integrity


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)
    def limit(self, n):
        self.docs = self.docs[:n]
        return self
    async def to_list(self, n):
        return self.docs[:n]


class Collection:
    def __init__(self, docs):
        self.docs = list(docs)
        self.indexes = []
    async def find_one(self, query):
        if "_id" in query:
            return next((d for d in self.docs if d.get("_id") == query["_id"]), None)
        return None
    async def create_index(self, key, **kwargs):
        self.indexes.append((key, kwargs))
        return kwargs.get("name") or str(key)
    def find(self, query):
        docs = self.docs
        if "app_user_id" in query:
            docs = [d for d in docs if d.get("app_user_id") == query["app_user_id"]]
        elif "tenant_id" in query:
            allowed = query["tenant_id"]["$in"]
            docs = [d for d in docs if d.get("tenant_id") in allowed and d.get("status") == query["status"]]
        elif "email_normalized" in query:
            docs = [d for d in docs if d.get("email_normalized") == query["email_normalized"]]
        elif "phone_normalized" in query:
            docs = [d for d in docs if d.get("phone_normalized") == query["phone_normalized"]]
        elif "email" in query:
            docs = [d for d in docs if d.get("email")]
        elif "phone" in query:
            docs = [d for d in docs if d.get("phone")]
        return Cursor(docs)


def run(coro):
    return asyncio.run(coro)


def set_db(monkeypatch, tenants, contracts=()):
    db = SimpleNamespace(tenants=Collection(tenants), rental_contracts=Collection(contracts))
    monkeypatch.setattr(integrity, "get_db", lambda: db)
    return db


def test_identity_indexes_are_non_unique_and_named(monkeypatch):
    db = set_db(monkeypatch, [])
    run(integrity.ensure_tenant_identity_indexes())
    names = {kwargs.get("name") for _, kwargs in db.tenants.indexes}
    assert names == {
        "tenant_email_normalized_lookup",
        "tenant_phone_normalized_lookup",
        "tenant_app_user_lookup",
    }
    assert all(kwargs.get("unique") is not True for _, kwargs in db.tenants.indexes)


def test_app_user_link_is_authoritative(monkeypatch):
    tid = ObjectId()
    set_db(monkeypatch, [{"_id": tid, "app_user_id": "u1", "email": "same@example.com"}])
    tenant = run(integrity.resolve_authenticated_tenant({"_id": "u1", "email": "other@example.com"}))
    assert tenant["_id"] == tid


def test_indexed_normalized_email_resolves_without_legacy_dependency(monkeypatch):
    tid = ObjectId()
    set_db(monkeypatch, [{
        "_id": tid,
        "email": "Old-Casing@Example.com",
        "email_normalized": "a@example.com",
    }])
    tenant = run(integrity.find_unique_tenant_by_email(
        "A@EXAMPLE.COM",
        ambiguity_detail="ambiguous",
    ))
    assert tenant["_id"] == tid


def test_duplicate_indexed_email_fails_closed(monkeypatch):
    set_db(monkeypatch, [
        {"_id": ObjectId(), "email_normalized": "a@example.com"},
        {"_id": ObjectId(), "email_normalized": "a@example.com"},
    ])
    with pytest.raises(HTTPException) as exc:
        run(integrity.find_unique_tenant_by_email(
            "a@example.com",
            ambiguity_detail="tenant_identity_ambiguous_email",
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_identity_ambiguous_email"


def test_duplicate_legacy_email_fails_closed(monkeypatch):
    set_db(monkeypatch, [
        {"_id": ObjectId(), "email": "A@Example.com"},
        {"_id": ObjectId(), "email": "a@example.com"},
    ])
    with pytest.raises(HTTPException) as exc:
        run(integrity.resolve_authenticated_tenant({"_id": "u1", "email": "a@example.com"}))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_identity_ambiguous_email"


def test_duplicate_normalized_phone_fails_closed(monkeypatch):
    set_db(monkeypatch, [
        {"_id": ObjectId(), "phone": "+1 (806) 555-0100"},
        {"_id": ObjectId(), "phone": "18065550100"},
    ])
    with pytest.raises(HTTPException) as exc:
        run(integrity.resolve_authenticated_tenant({"_id": "u1", "phone": "1-806-555-0100"}))
    assert exc.value.detail == "tenant_identity_ambiguous_phone"


def test_legacy_identity_scan_fails_closed_if_unbounded(monkeypatch):
    set_db(monkeypatch, [
        {"_id": ObjectId(), "phone": f"806555{i:04d}"}
        for i in range(1001)
    ])
    with pytest.raises(HTTPException) as exc:
        run(integrity.find_unique_tenant_by_phone(
            "18065550100",
            ambiguity_detail="tenant_identity_ambiguous_phone",
        ))
    assert exc.value.detail == "tenant_identity_legacy_phone_unbounded"


def test_multiple_active_contracts_fail_closed(monkeypatch):
    tid = ObjectId()
    set_db(monkeypatch, [], [
        {"_id": ObjectId(), "tenant_id": str(tid), "status": "active"},
        {"_id": ObjectId(), "tenant_id": tid, "status": "active"},
    ])
    with pytest.raises(HTTPException) as exc:
        run(integrity.find_active_contract_for_tenant({"_id": tid}))
    assert exc.value.status_code == 409
    assert exc.value.detail == "tenant_multiple_active_contracts"
