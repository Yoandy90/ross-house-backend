"""Tenant rent payment endpoints — PaymentIntent creation + confirmation."""
import logging
from datetime import datetime
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_tenant_flex
from rental.stripe_pkg.helpers import _get_or_create_stripe_customer

router = APIRouter()


@router.post('/tenant/create-stripe-payment')
async def tenant_create_stripe_payment(request: Request):
    """Tenant: Create a Stripe PaymentIntent for rent payment (with optional Connect split)"""
    tenant = await auth_tenant_flex(request)
    data = await request.json()

    # Get Stripe config from rental_config
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    stripe_secret = config.get("stripe_secret_key", "")
    stripe_enabled = config.get("stripe_enabled", False)
    commission_rate = config.get("commission_rate", 10.0)
    connect_enabled = config.get("connect_enabled", False)

    if not stripe_enabled or not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no está configurado. Contacte al administrador.")

    # Get active contract
    contract = await get_db().rental_contracts.find_one({
        "tenant_id": tenant["_id"],
        "status": "active"
    })
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")

    # Check if already paid this month
    now = datetime.utcnow()
    current_month = now.strftime('%B').lower()
    existing = await get_db().rental_payments.find_one({
        "contract_id": str(contract["_id"]),
        "period_month": {"$regex": f"^{current_month[:3]}", "$options": "i"},
        "period_year": now.year,
        "status": {"$in": ["completed", "paid", "pending_verification"]}
    })
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un pago registrado para este mes")

    # ─── Amount resolution (avoid double-counting late fee) ──────────────
    # New client behavior: sends rent_amount (base) + late_fee separately, so
    # the source of truth is rent_amount + late_fee.
    # Legacy client behavior: sends amount as the FULL total (incl. late fee).
    if "rent_amount" in data:
        amount = float(data.get("rent_amount") or 0)
        late_fee = float(data.get("late_fee") or 0)
        total = amount + late_fee
    else:
        # Fallback for older clients: treat `amount` as the full total
        total = float(data.get("amount") or contract.get("rent_amount") or 0)
        amount = total
        late_fee = 0.0

    if total <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")

    try:
        import stripe
        stripe.api_key = stripe_secret

        # ─── Resolve Stripe Customer so saved cards appear in PaymentSheet ────
        # Saved payment methods are stored against `app_users.stripe_customer_id`
        # (the "Métodos de Pago" screen uses `auth_marketplace`).
        # The `auth_tenant_flex` returns a `tenants` doc, so we have to find the
        # linked app_user to reuse the same Stripe customer.
        stripe_customer_id = None
        ephemeral_key_secret = None
        try:
            app_user = None
            app_user_id = tenant.get("app_user_id")
            if app_user_id:
                try:
                    app_user = await get_db().app_users.find_one({"_id": ObjectId(app_user_id)})
                except Exception:
                    app_user = None
            if not app_user and tenant.get("email"):
                import re as _re
                _email = tenant["email"].strip().lower()
                app_user = await get_db().app_users.find_one({
                    "email": {"$regex": f"^{_re.escape(_email)}$", "$options": "i"}
                })

            if app_user:
                stripe_customer_id = await _get_or_create_stripe_customer(app_user)
            elif tenant.get("stripe_customer_id"):
                # Last resort: tenant doc itself has one stored
                stripe_customer_id = tenant["stripe_customer_id"]
        except Exception as e:
            logging.warning(f"Stripe customer resolution failed for tenant {tenant.get('_id')}: {e}")
            stripe_customer_id = None

        # Check if property has a connected owner (Stripe Connect split payment)
        property_id = str(contract.get("property_id", ""))
        owner_stripe_account = None

        if connect_enabled and property_id:
            # Look for marketplace listing with an owner who has Stripe Connect
            listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(property_id)}) if property_id else None
            if listing and listing.get("owner_id"):
                owner = await get_db().app_users.find_one({"_id": ObjectId(listing["owner_id"])})
                if owner and owner.get("stripe_account_id") and owner.get("stripe_onboarding_status") == "active":
                    owner_stripe_account = owner["stripe_account_id"]

        # Build PaymentIntent params
        intent_params = {
            "amount": int(total * 100),
            "currency": "usd",
            "metadata": {
                "tenant_id": str(tenant["_id"]),
                "tenant_name": tenant.get("name", ""),
                "contract_id": str(contract["_id"]),
                "property_id": property_id,
                "period_month": current_month,
                "period_year": str(now.year),
                "rent_amount": str(amount),
                "late_fee": str(late_fee),
            },
            "description": f"Renta {current_month.title()} {now.year} - {tenant.get('name', '')}",
            "receipt_email": tenant.get("email"),
        }

        # Attach customer (enables saved cards in PaymentSheet) and persist
        # future-use so any new card entered also gets saved automatically.
        if stripe_customer_id:
            intent_params["customer"] = stripe_customer_id
            intent_params["setup_future_usage"] = "off_session"

        # If owner has Stripe Connect, add automatic split
        if owner_stripe_account:
            application_fee = int(total * 100 * (commission_rate / 100))
            intent_params["application_fee_amount"] = application_fee
            intent_params["transfer_data"] = {"destination": owner_stripe_account}
            intent_params["metadata"]["split_payment"] = "true"
            intent_params["metadata"]["commission_rate"] = str(commission_rate)
            intent_params["metadata"]["owner_stripe_account"] = owner_stripe_account

        intent = stripe.PaymentIntent.create(**intent_params)

        # Create an Ephemeral Key so the mobile PaymentSheet can list/manage
        # the customer's saved payment methods.
        if stripe_customer_id:
            try:
                ek = stripe.EphemeralKey.create(
                    customer=stripe_customer_id,
                    stripe_version="2024-06-20",
                )
                ephemeral_key_secret = ek.secret
            except Exception as e:
                logging.warning(f"EphemeralKey creation failed: {e}")
                ephemeral_key_secret = None

        return {
            "success": True,
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "amount": total,
            "publishable_key": config.get("stripe_publishable_key", ""),
            "split_payment": bool(owner_stripe_account),
            "commission_rate": commission_rate if owner_stripe_account else 0,
            "customer_id": stripe_customer_id or "",
            "ephemeral_key": ephemeral_key_secret or "",
        }

    except Exception as e:
        logging.error(f"❌ Stripe PaymentIntent error: {e}")
        raise HTTPException(status_code=500, detail=f"Error de Stripe: {str(e)}")


