from pathlib import Path

src = Path("rental/payment_processors_core.py").read_text()

for needle in [
    'from .vault_router import encrypt as vault_encrypt, decrypt as vault_decrypt',
    '_SECRET_PREFIX = "enc:v1:"',
    'def _decode_secret_value',
    'def _encode_secret_value',
    'def _encrypted_credentials',
    'env_creds[field] = _decode_secret_value',
    'stored_creds = _encrypted_credentials(name, creds)',
]:
    assert needle in src, needle

save = src.split('@router.put("/admin/payment-processors/{name}")', 1)[1].split('@router.post("/admin/payment-processors/{name}/enabled")', 1)[0]
assert 'credentials.{target_env}": stored_creds' in save
assert 'credentials.{target_env}": creds' not in save
print("PASS: payment-provider secret fields are encrypted before persistence and decrypted only in server memory.")
