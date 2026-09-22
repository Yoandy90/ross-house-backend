from pathlib import Path

src = Path("rental/stripe_pkg/autopay_router.py").read_text()
for needle in [
    '"processor":',
    '"helcim_method_id":',
    '"helcim_method_type":',
    '"helcim_method_brand":',
    '"helcim_method_last4":',
    '"authorization_version":',
    '"authorized_at":',
]:
    assert needle in src, needle
print("PASS: admin autopay overview exposes Helcim authorization and method metadata")
