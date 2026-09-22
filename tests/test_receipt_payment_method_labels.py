from pathlib import Path

src = Path("rental_pdf_service.py").read_text()
for needle in [
    "'helcim_saved': 'Helcim'",
    "'helcim_autopay': 'Pago Automático (Helcim)'",
    "'cash_app_pay': 'Cash App Pay'",
    "'cashapp': 'Cash App'",
    "'zelle': 'Zelle'",
    "'money_order': 'Money Order'",
    "'manual_reconciliation_verified': 'Conciliación Verificada'",
    "'stripe_autopay': 'Pago Automático (Stripe)'",
]:
    assert needle in src, needle
print("PASS: receipt PDF labels match canonical admin/payment methods.")
