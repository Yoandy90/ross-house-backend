"""ETAPA 4C — FASE 0: Auditoría READ-ONLY de producción (121 Oak y 812 NE 2nd).
CERO writes. Solo lecturas contra Atlas prod.
"""
import asyncio, os, json
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

OAK_ID = "6a2761e4a8489d364620984e"
P812_ID = "6a277696a8489d364620984f"

# Montos documentados de 812 a buscar como posibles duplicados
DUP_AMOUNTS_812 = [350.0, 15.0, 29.0, 649.50, 785.0, 500.0, 150.0, 135.0, 149.24, 1000.0, 100.0, 1043.50]


def show_inv(inv):
    fields = ["property_id", "schema_version", "address", "property_address", "name",
              "purchase_price", "closing_costs", "current_estimated_value", "arv",
              "loan_balance", "status", "monthly_rent", "estimated_value",
              "renovation_cost", "total_invested"]
    out = {f: inv.get(f, "<ABSENT>") for f in fields if f in inv or f in (
        "property_id", "purchase_price", "closing_costs", "current_estimated_value",
        "arv", "loan_balance", "schema_version")}
    legacy = inv.get("expenses") or []
    out["legacy_expenses_count"] = len(legacy)
    out["legacy_expenses"] = [{k: e.get(k) for k in ("description", "amount", "date", "category")} for e in legacy]
    return out


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]

    for label, iid in (("121 OAK", OAK_ID), ("812 NE 2ND", P812_ID)):
        inv = await db.investments.find_one({"_id": ObjectId(iid)})
        print(f"\n{'='*60}\nINVESTMENT {label} ({iid})\n{'='*60}")
        print(json.dumps(show_inv(inv), indent=1, default=str))
        pid = inv.get("property_id")
        if pid:
            try:
                prop = await db.properties.find_one({"_id": ObjectId(pid)})
            except Exception:
                prop = await db.properties.find_one({"_id": pid})
            if prop:
                print("LINKED PROPERTY:", json.dumps({k: prop.get(k) for k in (
                    "_id", "address", "property_number", "city", "state", "status",
                    "monthly_rent", "estimated_value", "purchase_price")}, indent=1, default=str))
            # gastos canónicos de esa propiedad
            exps = [e async for e in db.property_expenses.find({"property_id": str(pid)})]
            print(f"\nPROPERTY_EXPENSES for {label}: {len(exps)}")
            for e in exps:
                print(" ", json.dumps({k: e.get(k) for k in (
                    "expense_number", "description", "amount", "date", "category",
                    "expense_scope", "accounting_treatment", "migrated_from")}, default=str))
            # income history
            pays = [p async for p in db.rental_payments.find(
                {"property_id": str(pid)}, {"amount": 1, "status": 1, "payment_date": 1, "receipt_number": 1})]
            print(f"\nRENTAL_PAYMENTS for {label}: {len(pays)}")
            for p in pays:
                print(" ", json.dumps({k: p.get(k) for k in ("receipt_number", "amount", "status", "payment_date")}, default=str))

    # ── Búsqueda global de posibles duplicados por monto (todas las colecciones de gastos) ──
    print(f"\n{'='*60}\nBÚSQUEDA GLOBAL DE DUPLICADOS POR MONTO (property_expenses)\n{'='*60}")
    for amt in DUP_AMOUNTS_812:
        hits = [e async for e in db.property_expenses.find({"amount": amt})]
        for e in hits:
            print(f"  ${amt}: {e.get('expense_number')} | {e.get('description')} | prop={e.get('property_id')} | scope={e.get('expense_scope')} | treat={e.get('accounting_treatment')} | date={e.get('date')}")
        if not hits:
            print(f"  ${amt}: (no encontrado)")

    # gastos BUSINESS (oficina 305 Bruce)
    print(f"\n{'='*60}\nBUSINESS EXPENSES (resumen)\n{'='*60}")
    biz = [e async for e in db.property_expenses.find({"expense_scope": "BUSINESS"})]
    print(f"count={len(biz)}, total=${sum(float(e.get('amount') or 0) for e in biz):.2f}")
    for e in biz[:12]:
        print(" ", e.get("expense_number"), "|", e.get("description"), "|", e.get("amount"))

    # gastos sin scope / sin treatment
    noscope = await db.property_expenses.count_documents({"expense_scope": {"$exists": False}})
    notreat = await db.property_expenses.count_documents({"accounting_treatment": {"$exists": False}})
    total = await db.property_expenses.count_documents({})
    print(f"\nTOTAL property_expenses={total}, sin expense_scope={noscope}, sin accounting_treatment={notreat}")

    # ¿Existen otras colecciones con gastos? (expenses genérica)
    names = await db.list_collection_names()
    print("\nColecciones con 'expens' o 'cost':", [n for n in names if "expens" in n or "cost" in n])

asyncio.run(main())
