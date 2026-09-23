from pathlib import Path

src = Path("rental/stripe_pkg/autopay_router.py").read_text()

create = src.split("@router.post('/admin/autopay/configs')",1)[1].split("@router.put('/admin/autopay/configs/{config_id}')",1)[0]
update = src.split("@router.put('/admin/autopay/configs/{config_id}')",1)[1].split("@router.delete('/admin/autopay/configs/{config_id}')",1)[0]
delete = src.split("@router.delete('/admin/autopay/configs/{config_id}')",1)[1].split("@router.post('/admin/autopay/run-now')",1)[0]

assert "no puede activar autopago" in create
assert '"enabled": False' in create
assert '"admin_draft": True' in create

assert "no puede reactivar autopago" in update
assert "Pausa el autopago antes de cambiar día o método" in update
assert '"paused_by_admin": True' in update

assert "delete_one" not in delete
assert '"archived_at": now' in delete
assert "historial de autorización conservado" in delete

assert 'find({"archived_at": {"$exists": False}})' in src
print("PASS: admin autopay can pause/archive/draft but cannot bypass tenant authorization.")
