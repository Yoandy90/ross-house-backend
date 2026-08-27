from pathlib import Path

from rental.stripe_pkg.reconciliation_resolution_router import (
    ACTIONS_COLLECTION,
    ALLOWED_OUTCOMES,
    _deterministic_object_id,
    _immutable_proposal_payload,
    _proposal_digest,
)


def test_allowed_outcomes_are_decisions_not_execution_actions():
    assert set(ALLOWED_OUTCOMES) == {
        "provider_confirmed_paid",
        "provider_confirmed_not_paid",
        "needs_refund_review",
        "needs_manual_credit_review",
        "dismiss_non_financial",
    }
    assert ACTIONS_COLLECTION == "payment_reconciliation_actions"


def test_proposal_payload_is_explicitly_non_financial():
    payload = _immutable_proposal_payload(
        source="stripe_webhook",
        item_id="event-1",
        exception={
            "status": "amount_mismatch",
            "updated_at": "2026-08-27T18:00:00+00:00",
            "reference_id": "pi_123",
        },
        outcome="provider_confirmed_paid",
        reason="Provider dashboard confirms the remote payment succeeded.",
        evidence_reference="provider-case-42",
        proposer={"id": "admin-a", "email": "a@example.com"},
    )
    assert payload["financial_effect"] == "none"
    assert payload["execution_status"] == "not_executed"
    assert payload["exception_status"] == "amount_mismatch"
    assert payload["exception_updated_at"] == "2026-08-27T18:00:00+00:00"


def test_digest_is_deterministic_and_binds_exact_proposal_content():
    base = {
        "source": "autopay",
        "item_id": "a1",
        "exception_status": "failed_unknown",
        "exception_updated_at": "2026-08-27T18:00:00+00:00",
        "reference_id": "pi_1",
        "outcome": "provider_confirmed_not_paid",
        "reason": "Provider confirms no remote charge was completed.",
        "evidence_reference": "case-1",
        "proposer": {"id": "1", "email": "a@example.com"},
        "financial_effect": "none",
        "execution_status": "not_executed",
    }
    assert _proposal_digest(dict(base)) == _proposal_digest(dict(base))
    changed = dict(base)
    changed["outcome"] = "provider_confirmed_paid"
    assert _proposal_digest(base) != _proposal_digest(changed)


def test_proposal_identity_is_atomic_per_exception_version():
    first = _deterministic_object_id(
        "recon-proposal", "hosted_checkout", "p1", "checkout_creation_unknown", "v1"
    )
    same = _deterministic_object_id(
        "recon-proposal", "hosted_checkout", "p1", "checkout_creation_unknown", "v1"
    )
    newer = _deterministic_object_id(
        "recon-proposal", "hosted_checkout", "p1", "checkout_creation_unknown", "v2"
    )
    assert first == same
    assert first != newer


def test_confirmation_identity_is_atomic_per_proposal():
    proposal = _deterministic_object_id("recon-proposal", "stripe_webhook", "e1", "amount_mismatch", "v1")
    assert _deterministic_object_id("recon-confirmation", proposal) == _deterministic_object_id(
        "recon-confirmation", proposal
    )


def test_router_has_dual_control_and_version_recheck_guards():
    source = Path("rental/stripe_pkg/reconciliation_resolution_router.py").read_text(encoding="utf-8")
    assert 'same_id = bool(' in source
    assert 'same_email = bool(' in source
    assert 'A different admin must confirm this proposal' in source
    assert 'data.get("confirm") is not True' in source
    assert 'hmac.compare_digest' in source
    assert 'Proposal outcome mismatch' in source
    assert 'exception_updated_at' in source
    assert 'Reconciliation item changed; create a new proposal' in source
    assert 'DuplicateKeyError' in source


def test_resolution_module_cannot_mutate_financial_sources_or_call_providers():
    source = Path("rental/stripe_pkg/reconciliation_resolution_router.py").read_text(encoding="utf-8")
    # Append-only workflow records are the only allowed write surface.
    assert source.count("insert_one(") == 2
    assert 'db[ACTIONS_COLLECTION].insert_one(' in source
    for forbidden in (
        "rental_payments.update_one",
        "rental_payments.insert_one",
        "rental_payments.delete_one",
        "autopay_config.update_one",
        "stripe_webhook_events.update_one",
        "PaymentIntent.create",
        "refund",
        "helcim_purchase_with_token",
        "create_hosted_checkout",
    ):
        assert forbidden not in source


def test_public_router_includes_resolution_workflow_once():
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert source.count("from rental.stripe_pkg.reconciliation_resolution_router") == 1
    assert source.count("router.include_router(_reconciliation_resolution_router)") == 1
    assert source.count("router.include_router(_hardened_webhook_router)") == 1
