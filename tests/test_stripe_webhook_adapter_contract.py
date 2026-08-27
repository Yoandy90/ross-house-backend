from pathlib import Path


def test_legacy_webhook_module_is_not_reimplemented_in_place():
    source = Path("rental/stripe_router.py").read_text(encoding="utf-8")
    assert "_webhooks_without_connect" in source
    assert 'getattr(route, "path", "") == "/stripe/connect-webhook"' in source
    assert "_hardened_webhook_router" in source


def test_hardened_adapter_delegates_non_pi_events_only():
    source = Path("rental/stripe_pkg/hardened_webhook_router.py").read_text(encoding="utf-8")
    assert 'if event_type != "payment_intent.succeeded"' in source
    assert "return await legacy_webhooks.stripe_connect_webhook(request)" in source
    assert "reconcile_succeeded_rent_payment" in source
    assert "Webhook processing unavailable" in source
