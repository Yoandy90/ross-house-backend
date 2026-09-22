from pathlib import Path

src = Path("rental/reports_router.py").read_text()
assert '"payment_date": {"$gte": start_window, "$lt": end_window}' not in src
for needle in [
    '"status": {"$in": list(PAID_STATUSES)}',
    'payment_dt = _safe_dt(p.get("payment_date") or p.get("paid_at") or p.get("completed_at"))',
    'not (start_window <= payment_dt < end_window)',
    'idx = month_idx(payment_dt)',
]:
    assert needle in src, needle
print("PASS: T12 normalizes BSON and legacy ISO settlement dates.")
