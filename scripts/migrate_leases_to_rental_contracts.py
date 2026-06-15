"""Migrate legacy `leases` collection → `rental_contracts`.

Strategy:
1. For each lease doc, build an equivalent rental_contracts doc with:
   - `contract_number` auto-assigned (CONT-YYYY-####)
   - Signature wrapped in {type:'canvas', image_data:...} format
   - Default fee fields (late_fee_amount=50, grace=5, payment_due_day=1)
   - Audit trail: `migrated_from_lease_id`, `migrated_at`, `legacy_collection`
2. Resolve tenant_id from tenant_email if empty (look up `tenants` or `app_users`)
3. Move original lease to `leases_archive` collection (for rollback if needed)
4. Delete from `leases`

Run with --dry-run to preview, --commit to apply.
"""
import asyncio
import sys
import os
import argparse
from datetime import datetime, timezone

sys.path.insert(0, '/app/ross-house-backend')
from dotenv import load_dotenv
load_dotenv('/app/ross-house-backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId


def normalize_date(v):
    """Coerce a date-like value into a datetime (UTC)."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            if "T" in v:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            return datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def wrap_signature(sig_value):
    """Convert legacy bare base64 signature into rental_contracts canvas format."""
    if not sig_value:
        return None
    if isinstance(sig_value, dict):
        return sig_value
    if isinstance(sig_value, str):
        return {"type": "canvas", "image_data": sig_value}
    return None


async def resolve_tenant_id(db, lease):
    """If lease tenant_id is empty, try to resolve via email or name."""
    if lease.get("tenant_id"):
        return str(lease["tenant_id"])
    email = (lease.get("tenant_email") or "").strip().lower()
    name = (lease.get("tenant_name") or "").strip()
    if email:
        t = await db.tenants.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
        if t:
            return str(t["_id"])
        # try app_users
        u = await db.app_users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
        if u:
            return str(u["_id"])
    if name:
        t = await db.tenants.find_one({"name": {"$regex": f"^{name}$", "$options": "i"}})
        if t:
            return str(t["_id"])
    return ""


async def next_contract_number(db):
    """Generate next CONT-YYYY-#### number based on current count for the year."""
    now = datetime.now(timezone.utc)
    count = await db.rental_contracts.count_documents({
        "contract_number": {"$regex": f"^CONT-{now.year}-"}
    })
    return f"CONT-{now.year}-{str(count + 1).zfill(3)}"


async def build_rc_doc(db, lease):
    """Map a `leases` document into a `rental_contracts` document."""
    now = datetime.now(timezone.utc)
    tenant_id = await resolve_tenant_id(db, lease)
    contract_number = await next_contract_number(db)

    rc = {
        "contract_number": contract_number,
        "property_id": str(lease.get("property_id", "")),
        "property_address": lease.get("property_address", ""),
        "tenant_id": tenant_id,
        "tenant_name": lease.get("tenant_name", ""),
        "tenant_email": lease.get("tenant_email", ""),
        "tenant_phone": lease.get("tenant_phone", ""),

        "lease_type": lease.get("lease_type", "residential"),
        "start_date": (normalize_date(lease.get("start_date")) or now).strftime("%Y-%m-%d"),
        "end_date": (normalize_date(lease.get("end_date")) or now).strftime("%Y-%m-%d"),
        "rent_amount": float(lease.get("rent_amount") or 0),
        "monthly_rent": float(lease.get("rent_amount") or 0),  # legacy alias
        "deposit_amount": float(lease.get("deposit_amount") or 0),
        "terms": lease.get("terms", ""),
        "clauses": lease.get("clauses", []),
        "special_conditions": lease.get("special_conditions", ""),

        # Default rental_contracts fields not present in leases
        "late_fee_amount": float(lease.get("late_fee_amount", 50)),
        "late_fee_grace_days": int(lease.get("late_fee_grace_days", 5)),
        "payment_due_day": int(lease.get("payment_due_day", 1)),
        "payment_method_type": lease.get("payment_method_type", "cash"),

        # Signatures
        "tenant_signature": wrap_signature(lease.get("tenant_signature")),
        "tenant_signed_at": normalize_date(lease.get("tenant_signed_at")),
        "tenant_signer_name": lease.get("tenant_signer_name", lease.get("tenant_name", "")),

        "admin_signature": wrap_signature(lease.get("admin_signature") or lease.get("landlord_signature")),
        "admin_signed_at": normalize_date(lease.get("admin_signed_at") or lease.get("landlord_signed_at")),
        "signature": wrap_signature(lease.get("admin_signature") or lease.get("landlord_signature")),
        "signed_at": normalize_date(lease.get("admin_signed_at") or lease.get("landlord_signed_at")),
        "signature_status": "signed" if (lease.get("admin_signature") or lease.get("tenant_signature")) else "pending",

        "status": lease.get("status", "active"),

        # Audit
        "created_at": normalize_date(lease.get("created_at")) or now,
        "updated_at": now,
        "created_by": "migration_from_leases",
        "migrated_from_lease_id": str(lease["_id"]),
        "migrated_at": now,
        "legacy_collection": "leases",
    }
    return rc


async def main(dry_run: bool):
    client = AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ.get('DB_NAME', 'taxportal')]

    print(f"\n{'═'*70}")
    print(f"  MIGRATION: leases → rental_contracts  ({'DRY RUN' if dry_run else 'COMMIT MODE'})")
    print(f"{'═'*70}\n")

    total = await db.leases.count_documents({})
    if total == 0:
        print("✅ Nothing to migrate. `leases` is empty.")
        return

    migrated = 0
    skipped = 0
    errors = 0

    async for lease in db.leases.find({}):
        lease_id = str(lease["_id"])
        try:
            # Skip if already migrated
            existing = await db.rental_contracts.find_one({"migrated_from_lease_id": lease_id})
            if existing:
                print(f"⏭️  SKIP {lease_id} — already migrated to rental_contract {existing['_id']}")
                skipped += 1
                continue

            # Also skip if a contract already exists for the same tenant+property
            tid = str(lease.get("tenant_id", ""))
            pid = str(lease.get("property_id", ""))
            if tid and pid:
                dup = await db.rental_contracts.find_one({"tenant_id": tid, "property_id": pid})
                if dup:
                    print(f"⏭️  SKIP {lease_id} — tenant+property already in rental_contracts ({dup['_id']})")
                    skipped += 1
                    continue

            rc_doc = await build_rc_doc(db, lease)

            print(f"\n📝 Lease {lease_id} → new rental_contract")
            print(f"   contract_number : {rc_doc['contract_number']}")
            print(f"   tenant          : {rc_doc['tenant_name']} <{rc_doc['tenant_email']}>")
            print(f"   tenant_id       : {rc_doc['tenant_id'] or '⚠️ EMPTY'}")
            print(f"   property        : {rc_doc['property_address']}")
            print(f"   period          : {rc_doc['start_date']} → {rc_doc['end_date']}")
            print(f"   rent / deposit  : ${rc_doc['rent_amount']:.2f} / ${rc_doc['deposit_amount']:.2f}")
            print(f"   status          : {rc_doc['status']}")
            print(f"   signed          : tenant={bool(rc_doc['tenant_signature'])} admin={bool(rc_doc['admin_signature'])}")

            if not dry_run:
                # Insert new rental_contract
                ins = await db.rental_contracts.insert_one(rc_doc)
                # Archive the lease
                archive_doc = dict(lease)
                archive_doc["archived_at"] = datetime.now(timezone.utc)
                archive_doc["migrated_to_rental_contract_id"] = str(ins.inserted_id)
                await db.leases_archive.insert_one(archive_doc)
                # Delete from leases
                await db.leases.delete_one({"_id": lease["_id"]})
                print(f"   ✅ COMMITTED → rental_contract {ins.inserted_id}")
                print(f"   📦 lease archived to leases_archive")

            migrated += 1
        except Exception as e:
            print(f"❌ ERROR on lease {lease_id}: {e}")
            errors += 1

    print(f"\n{'═'*70}")
    print(f"  RESULT: total={total} migrated={migrated} skipped={skipped} errors={errors}")
    if dry_run:
        print(f"  ℹ️ This was a DRY RUN. Re-run with --commit to apply.")
    print(f"{'═'*70}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--commit", action="store_true", help="Apply migration (default: dry-run only)")
    args = p.parse_args()
    asyncio.run(main(dry_run=not args.commit))