@router.post('/tenant/confirm-stripe-payment')
async def tenant_confirm_stripe_payment(request: Request):
    """Tenant: Confirm a successful Stripe payment and create the payment record"""
    tenant = await auth_tenant_flex(request)
    data = await request.json()

    payment_intent_id = data.get("payment_intent_id", "").strip()
    if not payment_intent_id:
        raise HTTPException(status_code=400, detail="payment_intent_id requerido")

    # Verify with Stripe
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    stripe_secret = config.get("stripe_secret_key", "")

    if not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no configurado")

    try:
        import stripe
        stripe.api_key = stripe_secret
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)

        if intent.status != "succeeded":
            raise HTTPException(status_code=400, detail=f"Pago no completado. Estado: {intent.status}")

        meta = intent.metadata
        now = datetime.utcnow()

        # Check for duplicate
        existing = await get_db().rental_payments.find_one({"stripe_payment_intent": payment_intent_id})
        if existing:
            return {"success": True, "message": "Pago ya registrado", "payment_id": str(existing["_id"])}

        amount = float(meta.get("rent_amount", 0))
        late_fee = float(meta.get("late_fee", 0))
        total = intent.amount / 100  # Convert from cents

        receipt_number = f"STR-{now.strftime('%Y%m%d')}-{str(tenant['_id'])[-4:]}"

        payment = {
            "contract_id": meta.get("contract_id", ""),
            "property_id": meta.get("property_id", ""),
            "tenant_id": str(tenant["_id"]),
            "tenant_name": tenant.get("name", ""),
            "amount": amount,
            "late_fee": late_fee,
            "total_paid": total,
            "payment_method": "stripe",
            "reference_number": payment_intent_id,
            "stripe_payment_intent": payment_intent_id,
            "receipt_number": receipt_number,
            "period_month": meta.get("period_month", now.strftime('%B').lower()),
            "period_year": int(meta.get("period_year", now.year)),
            "payment_date": now.isoformat(),
            "status": "completed",
            "submitted_by": "tenant_stripe",
            "submitted_at": now,
            "created_at": now,
            "updated_at": now,
        }

        result = await get_db().rental_payments.insert_one(payment)
        logging.info(f"✅ Stripe payment confirmed: {receipt_number} for {tenant.get('name')} - ${total}")

        return {
            "success": True,
            "message": "Pago procesado exitosamente con Stripe",
            "payment_id": str(result.inserted_id),
            "receipt_number": receipt_number,
            "amount": total,
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"❌ Stripe confirmation error: {e}")
        raise HTTPException(status_code=500, detail=f"Error verificando pago: {str(e)}")
