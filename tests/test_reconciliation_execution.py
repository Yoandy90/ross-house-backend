import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

import rental.stripe_pkg.reconciliation_execution_router as rer


class FakeRequest:
    def __init__(self, data=None):
        self._data = data or {}

    async def json(self):
        return dict(self._data)


class FakeActions:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        if "_id" in query:
            doc = self.docs.get(query["_id"])
            if doc and all(k == "_id" or doc.get(k) == v for k, v in query.items()):
                return dict(doc)
            return None
        for doc in self.docs.values():
            ok = True
            for key, value in query.items():
                if doc.get(key) != value:
                    ok = False
                    break
            if ok:
                return dict(doc)
        return None

    async def insert_one(self, doc):
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("duplicate")
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])


class FakeRentalPayments:
    def __init__(self, invoice=None):
        self.invoice = dict(invoice) if invoice else None
        self.update_calls = []

    async def find_one(self, query):
        if self.invoice is None:
            return None
        if query.get("_id") == self.invoice.get("_id"):
            return dict(self.invoice)
        return None

    async def update_one(self, query, update):
        self.update_calls.append((query, update))
        if self.invoice is None or query.get("_id") != self.invoice.get("_id"):
            return SimpleNamespace(modified_count=0)
        for key in ("amount", "late_fee", "total_paid", "total_due", "updated_at"):
            if key in query and self.invoice.get(key) != query[key]:
                return SimpleNamespace(modified_count=0)
        statuses = (query.get("status") or {}).get("$in", [])
        if statuses and self.invoice.get("status") not in statuses:
            return SimpleNamespace(modified_count=0)
        self.invoice.update(update["$set"])
        return SimpleNamespace(modified_count=1)


class FakeDB:
    def __init__(self, invoice=None):
        self.payment_reconciliation_actions = FakeActions()
        self.rental_payments = FakeRentalPayments(invoice)
        self.autopay_config = SimpleNamespace(find_one=self._none)
        self.stripe_webhook_events = SimpleNamespace(find_one=self._none)

    async def _none(self, *_args, **_kwargs):
        return None

    def __getitem__(self, name):
        assert name == rer.ACTIONS_COLLECTION
        return self.payment_reconciliation_actions


def _proposal(outcome="dismiss_non_financial"):
    oid = ObjectId()
    return {
        "_id": oid,
        "action": "proposal",
        "proposal_digest": "a" * 64,
        "source": "hosted_checkout",
        "item_id": str(ObjectId()),
        "exception_status": "checkout_creation_unknown",
        "exception_updated_at": "2026-08-27T18:00:00+00:00",
        "reference_id": "checkout_123",
        "outcome": outcome,
        "evidence_reference": "provider-case-77",
        "proposer": {"id": "admin-a", "email": "a@example.com"},
    }


def _confirmation(proposal):
    return {
        "_id": ObjectId(),
        "action": "confirmation",
        "proposal_id": str(proposal["_id"]),
        "proposal_digest": proposal["proposal_digest"],
        "source": proposal["source"],
        "item_id": proposal["item_id"],
        "exception_status": proposal["exception_status"],
        "exception_updated_at": proposal["exception_updated_at"],
        "outcome": proposal["outcome"],
        "proposer": proposal["proposer"],
        "confirmer": {"id": "admin-b", "email": "b@example.com"},
    }


def test_same_admin_matches_id_or_normalized_email():
    assert rer._same_admin({"id": "1", "email": "x@a.com"}, {"id": "1", "email": "z@a.com"})
    assert rer._same_admin({"id": "1", "email": "X@A.COM"}, {"id": "2", "email": "x@a.com"})
    assert not rer._same_admin({"id": "1", "email": "x@a.com"}, {"id": "2", "email": "z@a.com"})


def test_partial_invoice_snapshot_and_guard_bind_every_financial_field():
    stamp = object()
    invoice = {
        "_id": ObjectId(),
        "status": "partial",
        "amount": 1000.0,
        "late_fee": 50.0,
        "total_due": 1050.0,
        "total_paid": 400.0,
        "updated_at": stamp,
    }
    snap = rer._invoice_financial_snapshot(invoice)
    assert snap["outstanding_cents"] == 65000
    guard = rer._invoice_update_guard(invoice)
    assert guard["amount"] == 1000.0
    assert guard["late_fee"] == 50.0
    assert guard["total_due"] == 1050.0
    assert guard["total_paid"] == 400.0
    assert guard["updated_at"] is stamp
    assert set(guard["status"]["$in"]) == {"pending", "late", "partial"}


