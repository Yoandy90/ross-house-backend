import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.lease_renewal_notification_security_router as notify
from rental.auth_metrics import router as security_router
from rental.lease_renewal_security_router import router as renewal_router


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, matched_count=1, upserted_id=None):
        self.matched_count = matched_count
        self.upserted_id = upserted_id


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, limit):
        return list(self.docs)[:limit]


class Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []

    async def find_one(self, query):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    def find(self, query):
        docs = [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]
        return Cursor(docs)

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    doc.update(update["$set"])
                return Result(matched_count=1)
        if kwargs.get("upsert") and "$setOnInsert" in update:
            created = dict(update["$setOnInsert"])
            created.setdefault("_id", ObjectId())
            self.docs.append(created)
            return Result(matched_count=0, upserted_id=created["_id"])
        return Result(matched_count=0)


class DB:
    def __init__(self, proposal, contract, prop):
        self.lease_renewal_proposals = Collection([proposal])
        self.rental_contracts = Collection([contract])
        self.properties = Collection([prop])
        self.lease_renewal_notification_outbox = Collection([])


def fixture(status="draft", recommendation="renew"):
    contract_id = ObjectId()
    property_id = ObjectId()
    tenant_id = ObjectId()
    end = datetime.now(timezone.utc) + timedelta(days=30)
    contract = {
        "_id": contract_id,
        "status": "active",
        "property_id": str(property_id),
        "tenant_id": str(tenant_id),
        "tenant_name": "Tenant One",
        "tenant_email": "tenant@example.com",
        "tenant_phone": "8065551212",
        "rent_amount": 1200.0,
        "end_date": end.isoformat(),
    }
    proposal = {
        "_id": ObjectId(),
        "lease_id": str(contract_id),
        "property_id": str(property_id),
        "tenant_id": str(tenant_id),
        "current_rent": 1200.0,
        "lease_end_date": end.isoformat(),
        "recommendation": recommendation,
        "proposed_rent": 1200.0,
        "status": status,
    }
    prop = {"_id": property_id, "address": "121 Oak Ave"}
    return contract, proposal, prop


def test_notification_approve_wins_route_precedence():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(renewal_router, prefix="/api")
    matches = [
        r for r in app.routes
        if getattr(r, "path", None) == "/api/admin/lease-renewals/{proposal_id}/approve"
        and "POST" in getattr(r, "methods", set())
    ]
    assert len(matches) == 2
    assert matches[0].name == "secure_approve_proposal"


def test_draft_approval_creates_single_outbox_intent_without_direct_pii():
    contract, proposal, prop = fixture()
    db = DB(proposal, contract, prop)
    result = run(notify.secure_approve_and_queue(
        str(proposal["_id"]), db, {"email": "admin@example.com"}
    ))
    assert result["status"] == "approved"
    assert result["notification_queued"] is True
    assert result["queued_now"] is True
    assert proposal["status"] == "approved"
    assert len(db.lease_renewal_notification_outbox.docs) == 1
    outbox = db.lease_renewal_notification_outbox.docs[0]
    assert outbox["proposal_id"] == str(proposal["_id"])
    assert outbox["tenant_id"] == contract["tenant_id"]
    assert "tenant_email" not in outbox
    assert "tenant_phone" not in outbox
    assert "tenant_name" not in outbox
    assert outbox["status"] == "pending"


def test_repeated_approved_call_is_idempotent_and_repairs_missing_outbox():
    contract, proposal, prop = fixture(status="approved")
    db = DB(proposal, contract, prop)
    first = run(notify.secure_approve_and_queue(
        str(proposal["_id"]), db, {"email": "admin@example.com"}
    ))
    second = run(notify.secure_approve_and_queue(
        str(proposal["_id"]), db, {"email": "admin@example.com"}
    ))
    assert first["queued_now"] is True
    assert second["queued_now"] is False
    assert len(db.lease_renewal_notification_outbox.docs) == 1


def test_reject_terminal_state_cannot_queue_notification():
    contract, proposal, prop = fixture(status="rejected")
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(notify.secure_approve_and_queue(
            str(proposal["_id"]), db, {"email": "admin@example.com"}
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail == "renewal_proposal_transition_invalid"
    assert db.lease_renewal_notification_outbox.docs == []


def test_stale_rent_blocks_outbox_creation():
    contract, proposal, prop = fixture(status="approved")
    contract["rent_amount"] = 1300.0
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(notify.secure_approve_and_queue(
            str(proposal["_id"]), db, {"email": "admin@example.com"}
        ))
    assert exc.value.detail == "renewal_proposal_stale"
    assert db.lease_renewal_notification_outbox.docs == []


def test_nonrenew_message_does_not_claim_eviction_or_termination():
    contract, proposal, prop = fixture(status="approved", recommendation="non_renew")
    canonical = {
        **contract,
        "_canonical_end": datetime.fromisoformat(contract["end_date"]),
        "_canonical_rent": 1200.0,
        "_property": prop,
    }
    message = notify._message_for(proposal, canonical)
    lower = message["body"].lower()
    assert "eviction" not in lower
    assert "desalojo" not in lower
    assert "terminado" not in lower
    assert "opciones" in lower


def test_outbox_listing_validates_status_and_caps_limit():
    contract, proposal, prop = fixture(status="approved")
    db = DB(proposal, contract, prop)
    with pytest.raises(HTTPException) as exc:
        run(notify.list_notification_outbox("unknown", 100, db, {"email": "admin@example.com"}))
    assert exc.value.detail == "renewal_notification_status_invalid"


def test_outbox_source_has_no_external_send_calls():
    source = open("rental/lease_renewal_notification_security_router.py", encoding="utf-8").read()
    assert "sendgrid" not in source.lower()
    assert "twilio" not in source.lower()
    assert ".messages.create" not in source
    assert "mail.send" not in source
