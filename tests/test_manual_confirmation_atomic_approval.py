from pathlib import Path

src = Path("rental/manual_confirmations_router.py").read_text()
block = src.split('@router.post("/admin/manual-payment/confirmations/{cid}/approve")', 1)[1].split('@router.post("/admin/manual-payment/confirmations/{cid}/reject")', 1)[0]

for needle in [
    '"status": "approving"',
    '"approval_claim": claim_id',
    '"manual_confirmation_id": cid',
    '"confirmation_source": "tenant_manual_confirmation"',
    '"paid": True',
    '"total_due": charge["total_due"]',
    '"payment_date": now',
    'No vuelvas a aprobarla',
    'linked = await db.rental_payments.find_one',
]:
    assert needle in block, needle

assert '{"_id": s["_id"], "status": {"$ne": "approved"}}' not in block.split("Idempotencia dura", 1)[-1]
print("PASS: manual confirmation approval is claimed and recoverable from a settled ledger.")
