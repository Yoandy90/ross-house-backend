#!/usr/bin/env python3
"""Etapa 2 pre-apply logical backup: full BEFORE state of the exact records
that will be modified (2 investments + EXP-2026-0020). READ-ONLY."""
import os, sys, json, asyncio
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

INV_IDS = ["6a2761e4a8489d364620984e", "6a277696a8489d364620984f"]
EXPENSE_NUMBER = "EXP-2026-0020"


def scrub(doc):
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            v = str(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, list) and k.startswith('photos'):
            v = f'<{len(v)} photos omitted>'
        out[k] = v
    return out


async def main():
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    backup = {'created_at': datetime.now(timezone.utc).isoformat(), 'investments': [], 'expenses': []}
    for iid in INV_IDS:
        doc = await db.investments.find_one({'_id': ObjectId(iid)})
        backup['investments'].append(scrub(doc))
    exp = await db.property_expenses.find_one({'expense_number': EXPENSE_NUMBER})
    backup['expenses'].append(scrub(exp))
    os.makedirs(os.path.join(os.path.dirname(__file__), 'backups'), exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = os.path.join(os.path.dirname(__file__), 'backups', f'etapa2_before_{ts}.json')
    json.dump(backup, open(path, 'w'), indent=2, ensure_ascii=False, default=str)
    # verify readable
    reread = json.load(open(path))
    assert len(reread['investments']) == 2 and len(reread['expenses']) == 1
    print(f'BACKUP OK (readable): {path}')
    for d in reread['investments']:
        print(f"  BEFORE inv {d['_id']} addr={d.get('address')!r} property_id={d.get('property_id','<absent>')!r} schema_version={d.get('schema_version','<absent>')}")
    e = reread['expenses'][0]
    print(f"  BEFORE exp {e['_id']} {e['expense_number']} ${e['amount']} {e['expense_date']} cat={e['category']} property_id={e.get('property_id')!r} treatment={e.get('accounting_treatment','<absent>')}")
    cli.close()

asyncio.run(main())
