"""Persistent invoice-level exclusion across saved cards, autopay and checkout.

Claims are never expired or released automatically: a timeout cannot prove that
money did not move. Reconciliation is required before another attempt.
"""
import uuid
import math
from datetime import datetime, timezone
from bson import ObjectId
from .rent_charge_policy import current_period_query


async def claim_rent_charge(db, invoice_id, *, source, amount, contract_id,
                            checkout_id=None, autopay_id=None):
    if not math.isfinite(amount) or amount <= 0:
        return None
    now = datetime.now(timezone.utc)
    # Existing checkouts and pre-migration automatic attempts remain blockers.
    pending_query = current_period_query(contract_id, now)
    pending_query["$or"].append({"period_year": now.year,
        "period_month": {"$regex": f"^{now.strftime('%B')[:3]}", "$options": "i"}})
    pending_query.update({"status": {"$in": ["pending_checkout", "creating_checkout",
        "checkout_creation_unknown", "pending_verification"]}})
    if checkout_id is not None:
        pending_query["_id"] = {"$ne": checkout_id}
    if await db.rental_payments.find_one(pending_query):
        return None
    prior_query = {"last_attempt_invoice_id": str(invoice_id),
                   "last_attempt_date": {"$gte": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)}}
    if autopay_id is not None:
        prior_query["_id"] = {"$ne": autopay_id}
    if await db.autopay_config.find_one(prior_query):
        return None
    ids = [invoice_id]
    if ObjectId.is_valid(str(invoice_id)):
        ids.append(ObjectId(str(invoice_id)))
    invoice = await db.rental_payments.find_one({"_id": {"$in": ids}, "contract_id": str(contract_id)})
    if not invoice or invoice.get("status") not in ("pending", "late", "partial"):
        return None
    from .rent_charge_policy import invoice_balance
    if invoice_balance(invoice)["outstanding"] != amount or amount <= 0:
        return None
    query = {"_id": invoice["_id"], "charge_attempt": {"$exists": False}}
    for key in ("status", "amount", "late_fee", "total_due", "total_paid"):
        query[key] = invoice[key] if key in invoice else {"$exists": False}
    attempt = {"id": uuid.uuid4().hex, "source": source,
               "amount": amount, "status": "processing", "created_at": now}
    result = await db.rental_payments.update_one(query, {"$set": {"charge_attempt": attempt}})
    return attempt if result.modified_count == 1 else None
