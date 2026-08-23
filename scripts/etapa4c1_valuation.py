"""ETAPA 4C.1 — Captura controlada de valuación (owner estimates conservadores).
Sin --apply: DRY-RUN read-only. Con --apply: backup verificado + exactamente 2 updates.
NO toca: purchase_price, loan_balance, property_id, gastos, pagos, fórmulas, legacy.
"""
import asyncio, os, sys, json
from datetime import datetime, timezone
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
APPLY = "--apply" in sys.argv

INV_OAK = "6a2761e4a8489d364620984e"
INV_812 = "6a277696a8489d364620984f"
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Valores confirmados por el propietario (estimaciones CONSERVADORAS, no appraisals)
UPDATES = {
    INV_OAK: {
        "current_estimated_value": 150000.0,
        "valuation_source": "OWNER_ESTIMATE",
        "valuation_date": TODAY,
        "valuation_note": ("Owner estimates current market value at approximately "
                           "$150,000-$160,000; $150,000 recorded as conservative current estimate."),
    },
    INV_812: {
        "current_estimated_value": 120000.0,
        "arv": 160000.0,
        "valuation_source": "OWNER_ESTIMATE",
        "valuation_date": TODAY,
        "current_value_note": "Owner estimates current value during renovation at approximately $120,000.",
        "arv_note": ("Owner estimates completed value at approximately $160,000-$180,000; "
                     "$160,000 recorded as conservative ARV."),
    },
}
# Invariantes que NO deben cambiar
FROZEN = {
    INV_OAK: {"purchase_price": 70000.0, "loan_balance": 0.0},
    INV_812: {"purchase_price": 108000.0, "loan_balance": 0.0},
}


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]
    docs = {}
    exp_count = await db.property_expenses.count_documents({})
    pay_count = await db.rental_payments.count_documents({})
    print(f"BASELINE: property_expenses={exp_count}, rental_payments={pay_count}")

    for iid, label in ((INV_OAK, "121 OAK"), (INV_812, "812 NE 2ND")):
        inv = await db.investments.find_one({"_id": ObjectId(iid)})
        docs[iid] = inv
        for k, v in FROZEN[iid].items():
            assert inv.get(k) == v, f"{label} {k}={inv.get(k)} != {v} — STOP"
        assert inv.get("property_id"), f"{label} sin property_id — STOP"
        print(f"\n── {label} BEFORE ──")
        for k in ("purchase_price", "loan_balance", "current_estimated_value", "arv", "property_id"):
            print(f"  {k}: {inv.get(k, '<ABSENT=null>')}")
        print("  Cambios propuestos (SOLO estos):")
        for k, v in UPDATES[iid].items():
            print(f"    {k}: {inv.get(k, '<ABSENT=null>')} -> {v}")

    pending = sum(1 for iid in UPDATES
                  if any(docs[iid].get(k) != v for k, v in UPDATES[iid].items()))
    if not APPLY:
        print(f"\nDRY-RUN: {pending} update(s) pendiente(s). CERO writes ejecutados. Use --apply.")
        return

    # Backup lógico verificado
    os.makedirs(os.path.join(os.path.dirname(__file__), "backups"), exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(os.path.dirname(__file__), "backups", f"etapa4c1_before_{stamp}.json")
    with open(bpath, "w") as f:
        json.dump({"investment_121_oak": json.loads(json.dumps(docs[INV_OAK], default=str)),
                   "investment_812": json.loads(json.dumps(docs[INV_812], default=str)),
                   "rollback": "restaurar current_estimated_value/arv/valuation_* al estado BEFORE "
                               "(<ABSENT> = $unset)",
                   "baseline_counts": {"property_expenses": exp_count, "rental_payments": pay_count}},
                  f, indent=1)
    with open(bpath) as f:
        assert json.load(f)["investment_812"]["_id"], "backup ilegible — STOP"
    print(f"\nBackup verificado legible: {bpath}")

    now = datetime.utcnow()
    writes = 0
    for iid in (INV_OAK, INV_812):
        if any(docs[iid].get(k) != v for k, v in UPDATES[iid].items()):
            await db.investments.update_one({"_id": ObjectId(iid)},
                                            {"$set": {**UPDATES[iid], "updated_at": now}})
            writes += 1
            print(f"UPDATED investment {iid}")
        else:
            print(f"SKIP (idempotente) {iid}")
    print(f"TOTAL WRITES: {writes} documento(s) modificado(s) (máximo autorizado: 2)")

asyncio.run(main())
