"""Auto-pay cron — charges saved methods on the configured day of month.

Runs every 6h. Financial-integrity rules:
- canonical monthly invoice is the only source of the amount to charge;
- an atomic Mongo claim is acquired before any processor-side charge;
- ambiguous provider/network failures retain the claim, because the remote
  charge may have succeeded;
- Stripe receives a deterministic idempotency key as a second barrier.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from rental.rent_charge_policy import resolve_current_rent_charge

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60
logger = logging.getLogger(__name__)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """UTC boundaries for the calendar month containing ``now``."""
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return month_start, next_month


def _stripe_autopay_idempotency_key(invoice_id, now: datetime) -> str:
    """Stable Stripe key for one canonical rent invoice in one calendar month."""
    return f"autopay:{invoice_id}:{now.year}-{now.month:02d}"


async def _claim_monthly_attempt(
    db,
    autopay_id,
    now: datetime,
    *,
    invoice_id: str = "",
    amount: float = 0,
) -> bool:
    """Atomically reserve this config's one automatic charge attempt this month.

    Persist the canonical invoice id and exact server-side amount in the same
    claim write. That gives later reconciliation a trusted local linkage even
    when the process crashes immediately after claiming and before the provider
    response is available.
    """
    month_start, _next_month = _month_bounds(now)
    claim_set = {
        "last_attempt_date": now,
        "last_attempt_status": "processing",
    }
    if invoice_id:
        claim_set["last_attempt_invoice_id"] = str(invoice_id)
    if float(amount or 0) > 0:
        claim_set["last_attempt_amount"] = round(float(amount), 2)

    result = await db.autopay_config.update_one(
        {
            "_id": autopay_id,
            "enabled": True,
            "$or": [
                {"last_attempt_date": {"$exists": False}},
                {"last_attempt_date": None},
                {"last_attempt_date": {"$lt": month_start}},
            ],
        },
        {"$set": claim_set},
    )
    return result.modified_count == 1


async def _process_autopay_for_config(db, autopay):
    """Run a single automatic rent charge attempt when eligible."""
    now = datetime.now(timezone.utc)
    target_day = int(autopay.get("day_of_month") or 1)
    if now.day < target_day:
        return {"skipped": True, "reason": f"not_yet_day_{target_day}"}

    # Fast path only. The atomic claim below is authoritative for concurrency.
    last_attempt = autopay.get("last_attempt_date")
    if last_attempt and isinstance(last_attempt, datetime):
        if (last_attempt.year, last_attempt.month) >= (now.year, now.month):
            return {"skipped": True, "reason": "already_attempted_this_month"}

    user_id = str(autopay.get("user_id", ""))
    payment_method_id = autopay.get("payment_method_id", "")
    if not payment_method_id and not (
        autopay.get("processor") == "helcim" and autopay.get("helcim_card_token")
    ):
        return {"skipped": True, "reason": "no_payment_method"}

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

    try:
        charge = await resolve_current_rent_charge(db, contract, now)
    except (ValueError, RuntimeError):
        return {"skipped": True, "reason": "no_chargeable_invoice"}

    invoice = charge["invoice"]
    invoice_id = charge["invoice_id"]
    total = float(charge.get("outstanding") or 0)
    base_amount = float(charge.get("amount") or 0)
    late_fee = float(charge.get("late_fee") or 0)
    total_due = float(charge.get("total_due") or 0)
    prior_paid = float(charge.get("total_paid") or 0)
    if total <= 0:
        return {"skipped": True, "reason": "zero_amount"}

    autopay_id = autopay.get("_id")
    if autopay_id is None:
        logger.error("Autopay config without _id cannot be safely claimed")
        return {"skipped": True, "reason": "missing_autopay_id"}

    # HELCIM: authoritative saved-card token charge.
    if autopay.get("processor") == "helcim" and autopay.get("helcim_card_token"):
        try:
            from .helcim_vault_router import helcim_purchase_with_token
            from .payment_processors_router import _get_doc, _active_creds

            cfg = _active_creds((await _get_doc())["processors"].get("helcim", {}))
            api_token = cfg.get("api_token", "")
            if not api_token:
                return {"skipped": True, "reason": "helcim_not_configured"}

            if not await _claim_monthly_attempt(
                db, autopay_id, now, invoice_id=invoice_id, amount=total
            ):
                return {"skipped": True, "reason": "already_attempted_this_month"}

            tx = await helcim_purchase_with_token(
                api_token,
                int(round(total * 100)),
                autopay["helcim_card_token"],
                autopay.get("helcim_customer_code", ""),
            )
            status_tx = str(tx.get("status", "")).upper()
            await db.autopay_config.update_one(
                {"_id": autopay_id},
                {"$set": {
                    "last_attempt_status": status_tx or "DECLINED",
                    "last_result": status_tx or "DECLINED",
                    "last_attempt_amount": total,
                    "last_attempt_invoice_id": str(invoice_id),
                }},
            )
            if status_tx in ("APPROVED", "APPROVAL"):
                receipt = f"HLC-AUTO-{now.strftime('%Y%m%d')}-{user_id[-4:]}"
                result = await db.rental_payments.update_one(
                    {"_id": invoice["_id"], "status": {"$in": ["pending", "late", "partial"]}},
                    {"$set": {
                        "status": "completed",
                        "paid": True,
                        "payment_method": "helcim_autopay",
                        "receipt_number": receipt,
                        "reference_number": str(tx.get("transactionId", "")),
                        "total_paid": total_due,
                        "payment_date": now,
                        "updated_at": now,
                    }},
                )
                if result.modified_count != 1:
                    await db.autopay_config.update_one(
                        {"_id": autopay_id},
                        {"$set": {
                            "last_attempt_status": "reconciliation_required",
                            "last_result": "invoice_reconciliation_required",
                            "last_attempt_amount": total,
                            "last_attempt_invoice_id": str(invoice_id),
                        }},
                    )
                    logger.error("Helcim autopay charged but invoice transition was not applied: %s", invoice_id)
                    return {"charged": True, "success": False, "processor": "helcim",
                            "reason": "invoice_reconciliation_required"}
                logger.info("Autopay Helcim OK: invoice %s $%.2f", invoice_id, total)
                return {"charged": True, "success": True, "processor": "helcim", "receipt": receipt}

            await db.autopay_config.update_one(
                {"_id": autopay_id}, {"$inc": {"retry_count": 1}})
            return {"charged": False, "success": False,
                    "processor": "helcim", "reason": status_tx or "DECLINED"}
        except Exception as e:  # noqa: BLE001
            logger.warning("Autopay Helcim failed user %s: %s", user_id, e)
            await db.autopay_config.update_one(
                {"_id": autopay_id},
                {"$set": {
                    "last_attempt_status": "failed_unknown",
                    "last_result": f"error: {str(e)[:120]}",
                    "last_attempt_invoice_id": str(invoice_id),
                    "last_attempt_amount": total,
                }},
            )
            return {"charged": False, "success": False,
                    "processor": "helcim", "reason": str(e)[:120]}

    config = await db.rental_config.find_one({"type": "company"}) or {}
    sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_SECRET_KEY", "")
    if not sk:
        return {"skipped": True, "reason": "stripe_not_configured"}

    user_doc = None
    try:
        from bson import ObjectId as _OID
        user_doc = await db.app_users.find_one({"_id": _OID(user_id)})
    except Exception:
        pass
    customer_id = (user_doc or {}).get("stripe_customer_id", "")
    if not customer_id:
        return {"skipped": True, "reason": "no_stripe_customer"}

    if not await _claim_monthly_attempt(
        db, autopay_id, now, invoice_id=invoice_id, amount=total
    ):
        return {"skipped": True, "reason": "already_attempted_this_month"}

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
                "payment_id": invoice_id,
                "invoice_id": invoice_id,
                "contract_id": str(contract["_id"]),
                "tenant_id": str(invoice.get("tenant_id") or user_id),
                "tenant_name": autopay.get("user_name", ""),
                "property_id": str(contract.get("property_id", "")),
                "period_month": now.strftime("%B").lower(),
                "period_year": str(now.year),
                "period_month_num": str(now.month),
                "rent_amount": str(base_amount),
                "late_fee": str(late_fee),
                "invoice_total_due": str(total_due),
                "invoice_total_paid": str(prior_paid),
                "charge_amount": str(total),
            },
            idempotency_key=_stripe_autopay_idempotency_key(invoice_id, now),
        )

        await db.autopay_config.update_one(
            {"_id": autopay_id},
            {"$set": {
                "last_attempt_status": intent.status,
                "last_attempt_intent_id": intent.id,
                "last_attempt_amount": total,
                "last_attempt_invoice_id": str(invoice_id),
            }, "$inc": {"successful_charges": 1}},
        )
        logger.info("Autopay Stripe charged invoice %s $%.2f (PI=%s, status=%s)",
                    invoice_id, total, intent.id, intent.status)
        return {"success": True, "charged": True, "amount": total,
                "intent_id": intent.id, "status": intent.status}

    except Exception as e:  # noqa: BLE001
        err_str = str(e)
        # Keep the claim. A transport exception does not prove Stripe failed.
        await db.autopay_config.update_one(
            {"_id": autopay_id},
            {"$set": {
                "last_attempt_status": "failed_unknown",
                "last_attempt_error": err_str[:500],
                "last_attempt_invoice_id": str(invoice_id),
                "last_attempt_amount": total,
            }, "$inc": {"failed_charges": 1}},
        )
        logger.error("Autopay Stripe failed for %s: %s", autopay.get("user_email"), err_str)
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
