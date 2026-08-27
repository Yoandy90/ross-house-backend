"""Static/runtime guards for native Stripe rent payment integrity."""
import inspect
from pathlib import Path

from rental.stripe_pkg.tenant_payments_router import _intent_belongs_to_tenant
from rental.stripe_pkg.rent_reconciliation import stripe_payment_identity_query
from rental.stripe_pkg import hardened_webhook_router
from rental.stripe_router import router as stripe_router


def _function_block(source: str, marker: str, next_marker: str | None = None) -> str:
    block = source.split(marker, 1)[1]
    if next_marker and next_marker in block:
        block = block.split(next_marker, 1)[0]
    return block


def test_payment_identity_query_matches_all_historical_fields():
    assert stripe_payment_identity_query("pi_123") == {
        "$or": [
            {"stripe_payment_intent_id": "pi_123"},
            {"stripe_payment_intent": "pi_123"},
            {"reference_number": "pi_123"},
        ]
    }


def test_intent_must_belong_to_authenticated_tenant():
    assert _intent_belongs_to_tenant({"tenant_id": "tenant-a"}, "tenant-a") is True
    assert _intent_belongs_to_tenant({"tenant_id": "tenant-b"}, "tenant-a") is False
    assert _intent_belongs_to_tenant({}, "tenant-a") is False
    assert _intent_belongs_to_tenant(None, "tenant-a") is False


def test_native_create_uses_canonical_invoice_not_client_financial_values():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    create = _function_block(
        source,
        "async def tenant_create_stripe_payment",
        "async def tenant_confirm_stripe_payment",
    )
    assert "resolve_current_rent_charge" in create
    assert 'charge.get("outstanding")' in create
    assert 'charge.get("invoice_id")' in create
    assert 'data.get("late_fee")' not in create
    assert 'data.get("rent_amount")' not in create
    assert 'data.get("amount")' not in create
    assert '"amount": total_cents' in create
    assert '"invoice_id"' in create
    assert '"invoice_total_due"' in create
    assert '"invoice_total_paid"' in create


def test_confirm_endpoint_is_not_a_financial_writer():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    confirm = _function_block(source, "async def tenant_confirm_stripe_payment")
    assert "PaymentIntent.retrieve" in confirm
    assert "_intent_belongs_to_tenant" in confirm
    assert "stripe_payment_identity_query" in confirm
    assert ".rental_payments.insert_one(" not in confirm
    assert ".rental_payments.update_one(" not in confirm
    assert '"tenant_id": tenant["_id"]' in confirm
    assert '"processing": True' in confirm


def test_exactly_one_public_connect_webhook_and_it_is_hardened():
    connect = [r for r in stripe_router.routes if getattr(r, "path", "") == "/stripe/connect-webhook"]
    assert len(connect) == 1
    assert connect[0].endpoint is hardened_webhook_router.stripe_connect_webhook


def test_legacy_admin_webhook_events_route_is_preserved():
    paths = [getattr(r, "path", "") for r in stripe_router.routes]
    assert "/admin/stripe/webhook-events" in paths


def test_hardened_pi_path_uses_canonical_reconciliation():
    source = inspect.getsource(hardened_webhook_router.stripe_connect_webhook)
    assert 'event_type != "payment_intent.succeeded"' in source
    assert "legacy_webhooks.stripe_connect_webhook" in source
    assert "reconcile_succeeded_rent_payment" in source
    assert "stripe_payment_identity_query" in source


def test_reconciliation_module_never_inserts_rental_payment():
    source = Path("rental/stripe_pkg/rent_reconciliation.py").read_text(encoding="utf-8")
    assert ".rental_payments.insert_one(" not in source
    assert '"status": "completed"' in source
    assert '"status": {"$in": list(CHARGEABLE_STATUSES)}' in source


def test_legacy_webhook_is_not_modified_by_adapter_contract():
    # The adapter architecture intentionally leaves the large legacy module alone;
    # this guard documents that the hardened route imports/delegates it rather than
    # copying its non-payment event behavior.
    source = Path("rental/stripe_pkg/hardened_webhook_router.py").read_text(encoding="utf-8")
    assert "from rental.stripe_pkg import webhooks_router as legacy_webhooks" in source
    assert "if event_type != \"payment_intent.succeeded\"" in source
