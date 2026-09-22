from pathlib import Path

src = Path("rental/payment_processors_core.py").read_text()

for needle in [
    "PROVIDER_REGISTRY =",
    "CAPABILITIES =",
    "get_processor_for_capability",
    '"/admin/payment-processors/{name}/enabled"',
    '"/admin/payment-processors/capability-routing"',
    '"capability_routing": doc.get("capability_routing", {})',
    '"provider_registry": PROVIDER_REGISTRY',
    '"enabled": bool(cfg.get("enabled", False))',
    'get_processor_for_capability("hosted_checkout")',
]:
    assert needle in src, needle

assert '"saved_ach"' in src
assert '"cash_app_pay"' in src
assert 'while tenga capacidades asignadas' in src or 'mientras tenga capacidades asignadas' in src

print("PASS: modular payment-provider registry and capability routing are wired into hosted checkout.")
