"""Ownership history backfill — GATED (--apply). Solo 121 Oak y 812 NE 2nd.
Sin --apply: dry-run BEFORE/AFTER read-only. Con --apply: backup verificado +
$set SOLO de ownership_history (jamás toca campos legacy de owner).
"""
import asyncio, os, sys, json
from datetime import datetime
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rental.ownership import oak_history, p812_history, validate_history, LEGACY_OWNER_FIELDS

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
APPLY = "--apply" in sys.argv

TARGETS = {
    "69dbabdf5347719e9849b402": ("121 OAK", oak_history),
    "69e40ae6268db576b07cafd0": ("812 NE 2ND", p812_history),
}


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]
    before = {}
    for pid, (label, builder) in TARGETS.items():
        prop = await db.properties.find_one({"_id": ObjectId(pid)})
        assert prop, f"{label} no encontrada — STOP"
        before[pid] = prop
        history = builder()
        validate_history(history)
        print(f"\n── {label} ──")
        print("  BEFORE ownership_history:", prop.get("ownership_history", "<ABSENT>"))
        print("  Campos legacy (NO se tocan):",
              {k: str(prop.get(k))[:40] for k in LEGACY_OWNER_FIELDS})
        print(f"  AFTER ownership_history: {len(history)} registro(s):")
        for e in history:
            print(f"    - {e['owner_name']} | {e['owner_type']} | {e['status']} | "
                  f"{e['verification_status']} | eff={e['effective_date']} | instr={e['instrument_number']}")

    if not APPLY:
        print("\nDRY-RUN: CERO writes. Use --apply tras aprobación.")
        return

    os.makedirs(os.path.join(os.path.dirname(__file__), "backups"), exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(os.path.dirname(__file__), "backups", f"ownership_before_{stamp}.json")
    with open(bpath, "w") as f:
        json.dump({pid: json.loads(json.dumps(doc, default=str)) for pid, doc in before.items()},
                  f, indent=1)
    with open(bpath) as f:
        assert json.load(f), "backup ilegible — STOP"
    print(f"\nBackup verificado: {bpath}")

    for pid, (label, builder) in TARGETS.items():
        legacy_snapshot = {k: before[pid].get(k) for k in LEGACY_OWNER_FIELDS}
        await db.properties.update_one(
            {"_id": ObjectId(pid)},
            {"$set": {"ownership_history": builder(),
                      "ownership_history_updated_at": datetime.utcnow()}})
        after = await db.properties.find_one({"_id": ObjectId(pid)})
        assert {k: after.get(k) for k in LEGACY_OWNER_FIELDS} == legacy_snapshot, \
            f"{label}: campos legacy cambiaron — ROLLBACK REQUERIDO"
        print(f"UPDATED {label}: ownership_history escrito, legacy intacto ✓")
    print("TOTAL WRITES: 2 documentos (solo ownership_history)")

asyncio.run(main())
