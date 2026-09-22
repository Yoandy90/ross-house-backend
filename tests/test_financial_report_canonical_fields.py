from pathlib import Path

src = Path("rental/reports_router.py").read_text()
for needle in [
    'c.get("rent_amount", c.get("monthly_rent", 0))',
    'c.get("deposit_amount", c.get("security_deposit", 0))',
    'total_due = float(pay.get("total_due")',
    'outstanding += max(0.0, total_due - total_paid)',
    'recorded = float(p.get("total_paid") or (base + late))',
    'recorded_rent = max(0.0, recorded - recorded_late)',
]:
    assert needle in src, needle
print("PASS: financial reports use canonical contract and settled-payment fields.")
