"""Stripe webhook endpoint + admin webhook events listing."""
import os
import json
import logging
from datetime import datetime
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin
from rental.stripe_pkg.helpers import _get_stripe_config

router = APIRouter()


@router.post('/stripe/connect-webhook')
async def stripe_connect_webhook(request: Request):
    """
    Stripe Connect Webhook endpoint.
    Handles account.updated events to auto-update owner onboarding status.
    Also handles payment-related events for automatic tracking.
    """
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature', '')

    # Get webhook secret from env or DB config
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    if not webhook_secret:
        config = await _get_stripe_config()
        webhook_secret = config.get('stripe_webhook_secret', '')

    if not webhook_secret:
        logging.warning("⚠️ Stripe webhook secret not configured, processing without verification")
        try:
            event = json.loads(payload)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid payload")
    else:
        try:
            import stripe
            config = await _get_stripe_config()
            stripe.api_key = config.get("stripe_secret_key", "")
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except Exception as e:
            logging.error(f"❌ Stripe webhook signature verification failed: {e}")
            raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    event_type = event.get('type', '') if isinstance(event, dict) else event.type
    event_data = event.get('data', {}).get('object', {}) if isinstance(event, dict) else event.data.object

    logging.info(f"📩 Stripe Connect Webhook: {event_type}")

    # ── account.updated: Track onboarding completion ──
    if event_type == 'account.updated':
        account_id = event_data.get('id', '') if isinstance(event_data, dict) else event_data.id
        charges_enabled = event_data.get('charges_enabled', False) if isinstance(event_data, dict) else getattr(event_data, 'charges_enabled', False)
        payouts_enabled = event_data.get('payouts_enabled', False) if isinstance(event_data, dict) else getattr(event_data, 'payouts_enabled', False)
        details_submitted = event_data.get('details_submitted', False) if isinstance(event_data, dict) else getattr(event_data, 'details_submitted', False)

        # Determine status
        if charges_enabled and payouts_enabled:
            status = "active"
        elif details_submitted:
            status = "pending_verification"
        else:
            status = "incomplete"

        # Update owner in DB
        result = await get_db().app_users.update_one(
            {"stripe_account_id": account_id},
            {"$set": {
                "stripe_onboarding_status": status,
                "stripe_charges_enabled": charges_enabled,
                "stripe_payouts_enabled": payouts_enabled,
                "stripe_details_submitted": details_submitted,
                "stripe_last_webhook_at": datetime.utcnow(),
            }}
        )

        if result.modified_count > 0:
            owner = await get_db().app_users.find_one({"stripe_account_id": account_id})
            owner_name = owner.get("name", "Unknown") if owner else "Unknown"
            logging.info(f"✅ Stripe Connect: Owner '{owner_name}' status → {status} (charges={charges_enabled}, payouts={payouts_enabled})")
        else:
            logging.warning(f"⚠️ Stripe Connect: No owner found for account {account_id}")

    # ── transfer.created: Track payouts to owners ──
    elif event_type == 'transfer.created':
        transfer_id = event_data.get('id', '') if isinstance(event_data, dict) else event_data.id
        amount = (event_data.get('amount', 0) if isinstance(event_data, dict) else getattr(event_data, 'amount', 0)) / 100
        destination = event_data.get('destination', '') if isinstance(event_data, dict) else getattr(event_data, 'destination', '')
        logging.info(f"💸 Stripe Transfer created: ${amount:.2f} → {destination} (ID: {transfer_id})")

    # ── payment_intent.succeeded: Track successful payments ──
    elif event_type == 'payment_intent.succeeded':
        pi_id = event_data.get('id', '') if isinstance(event_data, dict) else event_data.id
        amount = (event_data.get('amount', 0) if isinstance(event_data, dict) else getattr(event_data, 'amount', 0)) / 100
        metadata = event_data.get('metadata', {}) if isinstance(event_data, dict) else getattr(event_data, 'metadata', {}) or {}
        logging.info(f"💳 Payment succeeded: ${amount:.2f} (PI: {pi_id}) meta={dict(metadata) if metadata else {}}")

        # ─── Link Stripe payment to auto-generated rental_payments doc ───
        try:
            contract_id = metadata.get('contract_id') if hasattr(metadata, 'get') else None
            period_year = metadata.get('period_year') if hasattr(metadata, 'get') else None
            period_month = metadata.get('period_month') if hasattr(metadata, 'get') else None
            rent_amount = float(metadata.get('rent_amount', amount)) if hasattr(metadata, 'get') and metadata.get('rent_amount') else amount
            late_fee = float(metadata.get('late_fee', 0)) if hasattr(metadata, 'get') and metadata.get('late_fee') else 0.0

            if contract_id and period_year:
                # Find pending auto-generated doc for this contract+period
                pending = await get_db().rental_payments.find_one({
                    "contract_id": contract_id,
                    "period_year": int(period_year),
                    "status": "pending",
                    "$or": [
                        {"period_month": {"$regex": f"^{(period_month or '')[:3]}", "$options": "i"}},
                        {"period_month_num": datetime.utcnow().month},
                    ],
                })

                now = datetime.utcnow()
                pay_count = await get_db().rental_payments.count_documents({"status": {"$in": ["completed", "paid"]}})
                receipt_number = f"REC-{now.year}-{str(pay_count + 1).zfill(4)}"

                update_doc = {
                    "status": "completed",
                    "paid": True,
                    "payment_method": "stripe",
                    "payment_date": now,
                    "total_paid": amount,
                    "amount": rent_amount,
                    "late_fee": late_fee,
                    "stripe_payment_intent_id": pi_id,
                    "receipt_number": receipt_number,
                    "updated_at": now,
                }
                if pending:
                    await get_db().rental_payments.update_one(
                        {"_id": pending["_id"]},
                        {"$set": update_doc},
                    )
                    logging.info(f"✅ Linked PI {pi_id} to pending rental_payment {pending['_id']} ({receipt_number})")
                else:
                    # No auto-doc found — create a fresh completed record so it
                    # still shows up in the tenant's invoice history.
                    contract = None
                    try:
                        contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
                    except Exception:
                        pass
                    new_doc = {
                        "contract_id": contract_id,
                        "property_id": str(contract.get("property_id", "")) if contract else metadata.get("property_id", ""),
                        "property_address": contract.get("property_address", "") if contract else "",
                        "tenant_id": metadata.get("tenant_id", "") if hasattr(metadata, 'get') else "",
                        "tenant_name": metadata.get("tenant_name", "") if hasattr(metadata, 'get') else "",
                        "period_year": int(period_year),
                        "period_month": period_month or now.strftime("%B"),
                        "period_month_num": now.month,
                        "period": f"{int(period_year)}-{str(now.month).zfill(2)}",
                        "auto_generated": False,
                        "created_at": now,
                        **update_doc,
                    }
                    await get_db().rental_payments.insert_one(new_doc)
                    logging.info(f"✅ Created new completed rental_payment for PI {pi_id} ({receipt_number})")
        except Exception as link_err:
            logging.exception(f"⚠️ Failed to link Stripe PI to rental_payments: {link_err}")

    # ── Log all events for audit ──
    try:
        await get_db().stripe_webhook_events.insert_one({
            "event_id": event.get('id', '') if isinstance(event, dict) else event.id,
            "event_type": event_type,
            "account_id": event_data.get('id', '') if isinstance(event_data, dict) else getattr(event_data, 'id', ''),
            "processed_at": datetime.utcnow(),
            "livemode": event.get('livemode', False) if isinstance(event, dict) else getattr(event, 'livemode', False),
        })
    except Exception as e:
        logging.warning(f"⚠️ Could not log webhook event: {e}")

    return {"received": True}


@router.get('/admin/stripe/webhook-events')
async def admin_list_webhook_events(request: Request):
    """Admin: List recent Stripe webhook events for monitoring"""
    await auth_admin(request)
    limit = int(request.query_params.get("limit", "50"))

    events = []
    cursor = get_db().stripe_webhook_events.find().sort("processed_at", -1).limit(limit)
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        events.append(doc)

    return {"success": True, "events": events, "total": len(events)}
