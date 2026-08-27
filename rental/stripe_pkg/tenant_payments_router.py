"""Tenant rent payment endpoints — PaymentIntent creation + confirmation."""
import logging
from datetime import datetime
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_tenant_flex
from rental.stripe_pkg.helpers import _get_or_create_stripe_customer

router = APIRouter()


def _stripe_payment_identity_query(payment_intent_id: str) -> dict:
    """Match both historical field names used for Stripe PaymentIntent IDs."""
    return {"$or": [
        {"stripe_payment_intent_id": payment_intent_id},
        {"stripe_payment_intent": payment_intent_id},
        {"reference_number": payment_intent_id},
    ]}


def _intent_belongs_to_tenant(metadata, tenant_id) -> bool:
    """A tenant may only confirm the PaymentIntent created for that tenant."""
    if not hasattr(metadata, "get"):
        return False
    return bool(metadata.get("tenant_id")) and str(metadata.get("tenant_id")) == str(tenant_id)


def _stripe_rent_intent_idempotency_key(contract_id, year: int, month: int) -> str:
    """Stable provider key for one contract's rent PaymentIntent per month.

    The key deliberately excludes amount/late-fee values. If financial params
    change after an intent was created, Stripe will reject reuse of the same key
    with different parameters instead of silently creating a second payable PI.
    """
    return f"rent:{contract_id}:{int(year):04d}-{int(month):02d}"


@router.post('/tenant/create-stripe-payment')
async def tenant_create_stripe_payment(request: Request):
    """Tenant: Create a Stripe PaymentIntent for rent payment (with optional Connect split)."""
    tenant = await auth_tenant_flex(request)
    data = await request.json()

    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    stripe_secret = config.get("stripe_secret_key", "")
    stripe_enabled = config.get("stripe_enabled", False)
    commission_rate = config.get("commission_rate", 10.0)
    connect_enabled = config.get("connect_enabled", False)

    if not stripe_enabled or not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no está configurado. Contacte al administrador.")

    contract = await get_db().rental_contracts.find_one({
        "tenant_id": tenant["_id"],
        "status": "active"
    })
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")

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

    # SECURITY: base rent comes from the contract, never from the client.
    contract_rent = float(contract.get("rent_amount") or 0)
    late_fee = float(data.get("late_fee") or 0)
    if contract_rent > 0:
        amount = contract_rent
    else:
        amount = float(data.get("rent_amount") or data.get("amount") or 0)
    total = amount + late_fee

    if total <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")

    try:
        import stripe
        stripe.api_key = stripe_secret

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
                stripe_customer_id = tenant["stripe_customer_id"]
        except Exception as e:
            logging.warning(f"Stripe customer resolution failed for tenant {tenant.get('_id')}: {e}")
            stripe_customer_id = None

        property_id = str(contract.get("property_id", ""))
        owner_stripe_account = None

        if connect_enabled and property_id:
            listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(property_id)}) if property_id else None
            if listing and listing.get("owner_id"):
                owner = await get_db().app_users.find_one({"_id": ObjectId(listing["owner_id"])})
                if owner and owner.get("stripe_account_id") and owner.get("stripe_onboarding_status") == "active":
                    owner_stripe_account = owner["stripe_account_id"]

        intent_params = {
            "amount": int(round(total * 100)),
            "currency": "usd",
            "metadata": {
                "tenant_id": str(tenant["_id"]),
                "tenant_name": tenant.get("name", ""),
                "contract_id": str(contract["_id"]),
                "property_id": property_id,
                "period_month": current_month,
                "period_year": str(now.year),
                "period_month_num": str(now.month),
                "rent_amount": str(amount),
                "late_fee": str(late_fee),
            },
            "description": f"Renta {current_month.title()} {now.year} - {tenant.get('name', '')}",
            "receipt_email": tenant.get("email"),
        }

        if stripe_customer_id:
            intent_params["customer"] = stripe_customer_id
            intent_params["setup_future_usage"] = "off_session"

        if owner_stripe_account:
            application_fee = int(round(total * 100 * (commission_rate / 100)))
            intent_params["application_fee_amount"] = application_fee
            intent_params["transfer_data"] = {"destination": owner_stripe_account}
            intent_params["metadata"]["split_payment"] = "true"
            intent_params["metadata"]["commission_rate"] = str(commission_rate)
            intent_params["metadata"]["owner_stripe_account"] = owner_stripe_account

        if config.get("stripe_3ds_enabled"):
            intent_params["payment_method_options"] = {"card": {"request_three_d_secure": "any"}}

        # P0: provider-side idempotency prevents double taps, transport retries,
        # and concurrent requests from creating multiple payable intents for the
        # same contract/month.
        intent = stripe.PaymentIntent.create(
            **intent_params,
            idempotency_key=_stripe_rent_intent_idempotency_key(
                contract["_id"], now.year, now.month),
        )

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
    """Verify tenant-owned Stripe success; signed webhook owns financial writes.

    This endpoint intentionally does NOT create or complete rental_payments.
    Having both the mobile confirmation and Stripe webhook write financial rows
    caused duplicate records and a race. Stripe's signed succeeded webhook is the
    single authoritative writer; the client only needs a success acknowledgement.
    """
    tenant = await auth_tenant_flex(request)
    data = await request.json()

    payment_intent_id = data.get("payment_intent_id", "").strip()
    if not payment_intent_id:
        raise HTTPException(status_code=400, detail="payment_intent_id requerido")

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

        meta = intent.metadata or {}
        if not _intent_belongs_to_tenant(meta, tenant["_id"]):
            logging.warning(
                "Stripe confirm ownership mismatch: PI %s tenant=%s metadata_tenant=%s",
                payment_intent_id, tenant.get("_id"), meta.get("tenant_id") if hasattr(meta, "get") else None)
            raise HTTPException(status_code=403, detail="El pago no pertenece a este inquilino")

        contract_id = str(meta.get("contract_id") or "")
        if not contract_id:
            raise HTTPException(status_code=400, detail="PaymentIntent sin contrato asociado")

        try:
            contract_oid = ObjectId(contract_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Contrato de PaymentIntent inválido")
        contract = await get_db().rental_contracts.find_one({
            "_id": contract_oid,
            "tenant_id": tenant["_id"],
        })
        if not contract:
            raise HTTPException(status_code=403, detail="El contrato del pago no pertenece a este inquilino")

        existing = await get_db().rental_payments.find_one(
            _stripe_payment_identity_query(payment_intent_id))
        if existing:
            if str(existing.get("tenant_id", "")) != str(tenant["_id"]):
                raise HTTPException(status_code=403, detail="Registro de pago pertenece a otro inquilino")
            return {
                "success": True,
                "message": "Pago ya registrado",
                "payment_id": str(existing["_id"]),
                "receipt_number": existing.get("receipt_number", ""),
                "amount": float(existing.get("total_paid") or intent.amount / 100),
                "processing": False,
            }

        logging.info("Stripe PI %s verified for tenant %s; awaiting webhook record",
                     payment_intent_id, tenant.get("_id"))
        return {
            "success": True,
            "message": "Pago verificado; registrando recibo",
            "payment_intent_id": payment_intent_id,
            "amount": intent.amount / 100,
            "processing": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"❌ Stripe confirmation error: {e}")
        raise HTTPException(status_code=500, detail=f"Error verificando pago: {str(e)}")
