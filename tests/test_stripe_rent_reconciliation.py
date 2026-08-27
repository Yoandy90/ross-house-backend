import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from bson import ObjectId

from rental.stripe_pkg.rent_reconciliation import (
    reconcile_succeeded_rent_payment,
    stripe_payment_identity_query,
)


class FakePayments:
    def __init__(self, docs):
        self.docs = docs
        self.insert_calls = 0
        self.update_calls = 0

    @staticmethod
    def _status_matches(doc, wanted):
        if isinstance(wanted, dict) and "$in" in wanted:
            return doc.get("status") in wanted["$in"]
        return doc.get("status") == wanted

    def _matches(self, doc, query):
        if "$or" in query:
            base = {k: v for k, v in query.items() if k != "$or"}
            if not self._matches(doc, base):
                return False
            return any(self._matches(doc, q) for q in query["$or"])
        for key, value in query.items():
            if key == "status":
                if not self._status_matches(doc, value):
                    return False
            elif key == "period_month" and isinstance(value, dict) and "$regex" in value:
                if not str(doc.get(key, "")).lower().startswith(value["$regex"].lstrip("^").lower()):
                    return False
            elif doc.get(key) != value:
                return False
        return True

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    async def update_one(self, query, update):
        self.update_calls += 1
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(update["$set"])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def insert_one(self, _doc):
        self.insert_calls += 1
        raise AssertionError("reconciliation must never insert rental_payments")


class FakeDB:
    def __init__(self, docs):
        self.rental_payments = FakePayments(docs)


def invoice(*, status="partial", total_due=1050, total_paid=400, tenant_id="tenant-1"):
    return {
        "_id": ObjectId(),
        "contract_id": "contract-1",
        "tenant_id": tenant_id,
        "period": "2026-08",
        "period_year": 2026,
        "period_month_num": 8,
        "period_month": "August",
        "amount": 1000.0,
        "late_fee": 50.0,
        "total_due": float(total_due),
        "total_paid": float(total_paid),
        "status": status,
    }


def meta(doc):
    return {
        "invoice_id": str(doc["_id"]),
        "contract_id": doc["contract_id"],
        "tenant_id": doc["tenant_id"],
        "period_year": "2026",
        "period_month": "august",
    }


def run(coro):
    return asyncio.run(coro)


def test_identity_query_covers_all_historical_fields():
    q = stripe_payment_identity_query("pi_123")
    assert q == {"$or": [
        {"stripe_payment_intent_id": "pi_123"},
        {"stripe_payment_intent": "pi_123"},
        {"reference_number": "pi_123"},
    ]}


def test_partial_invoice_settles_exact_outstanding_without_insert():
    doc = invoice()
    db = FakeDB([doc])
    result = run(reconcile_succeeded_rent_payment(
        db,
        payment_intent_id="pi_partial",
        amount_cents=65000,
        metadata=meta(doc),
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    ))
    assert result["settled"] is True
    assert result["status"] == "completed"
    assert doc["status"] == "completed"
    assert doc["total_paid"] == 1050.0
    assert doc["amount"] == 1000.0
    assert doc["late_fee"] == 50.0
    assert doc["stripe_payment_intent_id"] == "pi_partial"
    assert db.rental_payments.insert_calls == 0


def test_late_invoice_settles_full_server_balance():
    doc = invoice(status="late", total_due=1075, total_paid=0)
    doc["late_fee"] = 75.0
    db = FakeDB([doc])
    result = run(reconcile_succeeded_rent_payment(
        db, payment_intent_id="pi_late", amount_cents=107500, metadata=meta(doc)))
    assert result["settled"] is True
    assert doc["total_paid"] == 1075.0
    assert doc["late_fee"] == 75.0


def test_amount_mismatch_fails_closed_without_write():
    doc = invoice()
    db = FakeDB([doc])
    result = run(reconcile_succeeded_rent_payment(
        db, payment_intent_id="pi_bad", amount_cents=100, metadata=meta(doc)))
    assert result["status"] == "amount_mismatch"
    assert result["expected_cents"] == 65000
    assert doc["status"] == "partial"
    assert db.rental_payments.update_calls == 0
    assert db.rental_payments.insert_calls == 0


def test_existing_pi_is_idempotent_and_does_not_recredit():
    doc = invoice(status="completed", total_due=1050, total_paid=1050)
    doc["stripe_payment_intent_id"] = "pi_done"
    db = FakeDB([doc])
    result = run(reconcile_succeeded_rent_payment(
        db, payment_intent_id="pi_done", amount_cents=65000, metadata=meta(doc)))
    assert result["status"] == "duplicate"
    assert result["settled"] is True
    assert db.rental_payments.update_calls == 0


def test_tenant_mismatch_fails_closed():
    doc = invoice(tenant_id="tenant-real")
    m = meta(doc)
    m["tenant_id"] = "tenant-other"
    db = FakeDB([doc])
    result = run(reconcile_succeeded_rent_payment(
        db, payment_intent_id="pi_wrong_tenant", amount_cents=65000, metadata=m))
    assert result["status"] == "tenant_mismatch"
    assert db.rental_payments.update_calls == 0


def test_legacy_pi_without_invoice_id_can_match_period():
    doc = invoice(status="pending", total_due=1050, total_paid=0)
    db = FakeDB([doc])
    legacy_meta = {
        "contract_id": "contract-1",
        "tenant_id": "tenant-1",
        "period_year": "2026",
        "period_month": "august",
    }
    result = run(reconcile_succeeded_rent_payment(
        db, payment_intent_id="pi_legacy", amount_cents=105000, metadata=legacy_meta))
    assert result["settled"] is True
    assert doc["status"] == "completed"
    assert db.rental_payments.insert_calls == 0


def test_missing_or_ambiguous_invoice_never_creates_completed_row():
    db = FakeDB([])
    result = run(reconcile_succeeded_rent_payment(
        db,
        payment_intent_id="pi_missing",
        amount_cents=105000,
        metadata={"contract_id": "contract-1", "tenant_id": "tenant-1", "period_year": "2026"},
    ))
    assert result["settled"] is False
    assert result["status"] in {"invoice_not_found", "invalid_metadata"}
    assert db.rental_payments.insert_calls == 0
