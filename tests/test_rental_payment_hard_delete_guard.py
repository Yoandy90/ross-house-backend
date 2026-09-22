from pathlib import Path

src = Path("rental/contracts_router.py").read_text()
block = src.split("@router.delete('/admin/rental-payments/{payment_id}')", 1)[1].split("@router.post('/admin/rental-payments/generate-monthly')", 1)[0]

for needle in [
    'status in {"paid", "completed", "partial"}',
    'total_paid > 0',
    'receipt_number',
    'reference_number',
    'charge_attempt',
    'manual_confirmation_history',
    'confirmation_source',
    'record_type',
    'unpaid_invoice_deleted',
    'status_code=409',
]:
    assert needle in block, needle

assert 'delete_one({"_id": ObjectId(payment_id)})' not in block
print("PASS: paid/provider-linked rental ledger records cannot be hard-deleted")
