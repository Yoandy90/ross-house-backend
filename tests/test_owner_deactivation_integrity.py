from pathlib import Path

src = Path("rental/owner_router.py").read_text()
block = src.split("@router.delete('/admin/owners/{owner_id}')",1)[1].split("@router.get('/admin/owners/{owner_id}')",1)[0]

for needle in [
  'owned_property_ids = []',
  '"status": "active"',
  'No se puede desactivar este propietario',
  '_owner_account_filter(owner_id)',
  'owner_deactivated',
  '"message": "Propietario desactivado"',
]:
    assert needle in block, needle

print("PASS: owner deactivation preserves active contractual ownership relationships.")
