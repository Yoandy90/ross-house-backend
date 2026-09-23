from pathlib import Path

src = Path("rental/api_keys_router.py").read_text()
block = src.split('@router.get("/admin/api-keys/{key_name}/reveal")', 1)[1].split('@router.delete("/admin/api-keys/{key_name}")', 1)[0]

for needle in [
    'entry = _REGISTRY_MAP.get(key_name)',
    'if entry.get("secret"):',
    'status_code=403',
    'api_key_secret_reveal_disabled',
]:
    assert needle in block, needle

assert 'return {"success": True, "key": key_name, "value": value}' in block
print("PASS: secret API keys are write-only while non-secret config remains readable.")
