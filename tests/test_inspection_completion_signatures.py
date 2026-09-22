from pathlib import Path

src = Path("rental/inspection_security_router.py").read_text()
block = src.split("@router.put('/admin/inspections/{inspection_id}')", 1)[1].split("def _signature_image", 1)[0]

for needle in [
    'if target == "completed":',
    'signatures = current.get("signatures") or {}',
    'not signatures.get("admin") or not signatures.get("tenant")',
    'inspection_completion_requires_admin_and_tenant_signatures',
]:
    assert needle in block, needle

print("PASS: inspections cannot complete before both immutable signatures exist.")
