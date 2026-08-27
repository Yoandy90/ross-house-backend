from datetime import datetime, timedelta, timezone
from pathlib import Path

from rental.stripe_pkg.reconciliation_workflow_router import (
    EXECUTION_STALE_SECONDS,
    MAX_WORKFLOW_SCAN,
    OUTCOME_CAPABILITIES,
    WORKFLOW_STATES,
    _execution_capability,
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
    assert MAX_WORKFLOW_SCAN == 1000


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


def test_stale_execution_claim_becomes_requires_review_without_mutation():
    now = datetime(2026, 8, 27, 20, 0, tzinfo=timezone.utc)
    fresh = {"_id": "x1", "created_at": now - timedelta(seconds=EXECUTION_STALE_SECONDS - 1)}
    stale = {"_id": "x2", "created_at": now - timedelta(seconds=EXECUTION_STALE_SECONDS)}
    confirmation = {"_id": "c1"}
    assert _workflow_state(confirmation, fresh, None, now=now) == "execution_started"
    assert _workflow_state(confirmation, stale, None, now=now) == "requires_review"


def test_outcome_capabilities_never_include_provider_calls():
    assert set(OUTCOME_CAPABILITIES) == {
        "provider_confirmed_paid",
        "provider_confirmed_not_paid",
        "needs_refund_review",
        "needs_manual_credit_review",
        "dismiss_non_financial",
    }
    paid = _execution_capability("provider_confirmed_paid")
    assert paid == {
        "mode": "local_invoice_completion",
        "financial_write": True,
        "requires_exact_invoice": True,
        "provider_call": False,
    }
    for outcome, capability in OUTCOME_CAPABILITIES.items():
        assert capability["provider_call"] is False
        if outcome != "provider_confirmed_paid":
            assert capability["financial_write"] is False


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
    assert proposed["capability"]["requires_exact_invoice"] is True

    confirmation = {"_id": "c1", "confirmer": {"id": "b", "email": "b@example.com"}}
    confirmed = _workflow_summary(proposal, confirmation, None, None)
    assert confirmed["requires_second_admin"] is False
    assert confirmed["requires_third_admin"] is True
    assert confirmed["state"] == "confirmed"


def test_summary_marks_stale_claim_as_recovery_required():
    now = datetime(2026, 8, 27, 20, 0, tzinfo=timezone.utc)
    proposal = {"_id": "p1", "source": "autopay", "item_id": "a1", "outcome": "provider_confirmed_not_paid"}
    confirmation = {"_id": "c1"}
    claim = {"_id": "x1", "created_at": now - timedelta(minutes=10), "executor": {"id": "c"}}
    summary = _workflow_summary(proposal, confirmation, claim, None, now=now)
    assert summary["state"] == "requires_review"
    assert summary["recovery_required"] is True
    assert summary["execution_claim_age_seconds"] == 600


def test_summary_does_not_spread_arbitrary_source_fields():
    proposal = {
        "_id": "p1", "proposal_digest": "d", "source": "autopay", "item_id": "a1",
        "outcome": "provider_confirmed_not_paid",
        "reason": "Provider confirmed no transaction was completed.",
        "evidence_reference": "case-2",
        "proposer": {"id": "a", "email": "a@example.com"},
        "secret_token": "DO-NOT-RETURN", "payment_method_id": "pm_secret",
    }
    summary = _workflow_summary(proposal, None, None, None)
    serialized = repr(summary)
    assert "DO-NOT-RETURN" not in serialized
    assert "pm_secret" not in serialized
    assert "secret_token" not in summary
    assert "payment_method_id" not in summary


def test_workflow_router_is_strictly_read_only_and_scans_are_bounded():
    source = Path("rental/stripe_pkg/reconciliation_workflow_router.py").read_text(encoding="utf-8")
    for forbidden in (
        "insert_one(", "update_one(", "delete_one(", "replace_one(",
        "find_one_and_update(", "PaymentIntent.create(", "Refund.create(",
        "create_hosted_checkout(", "helcim_purchase_with_token(",
    ):
        assert forbidden not in source
    assert '"read_only": True' in source
    assert '.limit(scan_limit)' in source
    assert 'MAX_WORKFLOW_SCAN = 1000' in source
    assert 'max(1, min(int(limit or 100), 200))' in source
    assert 'min(MAX_WORKFLOW_SCAN' in source


def test_public_router_includes_workflow_dashboard_once():
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert source.count("from rental.stripe_pkg.reconciliation_workflow_router") == 1
    assert source.count("router.include_router(_reconciliation_workflow_router)") == 1
    assert source.count("router.include_router(_reconciliation_execution_router)") == 1
    assert source.count("router.include_router(_hardened_webhook_router)") == 1
