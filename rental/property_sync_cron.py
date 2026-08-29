"""
Background reconciliation for property status projections.

The lease lifecycle is the authority for occupancy. This task may repair a
missing single-property projection when there is exactly one active contract,
but it must never choose between multiple contracts, overwrite another
contract's claim, clear a claim merely because no active contract is visible,
or mutate a property while a serialized lease/topology operation owns it.
Those states require explicit lifecycle recovery or a later sync iteration.
"""
import asyncio
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


async def reconcile_property_statuses(db) -> dict:
    """Conservatively reconcile property projections from active leases."""
    fixed = 0
    skipped_manual = 0
    skipped_mutation = 0
    unchanged = 0
    ambiguous = 0
    conflicts = 0
    now = datetime.utcnow()

    async for prop in db.properties.find({}):
        pid_obj = prop["_id"]
        pid = str(pid_obj)

        mutation_lock = prop.get("mutation_lock")
        if mutation_lock:
            expires_at = mutation_lock.get("expires_at") if isinstance(mutation_lock, dict) else None
            # Malformed/non-expiring claims fail closed instead of allowing a
            # background projection write into an in-flight serialized mutation.
            if not isinstance(expires_at, datetime) or expires_at > now:
                skipped_mutation += 1
                continue

        if prop.get("status_manually_set"):
            skipped_manual += 1
            continue

        if prop.get("is_multi_unit"):
            from rental.units_router import sync_property_from_units
            await sync_property_from_units(pid)
            continue

        active_contracts = await db.rental_contracts.find({
            "property_id": pid,
            "status": "active",
        }).limit(2).to_list(2)

        if len(active_contracts) > 1:
            ambiguous += 1
            logger.error("[property-sync] multiple active contracts for property %s; no mutation", pid)
            continue

        current_status = str(prop.get("status") or "")
        current_contract = str(prop.get("current_contract_id") or "")
        current_tenant = str(prop.get("current_tenant_id") or "")

        if not active_contracts:
            # Never infer that a non-empty projection is stale. It may be an
            # interrupted termination whose lifecycle claim is awaiting explicit
            # recovery inspection.
            if current_contract or current_tenant:
                conflicts += 1
                logger.warning("[property-sync] occupied projection without active contract for %s; no mutation", pid)
                continue
            if current_status == "available":
                unchanged += 1
                continue
            result = await db.properties.update_one(
                {"_id": pid_obj,
                 "$or": [{"current_contract_id": None}, {"current_contract_id": ""},
                         {"current_contract_id": {"$exists": False}}]},
                {"$set": {"status": "available", "updated_at": now, "last_auto_sync": now}},
            )
            if result.matched_count == 1:
                fixed += 1
            else:
                conflicts += 1
            continue

        active_contract = active_contracts[0]
        target_contract = str(active_contract.get("_id") or "")
        target_tenant = str(active_contract.get("tenant_id") or "")
        if not target_contract or not target_tenant:
            ambiguous += 1
            logger.error("[property-sync] active contract missing relationship identity for %s", pid)
            continue

        if current_contract and current_contract != target_contract:
            conflicts += 1
            logger.warning("[property-sync] property %s claimed by another contract; no mutation", pid)
            continue
        if current_tenant and current_tenant != target_tenant:
            conflicts += 1
            logger.warning("[property-sync] property %s tenant projection conflicts; no mutation", pid)
            continue

        if (current_status == "rented" and current_contract == target_contract
                and current_tenant == target_tenant):
            unchanged += 1
            continue

        # Repair only an empty or same-contract projection, using CAS so a
        # concurrent lifecycle transition wins instead of being overwritten.
        result = await db.properties.update_one(
            {"_id": pid_obj,
             "$or": [
                 {"current_contract_id": target_contract},
                 {"current_contract_id": None},
                 {"current_contract_id": ""},
                 {"current_contract_id": {"$exists": False}},
             ]},
            {"$set": {
                "status": "rented",
                "current_tenant_id": target_tenant,
                "current_contract_id": target_contract,
                "updated_at": now,
                "last_auto_sync": now,
            }},
        )
        if result.matched_count == 1:
            fixed += 1
            logger.info("[property-sync] repaired property %s occupancy projection", pid)
        else:
            conflicts += 1
            logger.warning("[property-sync] CAS lost for property %s; no overwrite", pid)

    return {
        "fixed": fixed,
        "skipped_manual": skipped_manual,
        "skipped_mutation": skipped_mutation,
        "unchanged": unchanged,
        "ambiguous": ambiguous,
        "conflicts": conflicts,
    }


async def property_sync_loop():
    """Long-running asyncio task. Reconciles every N minutes."""
    try:
        interval_min = int(os.environ.get("PROPERTY_SYNC_INTERVAL_MIN", "15"))
    except ValueError:
        interval_min = 15

    interval_sec = max(interval_min * 60, 60)
    await asyncio.sleep(30)

    logger.info("Property-Contract sync cron started (interval: %s min)", interval_min)

    while True:
        try:
            from rental.shared import get_db
            db = get_db()
            report = await reconcile_property_statuses(db)
            if report["fixed"] or report["ambiguous"] or report["conflicts"]:
                logger.info(
                    "[property-sync] run: %s fixed, %s manual, %s mutation-locked, %s unchanged, %s ambiguous, %s conflicts",
                    report["fixed"], report["skipped_manual"], report["skipped_mutation"], report["unchanged"],
                    report["ambiguous"], report["conflicts"],
                )
        except asyncio.CancelledError:
            logger.info("Property-sync cron cancelled")
            raise
        except Exception as e:
            logger.error("Property-sync cron iteration failed: %s", e)

        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info("Property-sync cron cancelled during sleep")
            raise
