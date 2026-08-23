"""ETAPA 4C — DRY-RUN (READ-ONLY). Muestra plan CREATE/UPDATE/KEEP/REVIEW/DO_NOT_IMPORT
con verificación de duplicados en vivo. CERO writes.
"""
import asyncio, os, sys
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

sys.path.insert(0, os.path.dirname(__file__))
from etapa4c_plan import (CLOSING_CHARGES_812, INSPECTION_CHARGES_812, COMMON_FIELDS,
                          INVESTMENT_UPDATES_812, INVESTMENT_UPDATES_OAK, KEEP_812,
                          REVIEW, DO_NOT_IMPORT, INV_812, PROP_812, INV_OAK)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]
    inv = await db.investments.find_one({"_id": ObjectId(INV_812)})
    prop = await db.properties.find_one({"_id": ObjectId(PROP_812)})
    assert inv and prop, "812 investment/property no encontrados — STOP"
    assert inv.get("property_id") == PROP_812, "property_id no coincide — STOP"
    assert inv.get("purchase_price") == 108000.0, f"purchase_price inesperado: {inv.get('purchase_price')} — STOP"

    print("="*70)
    print("ETAPA 4C DRY-RUN — 812 NE 2nd (NINGÚN WRITE EJECUTADO)")
    print("="*70)
    print(f"\nInvestment: {INV_812} | Property: {PROP_812} ({prop.get('address')!r})")

    print("\n── CREATE (property_expenses, scope=PROPERTY, treatment=ACQUISITION_COST) ──")
    all_new = [("closing", c) for c in CLOSING_CHARGES_812] + [("inspection", c) for c in INSPECTION_CHARGES_812]
    duplicates = 0
    for group, c in all_new:
        dup = await db.property_expenses.find_one({"property_id": PROP_812, "amount": c["amount"]})
        flag = f"⚠️ DUPLICADO EXISTENTE: {dup.get('expense_number')}" if dup else "OK (no existe)"
        if dup:
            duplicates += 1
        print(f"  CREATE [{group}] ${c['amount']:>8.2f}  {c['description'][:60]}  -> {flag}")
    print(f"  Campos comunes: {COMMON_FIELDS}")
    print(f"  Total closing: ${sum(c['amount'] for c in CLOSING_CHARGES_812):.2f} (esperado 1043.50)")
    print(f"  Total inspection: ${sum(c['amount'] for c in INSPECTION_CHARGES_812):.2f} (esperado 785.00)")

    print("\n── UPDATE (investments) ──")
    oak = await db.investments.find_one({"_id": ObjectId(INV_OAK)})
    for k, v in INVESTMENT_UPDATES_812.items():
        print(f"  UPDATE 812 {k}: {inv.get(k, '<ABSENT>')} -> {v}  (0 EXPLÍCITO, compra CASH confirmada por el dueño)")
    for k, v in INVESTMENT_UPDATES_OAK.items():
        print(f"  UPDATE 121 Oak {k}: {oak.get(k, '<ABSENT>')} -> {v}  (0 EXPLÍCITO, compra CASH confirmada por el dueño)")

    print("\n── KEEP ──")
    for k, v in KEEP_812.items():
        print(f"  KEEP {k} = {v}")

    print("\n── REVIEW (NO escribir) ──")
    for r in REVIEW:
        print(f"  REVIEW {r['item']}: {r['reason']}")

    print("\n── DO_NOT_IMPORT ──")
    for r in DO_NOT_IMPORT:
        print(f"  DO_NOT_IMPORT {r['item']}: {r['reason']}")

    print(f"\nRESULTADO: duplicados detectados = {duplicates}. "
          + ("STOP — revisar antes de aplicar." if duplicates else "Plan seguro para aplicar tras aprobación."))

asyncio.run(main())
