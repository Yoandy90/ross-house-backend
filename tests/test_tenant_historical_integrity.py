from pathlib import Path

src = Path("rental/tenant_router.py").read_text()

tenant_block = src.split("@router.delete('/admin/tenants/{tenant_id}')", 1)[1].split("async def _send_welcome_email", 1)[0]
for needle in [
    'find_one({"tenant_id": tenant_id})',
    'historial contractual',
    'status_code=409',
    'tenant_without_contract_history_deleted',
]:
    assert needle in tenant_block, needle
assert "force=true" not in tenant_block

app_block = src.split("@router.delete('/admin/app-users/{user_id}')", 1)[1].split("@router.post('/admin/tenants')", 1)[0]
assert 'historical_contract = await db.rental_contracts.find_one({"tenant_id": tid})' in app_block
assert 'desactiva el acceso de la app por separado' in app_block
print("PASS: tenant/app-user deletes cannot erase contractual history.")
