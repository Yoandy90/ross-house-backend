from pathlib import Path

src = Path("rental/payment_processors_core.py").read_text()

for needle in [
    "def _missing_required_credentials",
    "if not cfg.get(\"enabled\", False):",
    "no está configurado en su entorno activo",
    "Configura primero las credenciales del entorno activo",
    "provider_cfg = doc[\"processors\"].get(provider, {})",
]:
    assert needle in src, needle

resolver = src.split("async def get_processor_for_capability", 1)[1].split("async def get_three_ds_settings", 1)[0]
assert "_missing_required_credentials(name, cfg)" in resolver

routing = src.split('@router.put("/admin/payment-processors/capability-routing")', 1)[1].split('@router.post("/admin/payment-processors/{name}/environment")', 1)[0]
assert "_missing_required_credentials(provider, provider_cfg)" in routing

print("PASS: enabled/routed providers must be configured in their active environment.")
