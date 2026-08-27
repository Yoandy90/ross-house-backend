from types import SimpleNamespace

import pytest
from bson import ObjectId

import rental.stripe_pkg.reconciliation_execution_router as rer
from rental.stripe_pkg.reconciliation_workflow_router import _workflow_state


class FakeRequest:
    def __init__(self, data):
        self.data = data

    async def json(self):
        return dict(self.data)


class Actions:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])


class DB:
    def __init__(self):
        self.payment_reconciliation_actions = Actions()
        self.rental_payments = SimpleNamespace(update_one=self._no_write)

    async def _no_write(self, *_args, **_kwargs):
        raise AssertionError("review-only outcomes must never write rental_payments")

    def __getitem__(self, name):
        assert name == rer.ACTIONS_COLLECTION
        return self.payment_reconciliation_actions


def proposal(outcome):
    return {
        "_id": ObjectId(),
        "proposal_digest": "d" * 64,
        "source": "stripe_webhook",
        "item_id": "event-1",
        "exception_status": "amount_mismatch",
        "exception_updated_at": "v1",
        "reference_id": "pi_1",
        "outcome": outcome,
        "proposer": {"id": "a", "email": "a@example.com"},
    }


def confirmation(p):
    return {
        "_id": ObjectId(),
        "proposal_id": str(p["_id"]),
        "proposal_digest": p["proposal_digest"],
        "source": p["source"],
        "item_id": p["item_id"],
        "exception_status": p["exception_status"],
        "exception_updated_at": p["exception_updated_at"],
        "outcome": p["outcome"],
        "confirmer": {"id": "b", "email": "b@example.com"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,expected_result",
    [
        ("needs_refund_review", "refund_review_required"),
        ("needs_manual_credit_review", "manual_credit_review_required"),
    ],
)
async def test_review_only_outcomes_return_successful_review_state_without_financial_write(monkeypatch, outcome, expected_result):
    p = proposal(outcome)
    c = confirmation(p)
    db = DB()

    async def auth(_request):
        return {"_id": "c", "email": "c@example.com"}

    async def load(_db, _cid):
        return p, c, c["_id"]

    async def ready(_db, _p, _c, _oid):
        return {"can_execute": True}

    monkeypatch.setattr(rer, "auth_admin", auth)
    monkeypatch.setattr(rer, "get_db", lambda: db)
    monkeypatch.setattr(rer, "_load_confirmed_decision", load)
    monkeypatch.setattr(rer, "_readiness", ready)

    response = await rer.execute_confirmed_reconciliation(
        str(c["_id"]),
        FakeRequest({
            "execute": True,
            "proposal_digest": p["proposal_digest"],
            "expected_outcome": outcome,
        }),
    )

    assert response["executed"] is False
    assert response["review_required"] is True
    assert response["execution_status"] == "requires_review"
    assert response["financial_effect"] == "none"
    assert response["provider_calls"] is False
    assert response["result"] == expected_result

    result = next(doc for doc in db.payment_reconciliation_actions.docs.values() if doc["action"] == "execution_result")
    assert result["execution_status"] == "requires_review"
    assert result["financial_effect"] == "none"
    assert _workflow_state(c, {"created_at": result["executed_at"]}, result) == "requires_review"


def test_any_noncompleted_result_fails_closed_to_requires_review():
    confirmation_doc = {"_id": "c"}
    claim = {"created_at": "2026-08-27T20:00:00+00:00"}
    assert _workflow_state(confirmation_doc, claim, {"execution_status": "requires_review"}) == "requires_review"
    assert _workflow_state(confirmation_doc, claim, {"execution_status": "unknown_future_status"}) == "requires_review"
    assert _workflow_state(confirmation_doc, claim, {"execution_status": "completed"}) == "executed"
