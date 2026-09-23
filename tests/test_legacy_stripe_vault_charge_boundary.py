from pathlib import Path

src = Path("rental/vault_cards_router.py").read_text()

save = src.split('@router.post("/admin/vault/card-save-link")', 1)[1].split('@router.get("/vault/card-save-link/{token}")', 1)[0]
charge = src.split('@router.post("/admin/vault/charge")', 1)[1].split('@router.get("/admin/vault/charges")', 1)[0]

for needle in [
    'get_processor_for_capability("saved_card")',
    'provider != "stripe"',
    'legacy_stripe_card_save_disabled_use_active_provider',
]:
    assert needle in save, needle

for needle in [
    'await _require_vault_session(request)',
    'get_processor_for_capability("saved_card")',
    'provider != "stripe"',
    'legacy_stripe_vault_charge_disabled_use_active_provider',
]:
    assert needle in charge, needle

print("PASS: legacy Stripe vault save/charge is vault-gated and provider-routed.")