def test_execution_receipt_and_ids_are_deterministic():
    confirmation = ObjectId()
    invoice = str(ObjectId())
    assert rer._execution_claim_id(confirmation) == rer._execution_claim_id(confirmation)
    assert rer._execution_result_id(confirmation) == rer._execution_result_id(confirmation)
    assert rer._execution_claim_id(confirmation) != rer._execution_result_id(confirmation)
    assert rer._execution_receipt(str(confirmation), invoice) == rer._execution_receipt(str(confirmation), invoice)


@pytest.mark.asyncio
async def test_nonfinancial_execution_requires_third_admin_and_never_writes_invoice(monkeypatch):
    proposal = _proposal("dismiss_non_financial")
    confirmation = _confirmation(proposal)
    db = FakeDB()

    async def fake_auth(_request):
        return {"_id": "admin-c", "email": "c@example.com"}

    async def fake_load(_db, _confirmation_id):
        return proposal, confirmation, confirmation["_id"]

    async def fake_readiness(_db, _proposal, _confirmation, _oid):
        return {"can_execute": True}

    monkeypatch.setattr(rer, "auth_admin", fake_auth)
    monkeypatch.setattr(rer, "get_db", lambda: db)
    monkeypatch.setattr(rer, "_load_confirmed_decision", fake_load)
    monkeypatch.setattr(rer, "_readiness", fake_readiness)

    response = await rer.execute_confirmed_reconciliation(
        str(confirmation["_id"]),
        FakeRequest({
            "execute": True,
            "proposal_digest": proposal["proposal_digest"],
            "expected_outcome": proposal["outcome"],
        }),
    )
    assert response["executed"] is True
    assert response["financial_effect"] == "none"
    assert response["provider_calls"] is False
    assert db.rental_payments.update_calls == []
    actions = list(db.payment_reconciliation_actions.docs.values())
    assert [a["action"] for a in actions] == ["execution_claim", "execution_result"]


@pytest.mark.asyncio
async def test_proposer_or_confirmer_cannot_execute(monkeypatch):
    proposal = _proposal("dismiss_non_financial")
    confirmation = _confirmation(proposal)
    db = FakeDB()

    async def fake_load(_db, _confirmation_id):
        return proposal, confirmation, confirmation["_id"]

    async def fake_readiness(_db, _proposal, _confirmation, _oid):
        return {"can_execute": True}

    monkeypatch.setattr(rer, "get_db", lambda: db)
    monkeypatch.setattr(rer, "_load_confirmed_decision", fake_load)
    monkeypatch.setattr(rer, "_readiness", fake_readiness)

    for identity in (proposal["proposer"], confirmation["confirmer"], {"id": "other", "email": "A@EXAMPLE.COM"}):
        async def fake_auth(_request, identity=identity):
            return {"_id": identity["id"], "email": identity["email"]}
        monkeypatch.setattr(rer, "auth_admin", fake_auth)
        with pytest.raises(HTTPException) as exc:
            await rer.execute_confirmed_reconciliation(
                str(confirmation["_id"]),
                FakeRequest({"execute": True, "proposal_digest": proposal["proposal_digest"], "expected_outcome": proposal["outcome"]}),
            )
        assert exc.value.status_code == 403
    assert not db.payment_reconciliation_actions.docs


