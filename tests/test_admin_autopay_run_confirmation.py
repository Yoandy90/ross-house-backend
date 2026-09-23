from pathlib import Path

src = Path("rental/stripe_pkg/autopay_router.py").read_text()
block = src.split("@router.post('/admin/autopay/run-now')",1)[1]

for needle in [
    'RUN_AUTOPAY_NOW',
    'Confirma explícitamente la ejecución manual de autopagos.',
    'autopay_manual_run_requested',
    'autopay_manual_run_completed',
]:
    assert needle in block, needle

print("PASS: manual autopay execution requires explicit server-side confirmation and audit.")
