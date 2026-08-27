from datetime import datetime, timedelta, timezone
from pathlib import Path

from rental.stripe_pkg.reconciliation_queue_router import (
    AUTOPAY_RECONCILIATION_STATUSES,
    HOSTED_RECONCILIATION_STATUSES,
    STRIPE_RECONCILIATION_STATUSES,
    _autopay_item,
    _hosted_item,
    _priority,
    _stripe_item,
)


def test_reconciliation_status_sets_are_fail_closed_and_specific():
    assert set(HOSTED_RECONCILIATION_STATUSES) == {
        "creating_checkout",
        "checkout_creation_unknown",
    }
    assert set(STRIPE_RECONCILIATION_STATUSES) == {
        "amount_mismatch",
        "invoice_not_found",
        "tenant_mismatch",
        "invalid_metadata",
    }
    assert set(AUTOPAY_RECONCILIATION_STATUSES) == {
        "failed_unknown",
        "reconciliation_required",
    }


def test_hosted_mapper_exposes_only_reconciliation_fields():
    item = _hosted_item({
        "_id": "p1",
        "status": "checkout_creation_unknown",
        "checkout_processor": "square",
        "contract_id": "c1",
        "tenant_id": "t1",
        "invoice_id": "i1",
        "total_paid": 650,
        "period": "2026-08",
        "checkout_order_id": "ord1",
        "checkout_url": "https://secret-provider-url.example/session",
        "provider_secret": "do-not-leak",
        "updated_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
    })
    assert item["source"] == "hosted_checkout"
    assert item["reference_id"] == "ord1"
    assert "checkout_url" not in item
    assert "provider_secret" not in item


def test_stripe_mapper_does_not_expose_payload_or_metadata():
    item = _stripe_item({
        "_id": "e1",
        "event_id": "evt_1",
        "account_id": "pi_1",
        "reconciliation_status": "amount_mismatch",
        "payload": {"card": "sensitive"},
        "metadata": {"tenant_name": "hidden"},
        "processed_at": datetime(2026, 8, 27, tzinfo=timezone.utc),
    })
    assert item["source"] == "stripe_webhook"
    assert item["reference_id"] == "pi_1"
    assert "payload" not in item
    assert "metadata" not in item


def test_autopay_mapper_never_exposes_saved_payment_credentials():
    item = _autopay_item({
        "_id": "a1",
        "last_attempt_status": "reconciliation_required",
        "processor": "helcim",
        "user_id": "t1",
        "last_attempt_amount": 650,
        "last_attempt_intent_id": "tx1",
        "payment_method_id": "pm_secret",
        "helcim_card_token": "card_token_secret",
        "helcim_customer_code": "customer_secret",
    })
    assert item["source"] == "autopay"
    assert item["reference_id"] == "tx1"
    assert "payment_method_id" not in item
    assert "helcim_card_token" not in item
    assert "helcim_customer_code" not in item


def test_priority_keeps_fresh_creating_checkout_low_then_escalates_with_age():
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    fresh = _priority({
        "status": "creating_checkout",
        "updated_at": (now - timedelta(seconds=30)).isoformat(),
    }, now)
    medium = _priority({
        "status": "creating_checkout",
        "updated_at": (now - timedelta(minutes=10)).isoformat(),
    }, now)
    high = _priority({
        "status": "creating_checkout",
        "updated_at": (now - timedelta(hours=2)).isoformat(),
    }, now)
    assert fresh["severity"] == "low"
    assert medium["severity"] == "medium"
    assert high["severity"] == "high"
    assert fresh["age_seconds"] == 30


def test_priority_reconciliation_required_is_critical_and_old_high_escalates():
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    critical = _priority({
        "status": "reconciliation_required",
        "updated_at": now.isoformat(),
    }, now)
    aged = _priority({
        "status": "amount_mismatch",
        "updated_at": (now - timedelta(days=2)).isoformat(),
    }, now)
    assert critical["severity"] == "critical"
    assert aged["severity"] == "critical"
    assert aged["age_seconds"] == 172800


def test_priority_handles_naive_datetime_and_unknown_timestamp_fail_safely():
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    naive = _priority({
        "status": "invalid_metadata",
        "updated_at": datetime(2026, 8, 27, 17, 0),
    }, now)
    unknown = _priority({"status": "invalid_metadata", "updated_at": "not-a-date"}, now)
    assert naive["age_seconds"] == 3600
    assert naive["severity"] == "medium"
    assert unknown["age_seconds"] is None
    assert unknown["severity"] == "medium"


def test_router_is_admin_only_and_read_only_by_contract():
    source = Path("rental/stripe_pkg/reconciliation_queue_router.py").read_text(encoding="utf-8")
    assert '@router.get("/admin/payment-reconciliation")' in source
    assert "await auth_admin(request)" in source
    assert ".insert_one(" not in source
    assert ".update_one(" not in source
    assert ".delete_one(" not in source
    assert ".replace_one(" not in source
    assert '"read_only": True' in source


def test_queue_applies_db_limits_and_returns_severity_summary():
    source = Path("rental/stripe_pkg/reconciliation_queue_router.py").read_text(encoding="utf-8")
    assert source.count(".limit(safe_limit)") == 3
    assert '"by_severity": by_severity' in source
    assert '"age_seconds": age_seconds' in source
    assert '"severity": _SEVERITY_LABELS[score]' in source


def test_stripe_aggregator_includes_queue_without_changing_webhook_override():
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert "_reconciliation_queue_router" in source
    assert "router.include_router(_reconciliation_queue_router)" in source
    assert 'if getattr(route, "path", "") == "/stripe/connect-webhook"' in source
    assert source.count("router.include_router(_hardened_webhook_router)") == 1


def test_helcim_reconciliation_failure_is_persisted_for_queue():
    source = Path("rental/autopay_cron.py").read_text(encoding="utf-8")
    block = source.split("if result.modified_count != 1:", 1)[1].split(
        "logger.info(\"Autopay Helcim OK", 1
    )[0]
    assert '"last_attempt_status": "reconciliation_required"' in block
    assert '"last_result": "invoice_reconciliation_required"' in block
    assert '"reason": "invoice_reconciliation_required"' in block
