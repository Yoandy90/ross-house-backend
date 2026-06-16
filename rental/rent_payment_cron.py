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
DEFAULT_LATE_FEE_AMOUNT = 50.0           # fallback if neither contract nor config has one
DEFAULT_GRACE_PERIOD_DAYS = 5


async def _resolve_late_fee_config(db, contract: dict) -> tuple[float, int]:
    """Resolve the late fee amount and grace days from (in priority order):
       1. The contract itself (contract.late_fee_amount, contract.late_fee_grace_days)
       2. The rental_config company-wide defaults
       3. Hardcoded fallback ($50 / 5 days)
    """
    # 1) Per-contract
    if contract.get("late_fee_amount") is not None:
        try:
            amt = float(contract["late_fee_amount"])
            grace = int(contract.get("late_fee_grace_days", DEFAULT_GRACE_PERIOD_DAYS))
            return amt, grace
        except (TypeError, ValueError):
            pass

    # 2) Company-wide
    cfg = await db.rental_config.find_one({"type": "company"}) or {}
    if cfg.get("default_late_fee_amount") is not None:
        try:
            amt = float(cfg["default_late_fee_amount"])
            grace = int(cfg.get("default_late_fee_grace_days", DEFAULT_GRACE_PERIOD_DAYS))
            return amt, grace
        except (TypeError, ValueError):
            pass

    # 3) Fallback
    return DEFAULT_LATE_FEE_AMOUNT, DEFAULT_GRACE_PERIOD_DAYS


async def _parse_contract_date(value) -> datetime | None:
    """Best-effort parse a contract start/end date to UTC-aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            # Accept "YYYY-MM-DD" and ISO formats
            s = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s) if "T" in s else datetime.strptime(s[:10], "%Y-%m-%d")
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


async def ensure_current_period_payment(db, contract: dict) -> str:
    """Ensure a pending rental_payments doc exists for this contract's
    current period. Returns a short status string."""
    now = datetime.now(timezone.utc)
    period_month_name = now.strftime("%B")  # e.g. 'June'
    period_year = now.year
    period_iso = now.strftime(PERIOD_FORMAT)
    period_month_num = now.month

    contract_id = str(contract["_id"])

    # ── Validate contract date window ──
    # Don't generate or apply late fees outside the contract's effective range.
    start_dt = await _parse_contract_date(contract.get("start_date"))
    end_dt = await _parse_contract_date(contract.get("end_date"))
    if start_dt and now < start_dt:
        return "skip_not_started"
    if end_dt and now > end_dt:
        return "skip_ended"

    monthly_rent = float(
        contract.get("monthly_rent") or contract.get("rent_amount") or 0
    )
    if monthly_rent <= 0:
        return "skip_no_rent"

    late_fee_amount, grace_days = await _resolve_late_fee_config(db, contract)

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
        if status == "pending" and now.day > grace_days and late_fee_amount > 0:
            current_fee = float(existing.get("late_fee", 0) or 0)
            # Only update if the configured fee differs from what's stored
            if abs(current_fee - late_fee_amount) > 0.01:
                await db.rental_payments.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "late_fee": late_fee_amount,
                        "total_due": monthly_rent + late_fee_amount,
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
    async for c in db.rental_contracts.find({"status": {"$in": ["active", "activo"]}}, {"_id": 1}):
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

            # Validate contract date window for the *next* period too
            c_start = await _parse_contract_date(c.get("start_date"))
            c_end = await _parse_contract_date(c.get("end_date"))
            next_in_range = True
            if c_start and next_month_first < c_start:
                next_in_range = False
            if c_end and next_month_first > c_end:
                next_in_range = False

            if days_until_next <= 7 and next_in_range:
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
