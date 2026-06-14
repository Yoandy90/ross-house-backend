"""
Background cron task that periodically reconciles property statuses
with their actual active contracts.

- Runs every PROPERTY_SYNC_INTERVAL_MIN minutes (default: 15)
- Respects status_manually_set flag (admin manual overrides)
- Safe to run alongside the manual /admin/properties/sync-status endpoint
"""
import asyncio
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


async def reconcile_property_statuses(db) -> dict:
    """Reconcile every property's status based on its active contract.
    Returns a small report dict.
    """
    from bson import ObjectId  # local import to avoid circular deps

    fixed = 0
    skipped_manual = 0
    unchanged = 0
    now = datetime.utcnow()

    async for prop in db.properties.find({}):
        pid_obj = prop["_id"]
        pid = str(pid_obj)

        # Respect admin's manual override
        if prop.get("status_manually_set"):
            skipped_manual += 1
            continue

        active_contract = await db.rental_contracts.find_one({
            "property_id": pid,
            "status": "active",
        })

        if active_contract:
            target_status = "rented"
            target_tenant = str(active_contract.get("tenant_id", ""))
            target_contract = str(active_contract.get("_id", ""))
        else:
            target_status = "available"
            target_tenant = None
            target_contract = None

        current_status = prop.get("status")
        current_contract = str(prop.get("current_contract_id") or "")

        if current_status == target_status and current_contract == (target_contract or ""):
            unchanged += 1
            continue

        await db.properties.update_one(
            {"_id": pid_obj},
            {"$set": {
                "status": target_status,
                "current_tenant_id": target_tenant,
                "current_contract_id": target_contract,
                "updated_at": now,
                "last_auto_sync": now,
            }}
        )
        fixed += 1
        logger.info(
            f"🔄 [property-sync] Updated {prop.get('address','?')[:30]} : "
            f"{current_status} → {target_status}"
        )

    return {
        "fixed": fixed,
        "skipped_manual": skipped_manual,
        "unchanged": unchanged,
    }


async def property_sync_loop():
    """Long-running asyncio task. Reconciles every N minutes."""
    try:
        interval_min = int(os.environ.get("PROPERTY_SYNC_INTERVAL_MIN", "15"))
    except ValueError:
        interval_min = 15

    interval_sec = max(interval_min * 60, 60)

    # Initial delay so it doesn't fight with startup
    await asyncio.sleep(30)

    logger.info(f"🕐 Property-Contract sync cron started (interval: {interval_min} min)")

    while True:
        try:
            from rental.shared import get_db
            db = get_db()
            report = await reconcile_property_statuses(db)
            if report["fixed"] > 0:
                logger.info(
                    f"✅ [property-sync] Cron run complete: "
                    f"{report['fixed']} fixed, "
                    f"{report['skipped_manual']} skipped (manual), "
                    f"{report['unchanged']} unchanged"
                )
        except asyncio.CancelledError:
            logger.info("🛑 Property-sync cron cancelled")
            raise
        except Exception as e:
            logger.error(f"❌ [property-sync] Cron iteration failed: {e}")

        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info("🛑 Property-sync cron cancelled during sleep")
            raise
