from pathlib import Path


def test_confirmation_remains_mobile_compatible_and_read_only():
    source = Path("rental/stripe_pkg/tenant_payments_router.py").read_text(encoding="utf-8")
    confirm = source.split("async def tenant_confirm_stripe_payment", 1)[1]
    assert '"success": True' in confirm
    assert '"processing": True' in confirm
    assert "stripe_payment_identity_query" in confirm
    assert ".rental_payments.insert_one(" not in confirm
    assert ".rental_payments.update_one(" not in confirm
