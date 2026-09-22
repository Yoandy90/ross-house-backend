from pathlib import Path

src = Path("rental/tenant_invoices_router.py").read_text()
block = src.split('@router.get("/tenant/invoices/{invoice_id}/pdf")', 1)[1]

for needle in [
    "secure_tenant_payment_receipt",
    'if exc.status_code != 404:',
    'Recibo_Renta_',
    'result["type"] = "rent"',
]:
    assert needle in block, needle

assert "generate_rental_receipt_pdf" not in block.split("# Utility bill case", 1)[0]
print("PASS: mobile invoice PDFs delegate to canonical tenant receipt authorization.")
