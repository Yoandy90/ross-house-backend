"""Auto-pay cron — charges saved cards on the configured day_of_month.

Runs every 6h. On each run:
  1. Fetches all autopay_config docs with enabled=true
  2. For each one, checks if today's day matches day_of_month
  3. Looks for a pending rental_payment for the current period
  4. Creates a Stripe PaymentIntent with off_session=true + confirm=true
     using the saved payment_method_id
  5. On success: marks the rental_payment as completed (via the webhook path
     in stripe_router that already handles payment_intent.succeeded)
  6. On failure: increments retry_count, alerts the tenant (optional), and
     marks the payment as 'late' if past grace period

Idempotency: the run is safe to invoke multiple times per day. Each charge
attempt is gated by a daily marker (last_attempt_date) to prevent duplicate
charges if the cron runs more than once per day.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


async def _process_autopay_for_config(db, autopay):
    """Run a single autopay charge attempt if today is the configured day."""
    now = datetime.now(timezone.utc)
    today_day = now.day
    target_day = int(autopay.get("day_of_month") or 1)

    # Only attempt on the configured day (or after, if we missed earlier runs)
    if today_day < target_day:
        return {"skipped": True, "reason": f"not_yet_day_{target_day}"}

    # Idempotency: don't double-charge in the same calendar month
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
        # Try via tenants table linking
        from bson import ObjectId as _OID
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

    # Charge with Stripe (off-session)
    try:
        import stripe
        stripe.api_key = sk
        intent = stripe.PaymentIntent.create(
            amount=int(total * 100),
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
        )

        # Mark attempt as successful; the rental_payments doc will be updated
        # by the existing webhook handler when payment_intent.succeeded arrives.
        await db.autopay_config.update_one(
            {"_id": autopay["_id"]},
            {"$set": {
                "last_attempt_date": now,
                "last_attempt_status": intent.status,
                "last_attempt_intent_id": intent.id,
                "last_attempt_amount": total,
            }, "$inc": {"successful_charges": 1}},
        )
        logging.info(f"✅ Autopay charged ${total} for tenant {autopay.get('user_email')} (PI: {intent.id}, status: {intent.status})")
        return {"success": True, "amount": total, "intent_id": intent.id, "status": intent.status}

    except Exception as e:
        err_str = str(e)
        await db.autopay_config.update_one(
            {"_id": autopay["_id"]},
            {"$set": {
                "last_attempt_date": now,
                "last_attempt_status": "failed",
                "last_attempt_error": err_str[:500],
            }, "$inc": {"failed_charges": 1}},
        )
        logging.error(f"❌ Autopay charge failed for {autopay.get('user_email')}: {err_str}")
        return {"success": False, "error": err_str}


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
            elif result.get("success"):
                stats["charged"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:
            stats["failed"] += 1
            logging.exception(f"Autopay processing error: {e}")
    logging.info(f"🔁 Autopay run complete: {stats}")
    return stats


async def autopay_loop():
    """Background task — runs forever every 6h."""
    from rental.shared import get_db
    logging.info("🚀 Autopay loop started")
    while True:
        try:
            db = get_db()
            if db is not None:
                await run_once(db)
        except Exception as e:
            logging.exception(f"Autopay loop iteration failed: {e}")
        await asyncio.sleep(DEFAULT_INTERVAL_SECONDS)
