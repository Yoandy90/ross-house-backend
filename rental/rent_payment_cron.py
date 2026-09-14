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
import hashlib
import logging
from calendar import monthrange
from datetime import datetime, timezone, timedelta

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

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


def canonical_invoice_id(contract_id: str, year: int, month: int) -> ObjectId:
    """Database-enforced identity for exactly one canonical invoice per period."""
    raw = f"rent-invoice:{contract_id}:{year:04d}-{month:02d}".encode()
    return ObjectId(hashlib.sha256(raw).hexdigest()[:24])


async def ensure_period_payment(db, contract: dict, period_start: datetime,
                                *, apply_late_fee: bool = False) -> tuple[str, dict | None]:
    """Ensure and return one canonical invoice for a lease month.

    The deterministic ``_id`` makes concurrent creation safe at MongoDB level.
    Legacy invoices are reused so this can be deployed without a data migration.
    """
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=timezone.utc)
    period_start = period_start.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    period_end = period_start.replace(
        day=monthrange(period_start.year, period_start.month)[1],
        hour=23, minute=59, second=59, microsecond=999999,
    )
    contract_id = str(contract["_id"])
    start_dt = await _parse_contract_date(contract.get("start_date"))
    end_dt = await _parse_contract_date(contract.get("end_date"))
    if start_dt and period_end < start_dt:
        return "skip_not_started", None
    if end_dt and period_start > end_dt:
        return "skip_ended", None

    monthly_rent = float(contract.get("monthly_rent") or contract.get("rent_amount") or 0)
    if monthly_rent <= 0:
        return "skip_no_rent", None

    period_iso = period_start.strftime(PERIOD_FORMAT)
    period_month_name = period_start.strftime("%B")
    existing = await db.rental_payments.find_one({
        "contract_id": contract_id,
        "record_type": {"$ne": "checkout_attempt"},
        "invoice_id": {"$exists": False},
        "$or": [
            {"period": period_iso},
            {"period_year": period_start.year, "period_month_num": period_start.month},
            {"period_year": period_start.year, "period_month": period_month_name},
        ],
    })
    if existing:
        if apply_late_fee and existing.get("status", "pending") == "pending":
            late_fee_amount, grace_days = await _resolve_late_fee_config(db, contract)
            now = datetime.now(timezone.utc)
            due_date = datetime(period_start.year, period_start.month, 1, tzinfo=timezone.utc)
            grace_deadline = due_date + timedelta(days=max(grace_days, 0))
            current_fee = float(existing.get("late_fee", 0) or 0)
            if now > grace_deadline and late_fee_amount > 0 and abs(current_fee - late_fee_amount) > 0.01:
                await db.rental_payments.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"late_fee": late_fee_amount,
                              "total_due": monthly_rent + late_fee_amount,
                              "late_fee_applied_at": now}},
                )
                existing = await db.rental_payments.find_one({"_id": existing["_id"]})
                return "late_fee_applied", existing
        return "already_exists", existing

    now = datetime.now(timezone.utc)
    payment_doc = {
        "_id": canonical_invoice_id(contract_id, period_start.year, period_start.month),
        "record_type": "invoice",
        "contract_id": contract_id,
        "property_id": str(contract.get("property_id", "")),
        "property_address": contract.get("property_address", ""),
        "tenant_id": str(contract.get("tenant_id", "")),
        "tenant_name": contract.get("tenant_name", ""),
        "amount": monthly_rent,
        "late_fee": 0.0,
        "total_due": monthly_rent,
        "total_paid": 0.0,
        "period": period_iso,
        "period_month": period_month_name,
        "period_month_num": period_start.month,
        "period_year": period_start.year,
        "due_date": period_start,
        "status": "pending",
        "paid": False,
        "auto_generated": True,
        "created_at": now,
    }
    try:
        await db.rental_payments.insert_one(payment_doc)
        return "created", payment_doc
    except DuplicateKeyError:
        existing = await db.rental_payments.find_one({"_id": payment_doc["_id"]})
        return "already_exists", existing


async def ensure_current_period_payment(db, contract: dict) -> str:
    """Ensure a pending rental_payments doc exists for this contract's
    current period. Returns a short status string."""
    now = datetime.now(timezone.utc)
    status, _ = await ensure_period_payment(db, contract, now, apply_late_fee=True)
    return status


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
                status, _ = await ensure_period_payment(db, c, next_month_first)
                stats[status] = stats.get(status, 0) + 1
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
