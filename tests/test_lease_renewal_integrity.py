import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.lease_renewal_security_router as secure
from rental.auth_metrics import router as security_router
from rental.lease_renewals_router import router as historical_router


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count
        self.upserted_id = None


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, _limit):
        return list(self.docs)


class Collection:
    def __init__(self, docs=None, matched_count=1):
        self.docs = list(docs or [])
        self.matched_count = matched_count
        self.updates = []
        self.find_queries = []

    async def find_one(self, query, *_args, **_kwargs):
        self.find_queries.append(query)
        for doc in self.docs:
            ok = True
            for key, expected in query.items():
                if key == "_id":
                    ok = ok and doc.get("_id") == expected
                elif key == "status":
                    ok = ok and doc.get("status") == expected
                else:
                    ok = ok and doc.get(key) == expected
            if ok:
                return doc
        return None

    def find(self, query):
        self.find_queries.append(query)
        docs = self.docs
        if query:
            docs = [d for d in docs if all(d.get(k) == v for k, v in query.items())]
        return Cursor(docs)

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        return Result(self.matched_count)


class DB:
    def __init__(self, proposal, contract, prop, matched_count=1):
        self.lease_renewal_proposals = Collection([proposal] if proposal else [], matched_count)
        self.rental_contracts = Collection([contract] if contract else [])
        self.properties = Collection([prop] if prop else [])
        self.rent_payments = Collection([])


async def allow_admin(_request=None):
    return {"_id": "admin-1", "role": "admin", "email": "admin@example.com"}


def contract_and_proposal(status="draft"):
    contract_id = ObjectId()
    property_id = ObjectId()
    tenant_id = ObjectId()
    contract = {
        "_id": contract_id,
        "status": "active",
        "property_id": str(property_id),
        "tenant_id": str(tenant_id),
        "tenant_name": "Tenant One",
        "tenant_email": "tenant@example.com",
        "tenant_phone": "8065551212",
        "rent_amount": 1200.0,
        "end_date": "2026-09-30T00:00:00+00:00",
    }
    proposal = {
        "_id": ObjectId(),
        "lease_id": str(contract_id),
        "property_id": str(property_id),
        "tenant_id": str(tenant_id),
        "current_rent": 1200.0,
        "lease_end_date": "2026-09-30T00:00:00+00:00",
        "recommendation": "renew",
        "proposed_rent": 1200.0,
        "status": status,
    }
    prop = {"_id": property_id, "address": "121 Oak Ave"}
    return contract, proposal, prop


def test_renewal_routes_have_one_canonical_security_handler():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_router, prefix="/api")
    expected = {
        ("/api/admin/lease-renewals/proposals", "GET"): "secure_list_proposals",
        ("/api/admin/lease-renewals/refresh/{proposal_id}", "POST"): "secure_refresh_proposal",
        ("/api/admin/lease-renewals/{proposal_id}", "PATCH"): "secure_edit_proposal",
        ("/api/admin/lease-renewals/{proposal_id}/approve", "POST"): "secure_approve_proposal",
        ("/api/admin/lease-renewals/{proposal_id}/reject", "POST"): "secure_reject_proposal",
    }
    for (path, method), name in expected.items():
        matches = [
            route
            for route in app.routes
            if getattr(route, "path", None) == path
            and method in getattr(route, "methods", set())
        ]
        assert len(matches) == 1
        assert matches[0].name == name
    assert historical_router.routes == []


def test_generic_edit_cannot_set_status(monkeypatch):
    contract, proposal, prop = contract_and_proposal()
    db = DB(proposal, contract, prop)
    monkeypatch.setattr(secure, "get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_edit_proposal(str(proposal["_id"]), {"status": "approved"}, db, {"email": "admin@example.com"}))
    assert exc.value.status_code == 409
    assert exc.value.detail == "renewal_status_transition_managed"
    assert db.lease_renewal_proposals.updates == []


def test_approve_requires_active_canonical_contract(monkeypatch):
    contract, proposal, prop = contract_and_proposal()
    contract["status"] = "terminated"
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_approve_proposal(str(proposal["_id"]), db, {"email": "admin@example.com"}))
    assert exc.value.status_code == 409
    assert exc.value.detail == "renewal_contract_not_active"
    assert db.lease_renewal_proposals.updates == []


