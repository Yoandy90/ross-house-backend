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


async def _notify_admin(subject: str, message: str):
    """Best-effort admin notification (email via SendGrid + SMS via Twilio).
    Never raises — webhook processing must not fail because of a notification."""
    db = get_db()
    api_cfg = await db.api_config.find_one({'_id': 'main'}) or {}
    company_cfg = await db.rental_config.find_one({'type': 'company'}) or {}

    # ── Email ──
    try:
        sendgrid_key = os.getenv('SENDGRID_API_KEY') or api_cfg.get('sendgrid_api_key', '')
        from_email = os.getenv('SENDGRID_FROM_EMAIL') or api_cfg.get('sendgrid_from_email', 'info@rosshouserentals.com')
        recipients = {e for e in ['yoandyross@gmail.com', company_cfg.get('email', '')] if e}
        if sendgrid_key and recipients:
            import sendgrid
            from sendgrid.helpers.mail import Mail, Email, To, Content
            sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
            for rcpt in recipients:
                mail = Mail(
                    from_email=Email(from_email, "Ross House Rentals"),
                    to_emails=To(rcpt),
                    subject=subject,
                    plain_text_content=Content("text/plain", message),
                )
                sg.client.mail.send.post(request_body=mail.get())
            logging.info(f"📧 Admin notificado por email: {subject}")
    except Exception as e:
        logging.warning(f"⚠️ No se pudo enviar email de notificación: {e}")

    # ── SMS ──
    try:
        twilio_sid = os.getenv('TWILIO_ACCOUNT_SID') or api_cfg.get('twilio_account_sid', '')
        twilio_token = os.getenv('TWILIO_AUTH_TOKEN') or api_cfg.get('twilio_auth_token', '')
        twilio_phone = os.getenv('TWILIO_PHONE_NUMBER') or api_cfg.get('twilio_phone_number', '')
        admin_phone = api_cfg.get('company_phone') or company_cfg.get('phone', '')
        if twilio_sid and twilio_token and twilio_phone and admin_phone:
            digits = ''.join(filter(str.isdigit, admin_phone))
            to_phone = f'+1{digits[-10:]}' if len(digits) >= 10 else admin_phone
            from twilio.rest import Client
            Client(twilio_sid, twilio_token).messages.create(
                body=f"{subject}\n{message}"[:1500], from_=twilio_phone, to=to_phone)
            logging.info(f"📱 Admin notificado por SMS ({to_phone[-4:]})")
    except Exception as e:
        logging.warning(f"⚠️ No se pudo enviar SMS de notificación: {e}")


