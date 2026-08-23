#!/usr/bin/env python3
"""Investment → Property backfill (SAFE). DRY-RUN IS THE DEFAULT.

Usage:
  python3 scripts/investment_backfill.py                # dry-run (read-only)
  python3 scripts/investment_backfill.py --apply        # real write (requires typing YES)
  python3 scripts/investment_backfill.py --rollback backups/investment_backfill_<ts>.json

Guarantees:
  - Dry-run by default; --apply requires interactive confirmation "YES".
  - Idempotent: records already linked are skipped (LINKED_EXISTING).
  - Only MATCHED_EXACT and MANUALLY_CONFIRMED are ever written.
  - AMBIGUOUS / UNMATCHED are NEVER written and never auto-create properties.
  - Before writing, a logical backup (JSON) of the affected documents' prior
    state is saved to scripts/backups/ — used by --rollback.
  - Rollback restores property_id / schema_version to their prior values.
  - No deletes. No legacy field removal. No index drops.
"""
import os, sys, json, asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from rental.normalization import plan_investment_backfill, SCHEMA_VERSION_NORMALIZED

BACKUP_DIR = os.path.join(os.path.dirname(__file__), 'backups')


async def run(apply: bool):
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    properties = [p async for p in db.properties.find({}, {'address': 1, 'city': 1})]
    investments = [i async for i in db.investments.find({}, {'address': 1, 'property_id': 1, 'schema_version': 1})]
    plan = plan_investment_backfill(investments, properties)

    print(f'MODE: {"APPLY" if apply else "DRY-RUN (no writes)"}')
    counts = {}
    for row in plan:
        counts[row['status']] = counts.get(row['status'], 0) + 1
        print(f"  {row['status']:<18} inv {row['investment_id']} ({row['legacy_address']!r})"
              f" -> property_id={row['proposed_property_id']}")
    print('SUMMARY:', counts)

    writable = [r for r in plan if r['will_write']]
    if not apply:
        print(f'\nDRY-RUN complete. {len(writable)} record(s) WOULD be written. No changes made.')
        cli.close(); return

    if not writable:
        print('Nothing to write.'); cli.close(); return
    confirm = input(f'About to write {len(writable)} investment link(s) to {db.name}. Type YES to continue: ')
    if confirm.strip() != 'YES':
        print('Aborted. No changes made.'); cli.close(); return

    # Logical backup of prior state (for --rollback)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = []
    for r in writable:
        prev = next(i for i in investments if str(i['_id']) == r['investment_id'])
        backup.append({'investment_id': r['investment_id'],
                       'prev_property_id': prev.get('property_id'),
                       'prev_schema_version': prev.get('schema_version'),
                       'new_property_id': r['proposed_property_id']})
    path = os.path.join(BACKUP_DIR, f'investment_backfill_{ts}.json')
    json.dump(backup, open(path, 'w'), indent=2)
    print(f'Backup written: {path}')

    for r in writable:
        await db.investments.update_one(
            {'_id': ObjectId(r['investment_id']), 'property_id': {'$in': [None, '']}},  # idempotent guard
            {'$set': {'property_id': r['proposed_property_id'],
                      'schema_version': SCHEMA_VERSION_NORMALIZED,
                      'normalized_at': datetime.utcnow().isoformat(),
                      'normalized_via': r['status']}})
        print(f"  WROTE inv {r['investment_id']} -> {r['proposed_property_id']} ({r['status']})")
    print('APPLY complete.')
    cli.close()


async def rollback(path: str):
    backup = json.load(open(path))
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    for b in backup:
        update: dict = {'$unset': {'normalized_at': '', 'normalized_via': ''}}
        sets, unsets = {}, {}
        (sets if b['prev_property_id'] is not None else unsets).update(
            {'property_id': b['prev_property_id'] if b['prev_property_id'] is not None else ''})
        (sets if b['prev_schema_version'] is not None else unsets).update(
            {'schema_version': b['prev_schema_version'] if b['prev_schema_version'] is not None else ''})
        if sets: update['$set'] = sets
        if unsets: update['$unset'].update(unsets)
        await db.investments.update_one({'_id': ObjectId(b['investment_id'])}, update)
        print(f"  RESTORED inv {b['investment_id']} property_id={b['prev_property_id']}")
    print('Rollback complete.')
    cli.close()


if __name__ == '__main__':
    if '--rollback' in sys.argv:
        asyncio.run(rollback(sys.argv[sys.argv.index('--rollback') + 1]))
    else:
        asyncio.run(run(apply='--apply' in sys.argv))