def test_approve_detects_stale_rent_snapshot(monkeypatch):
    contract, proposal, prop = contract_and_proposal()
    contract["rent_amount"] = 1300.0
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_approve_proposal(str(proposal["_id"]), db, {"email": "admin@example.com"}))
    assert exc.value.status_code == 409
    assert exc.value.detail == "renewal_proposal_stale"


def test_approve_is_draft_only_and_uses_cas(monkeypatch):
    contract, proposal, prop = contract_and_proposal()
    db = DB(proposal, contract, prop)
    result = run(secure.secure_approve_proposal(str(proposal["_id"]), db, {"email": "Admin@Example.com"}))
    assert result == {"ok": True, "status": "approved"}
    query, update, kwargs = db.lease_renewal_proposals.updates[0]
    assert query["_id"] == proposal["_id"]
    assert query["status"] == "draft"
    assert query["lease_id"] == proposal["lease_id"]
    assert update["$set"]["status"] == "approved"
    assert update["$set"]["approved_by"] == "Admin@Example.com"
    assert kwargs == {}


def test_terminal_proposal_cannot_be_rejected_or_edited(monkeypatch):
    contract, proposal, prop = contract_and_proposal("approved")
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_reject_proposal(str(proposal["_id"]), {}, db, {"email": "admin@example.com"}))
    assert exc.value.detail == "renewal_proposal_transition_invalid"
    with pytest.raises(HTTPException) as exc2:
        run(secure.secure_edit_proposal(str(proposal["_id"]), {"proposed_rent": 1250}, db, {"email": "admin@example.com"}))
    assert exc2.value.detail == "renewal_proposal_not_editable"


def test_edit_validates_recommendation_and_rent(monkeypatch):
    contract, proposal, prop = contract_and_proposal()
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(secure.secure_edit_proposal(str(proposal["_id"]), {"recommendation": "evict"}, db, {"email": "admin@example.com"}))
    assert exc.value.detail == "renewal_recommendation_invalid"
    with pytest.raises(HTTPException) as exc2:
        run(secure.secure_edit_proposal(str(proposal["_id"]), {"proposed_rent": -1}, db, {"email": "admin@example.com"}))
    assert exc2.value.detail == "renewal_rent_invalid"


def test_analysis_view_strips_direct_tenant_pii():
    contract, _, _ = contract_and_proposal()
    view = secure._analysis_view(contract, 1200.0)
    assert view["tenant_name"] is None
    assert view["tenant_email"] is None
    assert view["tenant_phone"] is None
    assert view["tenant_id"] == contract["tenant_id"]
    assert view["monthly_rent"] == 1200.0


def test_existing_proposal_skips_analysis_and_upsert(monkeypatch):
    contract, proposal, prop = contract_and_proposal()
    contract["end_date"] = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    db = DB(proposal, contract, prop)
    called = {"analysis": 0}

    async def should_not_run(*_args, **_kwargs):
        called["analysis"] += 1
        raise AssertionError("existing proposal must skip analysis")

    monkeypatch.setattr(secure, "_safe_recommendation", should_not_run)
    result = run(secure.secure_list_proposals(None, db, {"email": "admin@example.com"}))
    assert result["total"] == 1
    assert called["analysis"] == 0
    assert db.lease_renewal_proposals.updates == []


def test_highlights_string_is_not_expanded_into_characters():
    contract, _, prop = contract_and_proposal()
    now = datetime.now(timezone.utc)
    canonical = {
        **contract,
        "_canonical_end": now + timedelta(days=20),
        "_canonical_rent": 1200.0,
        "_property": prop,
    }
    doc = secure._proposal_doc(
        canonical,
        {},
        {"recommendation": "renew", "proposed_rent": 1200, "highlights": "bad-shape"},
        now,
    )
    assert doc["highlights"] == []


def test_secure_boundary_never_mutates_lease_occupancy_or_payments():
    source = open("rental/lease_renewal_security_router.py", encoding="utf-8").read()
    assert "rental_contracts.update_one" not in source
    assert "properties.update_one" not in source
    assert "property_units.update_one" not in source
    assert "rent_payments.update_one" not in source
    assert "current_contract_id" not in source
    assert "current_tenant_id" not in source
    assert "force_activate" not in source


def test_generation_uses_canonical_rental_contracts_not_legacy_leases():
    source = open("rental/lease_renewal_security_router.py", encoding="utf-8").read()
    assert 'db.rental_contracts.find({"status": "active"})' in source
    assert "db.leases" not in source
    assert '"$setOnInsert": proposal' in source
    assert "upsert=True" in source
