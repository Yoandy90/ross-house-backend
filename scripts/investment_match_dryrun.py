#!/usr/bin/env python3
"""READ-ONLY dry-run: match investments -> properties by normalized address.
No writes. Output: MATCHED / AMBIGUOUS / UNMATCHED.
"""
import os, re, asyncio
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from motor.motor_asyncio import AsyncIOMotorClient

ABBR = {
    'avenue': 'ave', 'av': 'ave', 'street': 'st', 'drive': 'dr', 'road': 'rd',
    'lane': 'ln', 'boulevard': 'blvd', 'court': 'ct', 'place': 'pl',
    'north': 'n', 'south': 's', 'east': 'e', 'west': 'w',
    'northeast': 'ne', 'northwest': 'nw', 'southeast': 'se', 'southwest': 'sw',
    'first': '1st', 'second': '2nd', 'third': '3rd', 'fourth': '4th',
    'segunda': '2nd', '2da': '2nd', '1ra': '1st', '3ra': '3rd',
    'nd': 'ne',  # heuristic candidate ONLY for fuzzy tier (not exact) — see below
}
def norm(addr, fuzzy=False):
    a = re.sub(r'[^a-z0-9 ]', ' ', (addr or '').lower())
    toks = []
    for tk in a.split():
        if fuzzy:
            tk = ABBR.get(tk, tk)
        else:
            tk = {k: v for k, v in ABBR.items() if k != 'nd'}.get(tk, tk)
        toks.append(tk)
    return ' '.join(toks)

async def main():
    cli = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = cli[os.environ.get('DB_NAME', 'taxportal')]
    props = [p async for p in db.properties.find({}, {'address': 1, 'city': 1, 'state': 1, 'name': 1, 'status': 1})]
    invs = [i async for i in db.investments.find({}, {'address': 1, 'city': 1, 'state': 1, 'phase': 1, 'purchase_price': 1, 'property_id': 1})]
    print(f'properties: {len(props)} | investments: {len(invs)}')
    print('--- properties ---')
    for p in props:
        print(f'  {p["_id"]} | {p.get("name","")!r} | {p.get("address","")!r} | {p.get("city","")}, {p.get("state","")} | {p.get("status","")}')
    print('--- investments ---')
    for i in invs:
        print(f'  {i["_id"]} | {i.get("address","")!r} | {i.get("city","")}, {i.get("state","")} | phase={i.get("phase")} | ${i.get("purchase_price",0):,.0f} | property_id={i.get("property_id", "<none>")}')
    print('\n=== DRY-RUN MATCHING ===')
    for i in invs:
        ia, ic = norm(i.get('address')), (i.get('city') or '').strip().lower()
        exact = [p for p in props if norm(p.get('address')) == ia and ia]
        fuzzy = [p for p in props if norm(p.get('address'), fuzzy=True) == norm(i.get('address'), fuzzy=True) and norm(i.get('address'), fuzzy=True)]
        if len(exact) == 1:
            m = exact[0]
            same_city = (m.get('city') or '').strip().lower() == ic or not ic
            print(f'MATCHED   inv {i["_id"]} ({i.get("address")!r}) -> prop {m["_id"]} ({m.get("address")!r}) city_ok={same_city}')
        elif len(fuzzy) == 1 and not exact:
            m = fuzzy[0]
            print(f'AMBIGUOUS inv {i["_id"]} ({i.get("address")!r}) ~ prop {m["_id"]} ({m.get("address")!r}) [fuzzy-only, needs manual confirm]')
        elif len(exact) > 1 or len(fuzzy) > 1:
            print(f'AMBIGUOUS inv {i["_id"]} ({i.get("address")!r}) -> multiple candidates: {[str(p["_id"]) for p in (exact or fuzzy)]}')
        else:
            print(f'UNMATCHED inv {i["_id"]} ({i.get("address")!r})')
    # expenses linkage snapshot
    n_pe = await db.property_expenses.count_documents({})
    n_pe_linked = await db.property_expenses.count_documents({'property_id': {'$nin': ['', None]}})
    print(f'\nproperty_expenses: {n_pe} total, {n_pe_linked} with property_id')
    async for inv in db.investments.find({}, {'expenses': 1, 'address': 1}):
        exp = inv.get('expenses', [])
        print(f'investment {inv["_id"]} embedded expenses: {len(exp)} (${sum(e.get("amount",0) for e in exp):,.0f})')
    cli.close()

asyncio.run(main())
