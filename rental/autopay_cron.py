"""Auto-pay cron — charges saved cards on the configured day_of_month.

Runs every 6h. On each run:
  1. Fetches all autopay_config docs with enabled=true
  2. For each one, checks if today's day matches day_of_month
  3. Looks for a pending rental_payment for the current period
  4. Atomically claims the monthly attempt BEFORE any processor charge
  5. Charges the configured saved method
  6. Records the provider result; successful Stripe payments are finalized by webhook

Financial-integrity rule: the Mongo claim is the primary concurrency lock. Once
claimed, an ambiguous processor/network failure is NOT automatically retried in
the same month because the remote charge may actually have succeeded. Stripe
also receives a deterministic idempotency key as a second line of defense.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours
logger = logging.getLogger(__name__)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """UTC boundaries for the calendar month containing ``now``."""
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return month_start, next_month


def _stripe_autopay_idempotency_key(payment_id, now: datetime) -> str:
    """Stable Stripe key for one rental payment in one calendar month."""
    return f"autopay:{payment_id}:{now.year}-{now.month:02d}"


async def _claim_monthly_attempt(db, autopay_id, now: datetime) -> bool:
    """Atomically reserve this config's one charge attempt for the month.

    Only one concurrent worker can match and update the document. The claim is
    intentionally retained on provider/network errors because their outcome may
    be ambiguous; automatic retry could otherwise double-charge the tenant.
    """
    month_start, next_month = _month_bounds(now)
    result = await db.autopay_config.update_one(
        {
            "_id": autopay_id,
            "enabled": True,
            "$or": [
                {"last_attempt_date": {"$exists": False}},
                {"last_attempt_date": None},
                {"last_attempt_date": {"$lt": month_start}},
                {"last_attempt_date": {"$gte": next_month}},
            ],
        },
        {"$set": {
            "last_attempt_date": now,
            "last_attempt_status": "processing",
        }},
    )
    return result.modified_count == 1


async def _process_autopay_for_config(db, autopay):
    """Run a single autopay charge attempt if today is the configured day."""
    now = datetime.now(timezone.utc)
    today_day = now.day
    target_day = int(autopay.get("day_of_month") or 1)

    # Only attempt on the configured day (or after, if we missed earlier runs)
    if today_day < target_day:
        return {"skipped": True, "reason": f"not_yet_day_{target_day}"}

    # Fast-path only. The authoritative concurrency check is the atomic claim
    # immediately before the processor call.
    last_attempt = autopay.get("last_attempt_date")
    if last_attempt and isinstance(last_attempt, datetime):
        if last_attempt.year == now.year and last_attempt.month == now.month:
            return {"skipped": True, "reason": "already_attempted_this_month"}

    user_id = str(autopay.get("user_id", ""))
    payment_method_id = autopay.get("payment_method_id", "")
    if not payment_method_id:
        return {"skipped": True, "reason": "no_payment_method"}

    # Find the user's active contract
    contract = await db.rental_contracts.find_one({
        "tenant_id": user_id,
        "status": {"$in": ["active", "activo"]},
    })
    if not contract:
        try:
            tenant_doc = await db.tenants.find_one({"app_user_id": user_id})
            if tenant_doc:
                contract = await db.rental_contracts.find_one({
                    "tenant_id": str(tenant_doc["_id"]),
                    "status": {"$in": ["active", "activo"]},
                })
        except Exception:
            pass
    if not contract:
        return {"skipped": True, "reason": "no_active_contract"}

    # Find pending rental_payment for current period
    period = f"{now.year}-{str(now.month).zfill(2)}"
    pending = await db.rental_payments.find_one({
        "contract_id": str(contract["_id"]),
        "status": {"$in": ["pending", "late", "partial"]},
        "$or": [
            {"period": period},
            {"period_year": now.year, "period_month_num": now.month},
        ],
    })
    if not pending:
        return {"skipped": True, "reason": "no_pending_payment_for_period"}

    base_amount = float(pending.get("amount") or 0)
    late_fee = float(pending.get("late_fee") or 0)
    total = base_amount + late_fee
    if total <= 0:
        return {"skipped": True, "reason": "zero_amount"}

    autopay_id = autopay.get("_id")
    if autopay_id is None:
        logger.error("Autopay config without _id cannot be safely claimed")
        return {"skipped": True, "reason": "missing_autopay_id"}

    # ── HELCIM: saved-card token charge ──
    if autopay.get("processor") == "helcim" and autopay.get("helcim_card_token"):
        try:
            from .helcim_vault_router import helcim_purchase_with_token
            from .payment_processors_router import _get_doc, _active_creds
            cfg = _active_creds((await _get_doc())["processors"].get("helcim", {}))
            api_token = cfg.get("api_token", "")
            if not api_token:
                return {"skipped": True, "reason": "helcim_not_configured"}

            # P0: claim before the first provider-side operation.
            if not await _claim_monthly_attempt(db, autopay_id, now):
                return {"skipped": True, "reason": "already_attempted_this_month"}

            tx = await helcim_purchase_with_token(
                api_token, int(round(total * 100)),
                autopay["helcim_card_token"], autopay.get("helcim_customer_code", ""))
            status_tx = str(tx.get("status", "")).upper()
            await db.autopay_config.update_one({"_id": autopay_id}, {"$set": {
                "last_attempt_status": status_tx or "DECLINED",
                "last_result": status_tx or "DECLINED"}})
            if status_tx in ("APPROVED", "APPROVAL"):
                receipt = f"HLC-AUTO-{now.strftime('%Y%m%d')}-{user_id[-4:]}"
                await db.rental_payments.update_one({"_id": pending["_id"]}, {"$set": {
                    "status": "completed", "payment_method": "helcim_autopay",
                    "receipt_number": receipt,
                    "reference_number": str(tx.get("transactionId", "")),
                    "total_paid": total, "payment_date": now.isoformat(),
                    "updated_at": now}})
                logger.info("Autopago Helcim OK: user %s $%.2f (%s)", user_id, total, receipt)
                return {"charged": True, "success": True,
                        "processor": "helcim", "receipt": receipt}
            await db.autopay_config.update_one({"_id": autopay_id},
                                               {"$inc": {"retry_count": 1}})
            return {"charged": False, "success": False,
                    "processor": "helcim", "reason": status_tx}
        except Exception as e:  # noqa: BLE001
            logger.warning("Autopago Helcim falló user %s: %s", user_id, e)
            # Keep the prior monthly claim: provider outcome may be ambiguous.
            await db.autopay_config.update_one({"_id": autopay_id}, {"$set": {
                "last_attempt_status": "failed_unknown",
                "last_result": f"error: {str(e)[:120]}"}})
            return {"charged": False, "success": False,
                    "processor": "helcim", "reason": str(e)[:120]}

    # Load Stripe key from rental_config
    config = await db.rental_config.find_one({"type": "company"}) or {}
    sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_SECRET_KEY", "")
    if not sk:
        return {"skipped": True, "reason": "stripe_not_configured"}

    # Resolve Stripe customer id (stored on the user doc by setup endpoint)
    user_doc = None
    try:
        from bson import ObjectId as _OID
        user_doc = await db.app_users.find_one({"_id": _OID(user_id)})
    except Exception:
        pass
    customer_id = (user_doc or {}).get("stripe_customer_id", "")
    if not customer_id:
        return {"skipped": True, "reason": "no_stripe_customer"}

    # P0: atomic DB claim occurs after all local prerequisites but before charge.
    if not await _claim_monthly_attempt(db, autopay_id, now):
        return {"skipped": True, "reason": "already_attempted_this_month"}

    # Charge with Stripe (off-session). The deterministic provider idempotency
    # key protects against ambiguous client/network retries at Stripe as well.
    try:
        import stripe
        stripe.api_key = sk
        intent = stripe.PaymentIntent.create(
            amount=int(round(total * 100)),
            currency="usd",
            customer=customer_id,
            payment_method=payment_method_id,
            off_session=True,
            confirm=True,
            description=f"Autopago de renta — {now.strftime('%B %Y')}",
            receipt_email=autopay.get("user_email"),
            metadata={
                "autopay": "true",
                "payment_id": str(pending["_id"]),
                "contract_id": str(contract["_id"]),
                "tenant_id": user_id,
                "tenant_name": autopay.get("user_name", ""),
                "property_id": str(contract.get("property_id", "")),
                "period_month": now.strftime("%B").lower(),
                "period_year": str(now.year),
                "period_month_num": str(now.month),
                "rent_amount": str(base_amount),
                "late_fee": str(late_fee),
            },
            idempotency_key=_stripe_autopay_idempotency_key(pending["_id"], now),
        )

        # The rental_payments doc is finalized by the existing succeeded webhook.
        await db.autopay_config.update_one(
            {"_id": autopay_id},
            {"$set": {
                "last_attempt_status": intent.status,
                "last_attempt_intent_id": intent.id,
                "last_attempt_amount": total,
            }, "$inc": {"successful_charges": 1}},
        )
        logger.info("Autopay charged $%.2f for tenant %s (PI: %s, status: %s)",
                    total, autopay.get("user_email"), intent.id, intent.status)
        return {"success": True, "charged": True, "amount": total,
                "intent_id": intent.id, "status": intent.status}

    except Exception as e:  # noqa: BLE001
        err_str = str(e)
        # Never release the monthly claim here. Stripe may have accepted the
        # charge even when the local request ended with a transport exception.
        await db.autopay_config.update_one(
            {"_id": autopay_id},
            {"$set": {
                "last_attempt_status": "failed_unknown",
                "last_attempt_error": err_str[:500],
            }, "$inc": {"failed_charges": 1}},
        )
        logger.error("Autopay charge failed for %s: %s",
                     autopay.get("user_email"), err_str)
        return {"success": False, "charged": False, "error": err_str}


async def run_once(db):
    """Process all enabled autopay configs once."""
    stats = {
        "configs_checked": 0,
        "charged": 0,
        "skipped": 0,
        "failed": 0,
        "skip_reasons": {},
    }
    cursor = db.autopay_config.find({"enabled": True})
    async for ap in cursor:
        stats["configs_checked"] += 1
        try:
            result = await _process_autopay_for_config(db, ap)
            if result.get("skipped"):
                stats["skipped"] += 1
                reason = result.get("reason", "unknown")
                stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1
            elif result.get("success") or result.get("charged"):
                stats["charged"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            logger.exception("Autopay processing error: %s", e)
    logger.info("Autopay run complete: %s", stats)
    return stats


async def autopay_loop():
    """Background task — runs forever every 6h."""
    from rental.shared import get_db
    logger.info("Autopay loop started")
    while True:
        try:
            db = get_db()
            if db is not None:
                await run_once(db)
        except Exception as e:  # noqa: BLE001
            logger.exception("Autopay loop iteration failed: %s", e)
        await asyncio.sleep(DEFAULT_INTERVAL_SECONDS)
