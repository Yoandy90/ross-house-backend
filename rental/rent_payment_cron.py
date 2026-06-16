"""
Monthly rent payment auto-generation cron.

Every active contract should have a pending rental_payments doc for the
current period so the tenant can pay it from the mobile app.

Runs every 6 hours. Idempotent: re-running won't create duplicates.

Algorithm:
  1. For each contract with status in ('active','activo'):
       a) Determine current period 'YYYY-MM'
       b) Look up a payment with same contract_id + period_month/year
       c) If missing, insert a {status: 'pending'} doc with due_date = day 1
  2. Past-due payments older than 5 days inherit a $50 late fee
     (one-time, won't be doubled)
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("rent_payment_cron")

PERIOD_FORMAT = "%Y-%m"
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60   # every 6h
LATE_FEE_AMOUNT = 50.0
GRACE_PERIOD_DAYS = 5


async def ensure_current_period_payment(db, contract: dict) -> str:
    """Ensure a pending rental_payments doc exists for this contract's
    current period. Returns a short status string."""
    now = datetime.now(timezone.utc)
    period_month_name = now.strftime("%B")  # e.g. 'June'
    period_year = now.year
    period_iso = now.strftime(PERIOD_FORMAT)
    period_month_num = now.month

    contract_id = str(contract["_id"])
    monthly_rent = float(
        contract.get("monthly_rent") or contract.get("rent_amount") or 0
    )
    if monthly_rent <= 0:
        return "skip_no_rent"

    # Look for existing payment for this period
    existing = await db.rental_payments.find_one({
        "contract_id": contract_id,
        "$or": [
            {"period": period_iso},
            {"period_year": period_year, "period_month_num": period_month_num},
            {"period_year": period_year, "period_month": period_month_name},
        ],
    })

    if existing:
        # Apply late fee if past grace period and still unpaid
        status = existing.get("status", "pending")
        if status == "pending" and now.day > GRACE_PERIOD_DAYS:
            if not existing.get("late_fee"):
                await db.rental_payments.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "late_fee": LATE_FEE_AMOUNT,
                        "total_due": monthly_rent + LATE_FEE_AMOUNT,
                        "late_fee_applied_at": now,
                    }},
                )
                return "late_fee_applied"
        return "already_exists"

    # Generate receipt number placeholder (real one assigned on payment)
    due_date = datetime(period_year, period_month_num, 1, tzinfo=timezone.utc)

    payment_doc = {
        "contract_id": contract_id,
        "property_id": str(contract.get("property_id", "")),
        "property_address": contract.get("property_address", ""),
        "tenant_id": str(contract.get("tenant_id", "")),
        "tenant_name": contract.get("tenant_name", ""),
        "amount": monthly_rent,
        "late_fee": 0.0,
        "total_due": monthly_rent,
        "period": period_iso,
        "period_month": period_month_name,
        "period_month_num": period_month_num,
        "period_year": period_year,
        "due_date": due_date,
        "status": "pending",
        "paid": False,
        "auto_generated": True,
        "created_at": now,
    }
    await db.rental_payments.insert_one(payment_doc)
    return "created"


async def run_once(db) -> dict:
    """Run a single pass: ensure current-period payment for every active contract.
    Also creates next month's payment 7 days before its start to give the
    tenant visibility ahead of time."""
    stats = {"created": 0, "already_exists": 0, "late_fee_applied": 0, "skip_no_rent": 0, "errors": 0, "orphans_cleaned": 0}

    # ── Cleanup: archive payments whose contract no longer exists ──
    valid_ids = set()
    async for c in db.rental_contracts.find({}, {"_id": 1}):
        valid_ids.add(str(c["_id"]))
    async for p in db.rental_payments.find({"status": {"$in": ["pending", "late", "partial"]}}):
        if str(p.get("contract_id", "")) not in valid_ids:
            await db.rental_payments_archive.insert_one({**p, "archived_reason": "orphan_no_contract"})
            await db.rental_payments.delete_one({"_id": p["_id"]})
            stats["orphans_cleaned"] += 1

    cursor = db.rental_contracts.find({"status": {"$in": ["active", "activo"]}})
    async for c in cursor:
        try:
            status = await ensure_current_period_payment(db, c)
            stats[status] = stats.get(status, 0) + 1
        except Exception as e:
            logger.exception(f"Failed for contract {c.get('_id')}: {e}")
            stats["errors"] += 1

        # Also pre-generate next-month payment if we're within 7 days of month end
        try:
            now = datetime.now(timezone.utc)
            # Compute next month
            if now.month == 12:
                next_month_first = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                next_month_first = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
            days_until_next = (next_month_first - now).days
            if days_until_next <= 7:
                # Temporarily simulate "now" being the next month
                contract_copy = dict(c)
                # We use a fake current period directly:
                next_period_iso = next_month_first.strftime(PERIOD_FORMAT)
                existing_next = await db.rental_payments.find_one({
                    "contract_id": str(c["_id"]),
                    "period": next_period_iso,
                })
                if not existing_next:
                    monthly_rent = float(c.get("monthly_rent") or c.get("rent_amount") or 0)
                    if monthly_rent > 0:
                        payment_doc = {
                            "contract_id": str(c["_id"]),
                            "property_id": str(c.get("property_id", "")),
                            "property_address": c.get("property_address", ""),
                            "tenant_id": str(c.get("tenant_id", "")),
                            "tenant_name": c.get("tenant_name", ""),
                            "amount": monthly_rent,
                            "late_fee": 0.0,
                            "total_due": monthly_rent,
                            "period": next_period_iso,
                            "period_month": next_month_first.strftime("%B"),
                            "period_month_num": next_month_first.month,
                            "period_year": next_month_first.year,
                            "due_date": next_month_first,
                            "status": "pending",
                            "paid": False,
                            "auto_generated": True,
                            "created_at": now,
                        }
                        await db.rental_payments.insert_one(payment_doc)
                        stats["created"] += 1
        except Exception as e:
            logger.exception(f"Failed pre-generating next month for {c.get('_id')}: {e}")

    logger.info(f"💵 Rent auto-gen pass: {stats}")
    return stats


async def rent_payment_loop():
    """Background loop. Runs every 6h."""
    from .shared import get_db
    await asyncio.sleep(20)  # let startup settle
    while True:
        try:
            db = get_db()
            await run_once(db)
        except Exception as e:
            logger.exception(f"Rent cron loop error: {e}")
        await asyncio.sleep(DEFAULT_INTERVAL_SECONDS)
