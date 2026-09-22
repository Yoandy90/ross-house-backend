from pathlib import Path

src = Path("rental/contracts_router.py").read_text()
block = src.split("@router.get('/admin/rental-config')", 1)[1].split("# Campos del perfil", 1)[0]

assert 'config.pop("stripe_secret_key", None)' in block
assert 'stripe_secret_key_masked' in block
update = block.split("@router.put('/admin/rental-config')", 1)[1]
assert "'default_deposit'" in update
assert "'notifications'" in update
assert "'stripe_secret_key', 'stripe_publishable_key', 'stripe_enabled'" not in update
assert 'Processor credentials are managed only through /admin/payment-processors.' in update
print("PASS: general Settings does not expose or mutate payment processor secrets.")
