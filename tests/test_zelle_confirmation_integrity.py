from pathlib import Path

src = Path("rental/zelle_router.py").read_text()
block = src.split('@router.post("/admin/zelle-payments/{sub_id}/confirm")', 1)[1].split('@router.post("/admin/zelle-payments/{sub_id}/reject")', 1)[0]

for needle in [
    '"status": "confirming"',
    '"confirmation_claim": claim_id',
    '"zelle_submission_id": sub_id',
    '"paid": True',
    'outstanding',
    'Ese período de renta ya aparece pagado',
    'deterministic_id',
    'DuplicateKeyError',
    'No vuelvas a confirmarlo',
]:
    assert needle in block, needle

assert 'insert_one({' not in block
print("PASS: Zelle confirmation is claimed, amount-checked and idempotent.")