@pytest.mark.asyncio
async def test_paid_execution_completes_exact_partial_invoice_once(monkeypatch):
    invoice = {
        "_id": ObjectId(),
        "status": "partial",
        "amount": 1000.0,
        "late_fee": 50.0,
        "total_due": 1050.0,
        "total_paid": 400.0,
        "updated_at": "v1",
        "contract_id": "c1",
        "tenant_id": "t1",
    }
    proposal = _proposal("provider_confirmed_paid")
    confirmation = _confirmation(proposal)
    db = FakeDB(invoice)

    async def fake_auth(_request):
        return {"_id": "admin-c", "email": "c@example.com"}

    async def fake_load(_db, _confirmation_id):
        return proposal, confirmation, confirmation["_id"]

    async def fake_readiness(_db, _proposal, _confirmation, _oid):
        return {"can_execute": True}

    async def fake_invoice(_db, _proposal):
        return dict(db.rental_payments.invoice)

    async def fake_find(_collection, value):
        if str(value) == str(db.rental_payments.invoice["_id"]):
            return dict(db.rental_payments.invoice)
        return None

    monkeypatch.setattr(rer, "auth_admin", fake_auth)
    monkeypatch.setattr(rer, "get_db", lambda: db)
    monkeypatch.setattr(rer, "_load_confirmed_decision", fake_load)
    monkeypatch.setattr(rer, "_readiness", fake_readiness)
    monkeypatch.setattr(rer, "_proven_invoice_for_execution", fake_invoice)
    monkeypatch.setattr(rer, "_find_by_id", fake_find)

    response = await rer.execute_confirmed_reconciliation(
        str(confirmation["_id"]),
        FakeRequest({
            "execute": True,
            "proposal_digest": proposal["proposal_digest"],
            "expected_outcome": proposal["outcome"],
            "expected_invoice_id": str(invoice["_id"]),
            "confirmed_amount_cents": 65000,
        }),
    )
    assert response["financial_effect"] == "local_accounting"
    assert response["result"] == "local_invoice_completed"
    assert db.rental_payments.invoice["status"] == "completed"
    assert db.rental_payments.invoice["total_paid"] == 1050.0
    assert db.rental_payments.invoice["payment_method"] == "manual_reconciliation_verified"
    assert len(db.rental_payments.update_calls) == 1
    guard = db.rental_payments.update_calls[0][0]
    assert guard["amount"] == 1000.0
    assert guard["late_fee"] == 50.0
    assert guard["total_due"] == 1050.0
    assert guard["total_paid"] == 400.0
    assert guard["updated_at"] == "v1"

    with pytest.raises(HTTPException) as exc:
        await rer.execute_confirmed_reconciliation(
            str(confirmation["_id"]),
            FakeRequest({
                "execute": True,
                "proposal_digest": proposal["proposal_digest"],
                "expected_outcome": proposal["outcome"],
                "expected_invoice_id": str(invoice["_id"]),
                "confirmed_amount_cents": 65000,
            }),
        )
    assert exc.value.status_code == 409
    assert len(db.rental_payments.update_calls) == 1


@pytest.mark.asyncio
async def test_amount_or_invoice_echo_mismatch_fails_before_execution_claim(monkeypatch):
    invoice = {
        "_id": ObjectId(), "status": "partial", "amount": 1000.0, "late_fee": 50.0,
        "total_due": 1050.0, "total_paid": 400.0,
    }
    proposal = _proposal("provider_confirmed_paid")
    confirmation = _confirmation(proposal)
    db = FakeDB(invoice)

    async def fake_auth(_request):
        return {"_id": "admin-c", "email": "c@example.com"}
    async def fake_load(_db, _confirmation_id):
        return proposal, confirmation, confirmation["_id"]
    async def fake_readiness(_db, _proposal, _confirmation, _oid):
        return {"can_execute": True}
    async def fake_invoice(_db, _proposal):
        return dict(invoice)

    monkeypatch.setattr(rer, "auth_admin", fake_auth)
    monkeypatch.setattr(rer, "get_db", lambda: db)
    monkeypatch.setattr(rer, "_load_confirmed_decision", fake_load)
    monkeypatch.setattr(rer, "_readiness", fake_readiness)
    monkeypatch.setattr(rer, "_proven_invoice_for_execution", fake_invoice)

    bad_payloads = [
        {"expected_invoice_id": str(ObjectId()), "confirmed_amount_cents": 65000},
        {"expected_invoice_id": str(invoice["_id"]), "confirmed_amount_cents": 64999},
        {"expected_invoice_id": str(invoice["_id"]), "confirmed_amount_cents": 65001},
    ]
    for extra in bad_payloads:
        payload = {"execute": True, "proposal_digest": proposal["proposal_digest"], "expected_outcome": proposal["outcome"], **extra}
        with pytest.raises(HTTPException) as exc:
            await rer.execute_confirmed_reconciliation(str(confirmation["_id"]), FakeRequest(payload))
        assert exc.value.status_code == 409
    assert not db.payment_reconciliation_actions.docs
    assert db.rental_payments.update_calls == []


def test_execution_module_has_no_provider_or_refund_calls_and_one_financial_writer():
    from pathlib import Path
    source = Path("rental/stripe_pkg/reconciliation_execution_router.py").read_text(encoding="utf-8")
    for forbidden in (
        "PaymentIntent.create(", "Refund.create(", "helcim_purchase_with_token(",
        "create_hosted_checkout(", "requests.", "httpx.", "delete_one(", "find_one_and_update(",
    ):
        assert forbidden not in source
    assert source.count("db.rental_payments.update_one(") == 1
    assert source.count("db[ACTIONS_COLLECTION].insert_one(") == 2


def test_public_router_includes_execution_workflow_once():
    from pathlib import Path
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert source.count("from rental.stripe_pkg.reconciliation_execution_router") == 1
    assert source.count("router.include_router(_reconciliation_execution_router)") == 1
    assert source.count("router.include_router(_hardened_webhook_router)") == 1
