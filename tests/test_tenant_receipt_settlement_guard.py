from pathlib import Path

src = Path("rental/tenant_receipt_security_router.py").read_text()

for needle in [
    'status in {"completed", "paid"}',
    'payment.get("paid") is True',
    'float(payment.get("total_paid") or 0) > 0',
    'payment.get("record_type") == "checkout_attempt"',
    'bool(payment.get("invoice_id"))',
    'receipt_payment_not_settled',
    'recorded_paid_amount(payment) <= 0',
    'receipt_paid_amount_invalid',
    'safe_receipt_num',
]:
    assert needle in src, needle

print("PASS: tenant receipt endpoint only emits settled canonical rent receipts.")
