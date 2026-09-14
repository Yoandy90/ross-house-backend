"""Persistent invoice-level exclusion across saved cards, autopay and checkout.

Claims are never expired or released automatically: a timeout cannot prove that
money did not move. Reconciliation is required before another attempt.
"""
import uuid
import math
from calendar import month_name
from datetime import datetime, timezone
from bson import ObjectId
from .rent_charge_policy import period_query


async def claim_rent_charge(db, invoice_id, *, source, amount, contract_id,
                            checkout_id=None, autopay_id=None):
    if not math.isfinite(amount) or amount <= 0:
        return None
    now = datetime.now(timezone.utc)
    ids = [invoice_id]
    if ObjectId.is_valid(str(invoice_id)):
        ids.append(ObjectId(str(invoice_id)))
    invoice = await db.rental_payments.find_one({"_id": {"$in": ids}, "contract_id": str(contract_id)})
    if not invoice or invoice.get("status") not in ("pending", "late", "partial"):
        return None
    month_num = int(invoice.get("period_month_num") or 0)
    if not 1 <= month_num <= 12:
        period_value = str(invoice.get("period") or "")
        if len(period_value) == 7 and period_value[4] == "-":
            try:
                month_num = int(period_value[5:7])
            except ValueError:
                month_num = 0
        label = str(invoice.get("period_month") or "").lower()
        if not 1 <= month_num <= 12 and label:
            month_num = next((i for i in range(1, 13)
                              if month_name[i].lower().startswith(label[:3])), now.month)
        if not 1 <= month_num <= 12:
            month_num = now.month
    period_start = datetime(int(invoice.get("period_year") or now.year),
                            month_num, 1, tzinfo=timezone.utc)
    # Existing checkouts and pre-migration attempts for this exact invoice month
    # remain blockers. Future-month claims must not be blocked by the current month.
    pending_query = period_query(contract_id, period_start)
    pending_query.pop("record_type", None)
    pending_query.pop("invoice_id", None)
    pending_query["$or"].append({
        "period_year": period_start.year,
        "period_month": {"$regex": f"^{period_start.strftime('%B')[:3]}", "$options": "i"},
    })
    pending_query.update({"status": {"$in": ["pending_checkout", "creating_checkout",
        "checkout_creation_unknown", "pending_verification"]}})
    if checkout_id is not None:
        pending_query["_id"] = {"$ne": checkout_id}
    if await db.rental_payments.find_one(pending_query):
        return None
    prior_query = {"last_attempt_invoice_id": str(invoice_id)}
    if autopay_id is not None:
        prior_query["_id"] = {"$ne": autopay_id}
    if await db.autopay_config.find_one(prior_query):
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
