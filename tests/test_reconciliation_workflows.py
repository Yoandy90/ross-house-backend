from pathlib import Path

import pytest

from rental.stripe_pkg.reconciliation_workflow_router import (
    WORKFLOW_STATES,
    _workflow_state,
    _workflow_summary,
)


def test_workflow_states_are_exact_and_nonfinancial():
    assert WORKFLOW_STATES == (
        "proposed",
        "confirmed",
        "execution_started",
        "executed",
        "requires_review",
    )


def test_workflow_state_progression():
    confirmation = {"_id": "c1"}
    claim = {"_id": "x1", "execution_status": "started"}
    done = {"_id": "r1", "execution_status": "completed"}
    review = {"_id": "r2", "execution_status": "requires_review"}
    assert _workflow_state(None, None, None) == "proposed"
    assert _workflow_state(confirmation, None, None) == "confirmed"
    assert _workflow_state(confirmation, claim, None) == "execution_started"
    assert _workflow_state(confirmation, claim, done) == "executed"
    assert _workflow_state(confirmation, claim, review) == "requires_review"


def test_summary_marks_second_and_third_admin_requirements():
    proposal = {
        "_id": "p1",
        "proposal_digest": "d",
        "source": "stripe_webhook",
        "item_id": "e1",
        "exception_status": "amount_mismatch",
        "exception_updated_at": "v1",
        "outcome": "provider_confirmed_paid",
        "reason": "Evidence reviewed in provider dashboard.",
        "evidence_reference": "case-1",
        "proposer": {"id": "a", "email": "a@example.com"},
        "financial_effect": "none",
        "execution_status": "not_executed",
    }
    proposed = _workflow_summary(proposal, None, None, None)
    assert proposed["requires_second_admin"] is True
    assert proposed["requires_third_admin"] is False
    assert proposed["state"] == "proposed"

    confirmation = {"_id": "c1", "confirmer": {"id": "b", "email": "b@example.com"}}
    confirmed = _workflow_summary(proposal, confirmation, None, None)
    assert confirmed["requires_second_admin"] is False
    assert confirmed["requires_third_admin"] is True
    assert confirmed["state"] == "confirmed"


def test_summary_does_not_spread_arbitrary_source_fields():
    proposal = {
        "_id": "p1",
        "proposal_digest": "d",
        "source": "autopay",
        "item_id": "a1",
        "outcome": "provider_confirmed_not_paid",
        "reason": "Provider confirmed no transaction was completed.",
        "evidence_reference": "case-2",
        "proposer": {"id": "a", "email": "a@example.com"},
        "secret_token": "DO-NOT-RETURN",
        "payment_method_id": "pm_secret",
    }
    summary = _workflow_summary(proposal, None, None, None)
    serialized = repr(summary)
    assert "DO-NOT-RETURN" not in serialized
    assert "pm_secret" not in serialized
    assert "secret_token" not in summary
    assert "payment_method_id" not in summary


def test_workflow_router_is_strictly_read_only():
    source = Path("rental/stripe_pkg/reconciliation_workflow_router.py").read_text(encoding="utf-8")
    for forbidden in (
        "insert_one(", "update_one(", "delete_one(", "replace_one(",
        "find_one_and_update(", "PaymentIntent.create(", "Refund.create(",
        "create_hosted_checkout(", "helcim_purchase_with_token(",
    ):
        assert forbidden not in source
    assert '"read_only": True' in source
    assert '.limit(safe_limit)' in source
    assert 'max(1, min(int(limit or 100), 200))' in source


def test_public_router_includes_workflow_dashboard_once():
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert source.count("from rental.stripe_pkg.reconciliation_workflow_router") == 1
    assert source.count("router.include_router(_reconciliation_workflow_router)") == 1
    assert source.count("router.include_router(_reconciliation_execution_router)") == 1
    assert source.count("router.include_router(_hardened_webhook_router)") == 1
