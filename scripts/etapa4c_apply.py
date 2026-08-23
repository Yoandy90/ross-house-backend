"""ETAPA 4C — APPLY (GATED). NO ejecutar sin aprobación explícita del usuario.
Uso:  python scripts/etapa4c_apply.py --apply
Sin --apply solo repite el dry-run.

Pasos: 1) backup lógico JSON de docs afectados  2) re-verifica duplicados
       3) inserta ACQUISITION_COST canónicos  4) set loan_balance=0 en investment 812
Rollback: restaurar desde scripts/backups/etapa4c_before_*.json
  (delete_many de los _id insertados + $set/$unset de loan_balance previo).
"""
import asyncio, os, sys, json
from datetime import datetime
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

sys.path.insert(0, os.path.dirname(__file__))
from etapa4c_plan import (CLOSING_CHARGES_812, INSPECTION_CHARGES_812, COMMON_FIELDS,
                          INVESTMENT_UPDATES_812, INVESTMENT_UPDATES_OAK,
                          INV_812, PROP_812, INV_OAK)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

APPLY = "--apply" in sys.argv


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]
    inv = await db.investments.find_one({"_id": ObjectId(INV_812)})
    oak = await db.investments.find_one({"_id": ObjectId(INV_OAK)})
    prop = await db.properties.find_one({"_id": ObjectId(PROP_812)})
    assert inv and oak and prop and inv.get("property_id") == PROP_812
    assert inv.get("purchase_price") == 108000.0, "purchase_price 812 inesperado — STOP"
    assert oak.get("purchase_price") == 70000.0, "purchase_price Oak inesperado — STOP"
    # EXP-2026-0020 debe permanecer intacto
    e20 = await db.property_expenses.find_one({"expense_number": "EXP-2026-0020"})
    assert e20 and e20["amount"] == 149.24 and e20["accounting_treatment"] == "CAPITAL_IMPROVEMENT"

    charges = CLOSING_CHARGES_812 + INSPECTION_CHARGES_812
    # Re-verificación de duplicados (idempotencia): salta lo que ya exista
    to_create = []
    for c in charges:
        if await db.property_expenses.find_one({"property_id": PROP_812, "amount": c["amount"],
                                                "accounting_treatment": "ACQUISITION_COST"}):
            print(f"SKIP duplicado: ${c['amount']} {c['description'][:50]}")
            continue
        to_create.append(c)

    if not APPLY:
        print(f"DRY-RUN: {len(to_create)} creates pendientes, "
              f"UPDATE 812 loan_balance {inv.get('loan_balance', '<ABSENT>')} -> 0.0, "
              f"UPDATE Oak loan_balance {oak.get('loan_balance', '<ABSENT>')} -> 0.0. Use --apply.")
        return

    # 1) Backup lógico (BEFORE exacto de todo lo afectado)
    os.makedirs(os.path.join(os.path.dirname(__file__), "backups"), exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(os.path.dirname(__file__), "backups", f"etapa4c_before_{stamp}.json")
    with open(bpath, "w") as f:
        json.dump({"investment_812": json.loads(json.dumps(inv, default=str)),
                   "investment_121_oak": json.loads(json.dumps(oak, default=str)),
                   "property_812": json.loads(json.dumps(prop, default=str)),
                   "loan_balance_before": {"812": inv.get("loan_balance", "<ABSENT>"),
                                            "121_oak": oak.get("loan_balance", "<ABSENT>")},
                   "rollback": "delete_many property_expenses con _id en inserted_ids; "
                               "$unset loan_balance en ambos investments (estado previo <ABSENT>)",
                   "planned_creates": to_create}, f, indent=1)
    # verificar backup legible
    with open(bpath) as f:
        assert json.load(f)["investment_812"]["_id"], "backup ilegible — STOP"
    print(f"Backup verificado legible: {bpath}")

    # 2) Inserts canónicos (uno a uno, expense_number secuencial)
    now = datetime.utcnow()
    inserted = []
    for c in to_create:
        count = await db.property_expenses.count_documents({})
        doc = {
            "expense_number": f"EXP-{now.year}-{str(count + 1).zfill(4)}",
            "property_address": prop.get("address", ""),
            "property_number": prop.get("property_number", ""),
            "description": c["description"], "amount": c["amount"],
            "source_document": c["source_document"],
            "irs_category": "", "receipt_number": "", "receipt_id": "",
            "notes": "ETAPA 4C — Buyer's Statement Chicago Title 2026-05-08",
            "created_at": now, "updated_at": now, "created_by": "etapa4c_apply",
            **COMMON_FIELDS,
        }
        r = await db.property_expenses.insert_one(doc)
        inserted.append(str(r.inserted_id))
        print(f"CREATED {doc['expense_number']} ${c['amount']} {c['description'][:50]}")

    # 3) loan_balance = 0 EXPLÍCITO (decisión humana: ambas CASH sin deuda)
    await db.investments.update_one({"_id": ObjectId(INV_812)},
                                    {"$set": {**INVESTMENT_UPDATES_812, "updated_at": now}})
    print(f"UPDATED investment 812 {INV_812}: loan_balance=0.0")
    await db.investments.update_one({"_id": ObjectId(INV_OAK)},
                                    {"$set": {**INVESTMENT_UPDATES_OAK, "updated_at": now}})
    print(f"UPDATED investment 121 Oak {INV_OAK}: loan_balance=0.0")
    print(f"Inserted ids (para rollback): {inserted}")
    print(f"TOTAL WRITES: {len(inserted)} inserts + 2 updates")

asyncio.run(main())