@router.post('/stripe/connect-webhook')
async def stripe_connect_webhook(request: Request):
    """
    Stripe Connect Webhook endpoint.
    Handles account.updated events to auto-update owner onboarding status.
    Also handles payment-related events for automatic tracking.
    """
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature', '')

    # Get webhook secret(s): try BOTH the env var and the DB config value.
    # (If the env var in Railway is stale/wrong, the DB secret still validates.)
    config = await _get_stripe_config()
    secrets = [s for s in (
        os.getenv('STRIPE_WEBHOOK_SECRET', ''),
        config.get('stripe_webhook_secret', ''),
    ) if s]

    if not secrets:
        # SECURITY: refuse to process unsigned webhooks. Without signature
        # verification anyone could forge a 'payment succeeded' event and mark
        # rent as paid. Fail closed instead of trusting the payload.
        logging.error("❌ Stripe webhook secret not configured — rejecting unsigned webhook")
        raise HTTPException(status_code=503, detail="Webhook not configured (missing STRIPE_WEBHOOK_SECRET)")

    import stripe
    stripe.api_key = config.get("stripe_secret_key", "")
    event = None
    last_err = None
    for secret in secrets:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            break
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except Exception as e:
            last_err = e
    if event is None:
        logging.error(f"❌ Stripe webhook signature verification failed: {last_err}")
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(last_err)}")

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

    # ── checkout.session.completed / async_payment_succeeded:
    #    payment-link payment finished (cards are instant; ACH completes days
    #    later via checkout.session.async_payment_succeeded) ──
    elif event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        try:
            sess = event_data if isinstance(event_data, dict) else event_data.__dict__
            sess_get = sess.get if isinstance(sess, dict) else (lambda k, d=None: getattr(event_data, k, d))
            meta = sess_get('metadata', {}) or {}
            plink_id = sess_get('payment_link', '')
            customer_id = sess_get('customer', '')
            payment_status = sess_get('payment_status', '')  # 'paid' | 'unpaid'
            amount_total = (sess_get('amount_total', 0) or 0) / 100
            now = datetime.utcnow()

            # Mark our payment_links record. For async methods (ACH) the
            # 'completed' event arrives with payment_status='unpaid' while the
            # debit clears → mark as 'processing' until async_payment_succeeded.
            reference = (meta.get('reference', '') if hasattr(meta, 'get') else '') or 'sin referencia'
            cust_details_n = sess_get('customer_details', {}) or {}
            payer_email = cust_details_n.get('email', '') if isinstance(cust_details_n, dict) else ''
            if plink_id:
                if payment_status == 'paid':
                    link_update = {"status": "paid", "paid_at": now}
                else:
                    link_update = {"status": "processing"}
                await get_db().payment_links.update_one(
                    {"stripe_payment_link_id": plink_id},
                    {"$set": {**link_update,
                              "stripe_customer_id": customer_id,
                              "amount_paid": amount_total, "updated_at": now}},
                )

            # Fetch the payment method FIRST (needed for vault + notification detail)
            import stripe as _stripe
            pm_ref = None
            try:
                pi_id = sess_get('payment_intent', '')
                if pi_id:
                    pi = _stripe.PaymentIntent.retrieve(pi_id, expand=['payment_method'])
                    pm = pi.payment_method
                    if pm and (getattr(pm, 'card', None) or getattr(pm, 'us_bank_account', None)):
                        pm_ref = pm
            except Exception as e:
                logging.warning(f"payment-link: could not expand payment_method: {e}")

            # Build payer detail line: bank + account holder type → dispute risk
            pay_detail = ""
            if pm_ref is not None and getattr(pm_ref, 'us_bank_account', None):
                _b = pm_ref.us_bank_account
                _holder = (_b.account_holder_type or '').lower()
                if _holder == 'company':
                    _risk = "Cuenta de EMPRESA 🏢 — ventana de reclamo: solo 2 días hábiles (bajo riesgo)"
                elif _holder == 'individual':
                    _risk = "Cuenta PERSONAL 👤 — ventana de reclamo: hasta 60 días (mantén contrato/factura)"
                else:
                    _risk = "Tipo de titular no reportado"
                pay_detail = f"\nBanco: {(_b.bank_name or '').title()} ····{_b.last4} ({_b.account_type or 'checking'})\n{_risk}"
            elif pm_ref is not None and getattr(pm_ref, 'card', None):
                _c = pm_ref.card
                pay_detail = f"\nTarjeta: {(_c.brand or '').title()} ····{_c.last4}"

            # Notify admin about the payment status
            if event_type == 'checkout.session.async_payment_succeeded':
                await _notify_admin(
                    f"✅ Pago ACH COMPENSADO — ${amount_total:,.2f}",
                    f"El pago ACH de ${amount_total:,.2f} ({reference}) compensó exitosamente.\nPagador: {payer_email}{pay_detail}\nEl dinero está en camino a tu cuenta Stripe.")
            elif payment_status == 'paid':
                await _notify_admin(
                    f"✅ Pago recibido — ${amount_total:,.2f}",
                    f"Pago completado de ${amount_total:,.2f} ({reference}).\nPagador: {payer_email}{pay_detail}\nMétodo de pago guardado en el Baúl.")
            else:
                await _notify_admin(
                    f"🕓 Pago ACH iniciado — ${amount_total:,.2f}",
                    f"Se inició un pago ACH de ${amount_total:,.2f} ({reference}).\nPagador: {payer_email}{pay_detail}\nCompensará en ~4 días hábiles. Te avisaré cuando compense o rebote.")

            # Save the payment-method REFERENCE into the Vault — card OR bank
            # (never the full PAN/CVV/account number — PCI)
            if pm_ref is not None:
                existing = await get_db().payment_methods.find_one({"stripe_payment_method_id": pm_ref.id})
                if not existing:
                    cust_details = sess_get('customer_details', {}) or {}
                    base_doc = {
                        "user_id": meta.get('tenant_id', '') if hasattr(meta, 'get') else '',
                        "user_name": meta.get('tenant_name', '') if hasattr(meta, 'get') else '',
                        "user_email": cust_details.get('email', '') if isinstance(cust_details, dict) else '',
                        "stripe_payment_method_id": pm_ref.id,
                        "stripe_customer_id": customer_id,
                        "is_default": False,
                        "is_active_for_autopay": False,
                        "source": "payment_link",
                        "reference": meta.get('reference', '') if hasattr(meta, 'get') else '',
                        "created_at": now,
                    }
                    if getattr(pm_ref, 'card', None):
                        card = pm_ref.card
                        await get_db().payment_methods.insert_one({
                            **base_doc,
                            "type": "card",
                            "card_brand": (card.brand or '').title(),
                            "card_last4": card.last4,
                            "card_exp": f"{card.exp_month:02d}/{str(card.exp_year)[-2:]}",
                        })
                        logging.info(f"🔐 Vault: saved card reference ····{card.last4} from payment link")
                    else:
                        bank = pm_ref.us_bank_account
                        await get_db().payment_methods.insert_one({
                            **base_doc,
                            "type": "bank",
                            "bank_name": (bank.bank_name or '').title(),
                            "account_last4": bank.last4,
                            "last4": bank.last4,
                            "account_type": bank.account_type or "checking",
                            "account_holder_type": bank.account_holder_type or "",
                            "account_holder_name": getattr(pm_ref.billing_details, 'name', '') or '',
                        })
                        logging.info(f"🔐 Vault: saved bank (ACH) reference {bank.bank_name} ····{bank.last4} from payment link")
        except Exception as pl_err:
            logging.exception(f"⚠️ Failed to process {event_type}: {pl_err}")

    # ── checkout.session.async_payment_failed: ACH debit bounced ──
    elif event_type == 'checkout.session.async_payment_failed':
        try:
            sess_get = event_data.get if isinstance(event_data, dict) else (lambda k, d=None: getattr(event_data, k, d))
            plink_id = sess_get('payment_link', '')
            meta_f = sess_get('metadata', {}) or {}
            amount_f = (sess_get('amount_total', 0) or 0) / 100
            cust_f = sess_get('customer_details', {}) or {}
            if plink_id:
                await get_db().payment_links.update_one(
                    {"stripe_payment_link_id": plink_id},
                    {"$set": {"status": "failed", "updated_at": datetime.utcnow()}},
                )
                logging.warning(f"❌ Payment link {plink_id}: async payment FAILED (ACH bounced)")
            await _notify_admin(
                f"❌ Pago ACH REBOTÓ — ${amount_f:,.2f}",
                f"El pago ACH de ${amount_f:,.2f} ({(meta_f.get('reference','') if hasattr(meta_f,'get') else '') or 'sin referencia'}) FALLÓ (fondos insuficientes o cuenta inválida).\n"
                f"Pagador: {cust_f.get('email','') if isinstance(cust_f, dict) else ''}\n"
                f"Contacta al cliente para reintentar el pago.")
        except Exception as e:
            logging.exception(f"⚠️ Failed to process async_payment_failed: {e}")

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
