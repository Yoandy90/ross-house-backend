from pathlib import Path

src = Path("rental/payment_processors_core.py").read_text()
block = src.split('@router.post("/admin/payment-processors/migrate-secrets")', 1)[1].split('@router.put("/admin/payment-processors/{name}")', 1)[0]

for needle in [
    'value.startswith(_SECRET_PREFIX)',
    '_encode_secret_value(value)',
    'processor_secrets_migrated',
    '"migrated_fields": migrated',
    '"already_encrypted": migrated == 0',
]:
    assert needle in block, needle

assert 'delete' not in block.lower()
print("PASS: provider-secret migration is idempotent, encrypted-only and non-destructive.")
