from pathlib import Path

vault = Path("rental/vault_router.py").read_text()
cards = Path("rental/vault_cards_router.py").read_text()

assert "async def _require_vault_session(request: Request, admin: Optional[dict] = None)" in vault
for needle in [
    'token_admin_id = str(payload.get("admin_id") or "")',
    'current_admin_id = str(admin.get("_id") or admin.get("id") or "")',
    'token_email = str(payload.get("admin_email") or "").strip().lower()',
    'vault_session_admin_mismatch',
]:
    assert needle in vault, needle

assert vault.count("_require_vault_session(request, admin)") >= 2
assert "_require_vault_session(request, admin)" in cards
print("PASS: vault elevation tokens are bound to the authenticated admin account.")
