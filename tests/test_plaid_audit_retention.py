from pathlib import Path

src = Path("rental/plaid_router.py").read_text()

unlink = src.split("@router.delete('/admin/plaid/items/{item_id}')", 1)[1].split("async def run_full_sync", 1)[0]
assert 'delete_many({"item_id": item_id})' not in unlink
assert 'delete_one({"item_id": item_id})' not in unlink
for needle in [
    '"status": "unlinked"',
    '"$unset": {"access_token": "", "cursor": ""}',
    '"item_unlinked": True',
    '"plaid_item_unlinked"',
    'historial importado se conservó',
]:
    assert needle in unlink, needle

assert '"source_removed": True' in src
assert 'delete_one({"transaction_id": t["transaction_id"]})' not in src
assert '"item_unlinked": {"$ne": True}' in src
assert '"source_removed": {"$ne": True}' in src

print("PASS: Plaid disconnect/removal preserves reconciliation evidence and excludes archived rows from auto-match.")
