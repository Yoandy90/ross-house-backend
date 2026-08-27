from pathlib import Path

from rental.stripe_pkg.reconciliation_recovery_router import (
    RECOVERY_CLASSES,
    _classify_recovery,
    _same_financial_snapshot,
)


def test_recovery_classes_are_exact():
    assert RECOVERY_CLASSES == (
        "result_recorded",
        "financial_write_applied_result_missing",
        "no_financial_write_detected",
        "record_only_result_missing",
        "ambiguous_state",
    )


def test_result_recorded_wins():
    claim = {"_id": "c1", "financial_effect": "local_accounting_pending"}
    result = {"_id": "r1", "execution_status": "completed"}
    classification, guidance = _classify_recovery(claim, result, None)
    assert classification == "result_recorded"
    assert "no recovery" in guidance.lower()


def test_applied_invoice_is_detected_by_execution_claim_marker():
    claim = {"_id": "claim-1", "financial_effect": "local_accounting_pending"}
    invoice = {"status": "completed", "reconciliation_execution_claim_id": "claim-1"}
    classification, guidance = _classify_recovery(claim, None, invoice)
    assert classification == "financial_write_applied_result_missing"
    assert "do not retry" in guidance.lower()


def test_record_only_missing_result_cannot_be_mistaken_for_financial_write():
    claim = {"_id": "claim-2", "financial_effect": "none"}
    classification, guidance = _classify_recovery(claim, None, {"status": "completed"})
    assert classification == "record_only_result_missing"
    assert "no financial write" in guidance.lower()


def test_unchanged_invoice_snapshot_classifies_no_write_detected():
    before = {
        "id": "inv-1", "status": "partial", "amount": 1000.0, "late_fee": 50.0,
        "total_due": 1050.0, "total_paid": 400.0, "updated_at": "v1",
    }
    claim = {"_id": "claim-3", "financial_effect": "local_accounting_pending", "before": before}
    invoice = {
        "_id": "inv-1", "status": "partial", "amount": 1000.0, "late_fee": 50.0,
        "total_due": 1050.0, "total_paid": 400.0, "updated_at": "v1",
    }
    classification, guidance = _classify_recovery(claim, None, invoice)
    assert classification == "no_financial_write_detected"
    assert "do not retry automatically" in guidance.lower()


def test_changed_or_unprovable_state_is_ambiguous():
    before = {"id": "inv-1", "status": "partial", "amount": 1000.0, "late_fee": 50.0,
              "total_due": 1050.0, "total_paid": 400.0, "updated_at": "v1"}
    claim = {"_id": "claim-4", "financial_effect": "local_accounting_pending", "before": before}
    changed = {"_id": "inv-1", "status": "partial", "amount": 1000.0, "late_fee": 50.0,
               "total_due": 1050.0, "total_paid": 500.0, "updated_at": "v2"}
    classification, guidance = _classify_recovery(claim, None, changed)
    assert classification == "ambiguous_state"
    assert "do not retry" in guidance.lower()


def test_snapshot_comparison_binds_financial_and_version_fields():
    before = {"id": "i", "status": "partial", "amount": 1000, "late_fee": 50,
              "total_due": 1050, "total_paid": 400, "updated_at": "v1"}
    assert _same_financial_snapshot(before, dict(before))
    for key, value in (
        ("status", "late"), ("amount", 999), ("late_fee", 0),
        ("total_due", 1000), ("total_paid", 401), ("updated_at", "v2"),
    ):
        changed = dict(before)
        changed[key] = value
        assert not _same_financial_snapshot(before, changed)


def test_recovery_router_is_strictly_read_only_and_never_allows_retry():
    source = Path("rental/stripe_pkg/reconciliation_recovery_router.py").read_text(encoding="utf-8")
    for forbidden in (
        "insert_one(", "update_one(", "delete_one(", "replace_one(",
        "find_one_and_update(", "PaymentIntent.create(", "Refund.create(",
        "create_hosted_checkout(", "helcim_purchase_with_token(",
    ):
        assert forbidden not in source
    assert '"automatic_retry_allowed": False' in source
    assert '"provider_calls": False' in source
    assert '"read_only": True' in source


def test_public_router_includes_recovery_router_once():
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert source.count("from rental.stripe_pkg.reconciliation_recovery_router") == 1
    assert source.count("router.include_router(_reconciliation_recovery_router)") == 1
    assert source.count("router.include_router(_hardened_webhook_router)") == 1
