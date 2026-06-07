"""
Rental Stripe Router
=====================
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import os
import json
from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

router = APIRouter()

async def _get_stripe_config():
    """Helper: Get Stripe configuration"""
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    return config


@router.post('/admin/connect/configure')
async def admin_configure_connect(request: Request):
    """Admin: Configure Stripe Connect settings (commission rate, etc.)"""
    await auth_admin(request)
    data = await request.json()

    update = {"updated_at": datetime.utcnow()}
    if "commission_rate" in data:
        rate = float(data["commission_rate"])
        if rate < 0 or rate > 100:
            raise HTTPException(status_code=400, detail="La comisión debe estar entre 0 y 100%")
        update["commission_rate"] = rate
    if "connect_enabled" in data:
        update["connect_enabled"] = bool(data["connect_enabled"])

    await get_db().rental_config.update_one({"type": "company"}, {"$set": update}, upsert=True)
    return {"success": True, "message": "Configuración de Connect actualizada"}


@router.get('/admin/connect/status')
async def admin_connect_status(request: Request):
    """Admin: Get Stripe Connect configuration and connected accounts overview"""
    await auth_admin(request)

    config = await _get_stripe_config()
    commission_rate = config.get("commission_rate", 10.0)
    connect_enabled = config.get("connect_enabled", False)
    stripe_secret = config.get("stripe_secret_key", "")

    # Count connected owners
    connected = await get_db().app_users.count_documents({"role": "landlord", "stripe_account_id": {"$exists": True, "$ne": ""}})
    pending = await get_db().app_users.count_documents({"role": "landlord", "stripe_account_id": {"$exists": False}})

    # Recent payouts
    recent_payouts = []
    cursor = get_db().owner_payouts.find().sort("created_at", -1).limit(10)
    async for p in cursor:
        doc = serialize(p)
        recent_payouts.append(doc)

    return {
        "success": True,
        "commission_rate": commission_rate,
        "connect_enabled": connect_enabled,
        "stripe_configured": bool(stripe_secret),
        "connected_owners": connected,
        "pending_owners": pending,
        "recent_payouts": recent_payouts,
    }


@router.post('/owner/connect/onboard')
async def owner_connect_onboard(request: Request):
    """Owner: Create a Stripe Connect account and get onboarding link"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios pueden conectar Stripe")

    config = await _get_stripe_config()
    stripe_secret = config.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no está configurado por el administrador")

    data = await request.json()
    return_url = data.get("return_url", "https://rosshouserentals.com/owner/connect/complete")
    refresh_url = data.get("refresh_url", "https://rosshouserentals.com/owner/connect/refresh")

    try:
        import stripe
        stripe.api_key = stripe_secret

        user_id = str(user["_id"])
        existing_account = user.get("stripe_account_id", "")

        if existing_account:
            # Already has an account, create a new account link for re-onboarding
            account_link = stripe.AccountLink.create(
                account=existing_account,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding",
            )
            return {
                "success": True,
                "account_id": existing_account,
                "onboarding_url": account_link.url,
                "message": "Enlace de onboarding regenerado"
            }

        # Create new Connect Express account
        account = stripe.Account.create(
            type="express",
            country="US",
            email=user.get("email", ""),
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            business_type="individual",
            metadata={
                "owner_id": user_id,
                "owner_name": user.get("name", ""),
                "platform": "ross_house_rentals",
            },
        )

        # Save account ID to user
        await get_db().app_users.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "stripe_account_id": account.id,
                "stripe_onboarding_status": "pending",
                "stripe_connected_at": datetime.utcnow(),
            }}
        )

        # Create account link for onboarding
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )

        return {
            "success": True,
            "account_id": account.id,
            "onboarding_url": account_link.url,
            "message": "Cuenta de Stripe creada. Completa la verificación."
        }

    except Exception as e:
        logging.error(f"❌ Stripe Connect onboard error: {e}")
        raise HTTPException(status_code=500, detail=f"Error de Stripe: {str(e)}")


@router.get('/owner/connect/status')
async def owner_connect_status(request: Request):
    """Owner: Check Stripe Connect account status"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    stripe_account_id = user.get("stripe_account_id", "")
    if not stripe_account_id:
        return {
            "success": True,
            "connected": False,
            "status": "not_connected",
            "message": "No has conectado tu cuenta de Stripe aún"
        }

    config = await _get_stripe_config()
    stripe_secret = config.get("stripe_secret_key", "")
    if not stripe_secret:
        return {"success": True, "connected": False, "status": "stripe_not_configured"}

    try:
        import stripe
        stripe.api_key = stripe_secret
        account = stripe.Account.retrieve(stripe_account_id)

        charges_enabled = account.get("charges_enabled", False)
        payouts_enabled = account.get("payouts_enabled", False)
        details_submitted = account.get("details_submitted", False)

        if charges_enabled and payouts_enabled:
            status = "active"
            message = "Tu cuenta está activa y puede recibir pagos"
        elif details_submitted:
            status = "pending_verification"
            message = "Tu información está siendo verificada por Stripe"
        else:
            status = "incomplete"
            message = "Necesitas completar la verificación de tu cuenta"

        # Update DB
        await get_db().app_users.update_one(
            {"_id": user["_id"]},
            {"$set": {"stripe_onboarding_status": status}}
        )

        return {
            "success": True,
            "connected": True,
            "status": status,
            "charges_enabled": charges_enabled,
            "payouts_enabled": payouts_enabled,
            "details_submitted": details_submitted,
            "message": message,
        }

    except Exception as e:
        logging.error(f"❌ Stripe Connect status error: {e}")
        return {"success": True, "connected": False, "status": "error", "message": str(e)}


@router.get('/owner/payouts')
async def owner_get_payouts(request: Request):
    """Owner: Get payout history"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    user_id = str(user["_id"])
    cursor = get_db().owner_payouts.find({"owner_id": user_id}).sort("created_at", -1).limit(50)
    payouts = []
    async for p in cursor:
        doc = serialize(p)
        payouts.append({
            "id": doc.get("_id"),
            "amount": doc.get("amount", 0),
            "commission": doc.get("commission", 0),
            "net_amount": doc.get("net_amount", 0),
            "property_address": doc.get("property_address", ""),
            "tenant_name": doc.get("tenant_name", ""),
            "period": doc.get("period", ""),
            "status": doc.get("status", "pending"),
            "stripe_transfer_id": doc.get("stripe_transfer_id", ""),
            "created_at": doc.get("created_at", ""),
        })

    # Summary stats
    pipeline = [
        {"$match": {"owner_id": user_id, "status": "completed"}},
        {"$group": {
            "_id": None,
            "total_earned": {"$sum": "$net_amount"},
            "total_commission": {"$sum": "$commission"},
            "total_payouts": {"$sum": 1},
        }}
    ]
    stats = {}
    async for s in get_db().owner_payouts.aggregate(pipeline):
        stats = s

    return {
        "success": True,
        "payouts": payouts,
        "stats": {
            "total_earned": stats.get("total_earned", 0),
            "total_commission": stats.get("total_commission", 0),
            "total_payouts": stats.get("total_payouts", 0),
        }
    }


@router.post('/admin/connect/process-payout')
async def admin_process_payout(request: Request):
    """Admin: Process a payout to a property owner via Stripe Connect transfer"""
    await auth_admin(request)
    data = await request.json()

    owner_id = data.get("owner_id", "")
    amount = float(data.get("amount", 0))
    property_address = data.get("property_address", "")
    tenant_name = data.get("tenant_name", "")
    period = data.get("period", "")

    if not owner_id or amount <= 0:
        raise HTTPException(status_code=400, detail="owner_id y amount requeridos")

    # Get owner's Stripe account
    try:
        owner = await get_db().app_users.find_one({"_id": ObjectId(owner_id)})
    except:
        raise HTTPException(status_code=400, detail="ID de propietario inválido")

    if not owner:
        raise HTTPException(status_code=404, detail="Propietario no encontrado")

    stripe_account_id = owner.get("stripe_account_id", "")
    if not stripe_account_id:
        raise HTTPException(status_code=400, detail="El propietario no tiene cuenta de Stripe conectada")

    config = await _get_stripe_config()
    stripe_secret = config.get("stripe_secret_key", "")
    commission_rate = config.get("commission_rate", 10.0)

    if not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no configurado")

    # Calculate commission
    commission = round(amount * (commission_rate / 100), 2)
    net_amount = round(amount - commission, 2)

    try:
        import stripe
        stripe.api_key = stripe_secret

        # Create transfer to connected account
        transfer = stripe.Transfer.create(
            amount=int(net_amount * 100),  # cents
            currency="usd",
            destination=stripe_account_id,
            description=f"Pago renta - {property_address} - {period}",
            metadata={
                "owner_id": owner_id,
                "owner_name": owner.get("name", ""),
                "property_address": property_address,
                "tenant_name": tenant_name,
                "period": period,
                "gross_amount": str(amount),
                "commission_rate": str(commission_rate),
                "commission": str(commission),
            },
        )

        # Record payout
        payout_record = {
            "owner_id": owner_id,
            "owner_name": owner.get("name", ""),
            "stripe_account_id": stripe_account_id,
            "stripe_transfer_id": transfer.id,
            "amount": amount,
            "commission_rate": commission_rate,
            "commission": commission,
            "net_amount": net_amount,
            "property_address": property_address,
            "tenant_name": tenant_name,
            "period": period,
            "status": "completed",
            "created_at": datetime.utcnow(),
        }
        result = await get_db().owner_payouts.insert_one(payout_record)

        logging.info(f"✅ Payout processed: ${net_amount} to {owner.get('name')} (Transfer: {transfer.id})")

        return {
            "success": True,
            "transfer_id": transfer.id,
            "amount": amount,
            "commission": commission,
            "net_amount": net_amount,
            "payout_id": str(result.inserted_id),
            "message": f"Pago de ${net_amount:.2f} enviado exitosamente (comisión: ${commission:.2f})"
        }

    except Exception as e:
        # Save failed payout for retry
        await get_db().owner_payouts.insert_one({
            "owner_id": owner_id,
            "owner_name": owner.get("name", ""),
            "amount": amount,
            "commission": commission,
            "net_amount": net_amount,
            "property_address": property_address,
            "period": period,
            "status": "failed",
            "error": str(e),
            "created_at": datetime.utcnow(),
        })
        logging.error(f"❌ Payout error: {e}")
        raise HTTPException(status_code=500, detail=f"Error al procesar pago: {str(e)}")


@router.get('/admin/connect/owners')
async def admin_list_connected_owners(request: Request):
    """Admin: List all owners and their Stripe Connect status"""
    await auth_admin(request)

    cursor = get_db().app_users.find({"role": "landlord"}).sort("created_at", -1)
    owners = []
    async for u in cursor:
        doc = serialize(u)

        # Get payout stats for this owner
        pipeline = [
            {"$match": {"owner_id": doc.get("_id", ""), "status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$net_amount"}, "count": {"$sum": 1}}}
        ]
        stats = {}
        async for s in get_db().owner_payouts.aggregate(pipeline):
            stats = s

        owners.append({
            "id": doc.get("_id"),
            "name": doc.get("name", ""),
            "email": doc.get("email", ""),
            "phone": doc.get("phone", ""),
            "stripe_account_id": doc.get("stripe_account_id", ""),
            "stripe_status": doc.get("stripe_onboarding_status", "not_connected"),
            "total_payouts": stats.get("total", 0),
            "payout_count": stats.get("count", 0),
        })

    return {"success": True, "owners": owners, "count": len(owners)}


# ═══════════════════════════════════════════════════════════════════════════════
# STRIPE PAYMENT INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/tenant/create-stripe-payment')
async def tenant_create_stripe_payment(request: Request):
    """Tenant: Create a Stripe PaymentIntent for rent payment (with optional Connect split)"""
    tenant = await auth_tenant(request)
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

    amount = data.get("amount", contract.get("rent_amount", 0))
    late_fee = data.get("late_fee", 0)
    total = float(amount) + float(late_fee)

    if total <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")

    try:
        import stripe
        stripe.api_key = stripe_secret

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

        # If owner has Stripe Connect, add automatic split
        if owner_stripe_account:
            application_fee = int(total * 100 * (commission_rate / 100))
            intent_params["application_fee_amount"] = application_fee
            intent_params["transfer_data"] = {"destination": owner_stripe_account}
            intent_params["metadata"]["split_payment"] = "true"
            intent_params["metadata"]["commission_rate"] = str(commission_rate)
            intent_params["metadata"]["owner_stripe_account"] = owner_stripe_account

        intent = stripe.PaymentIntent.create(**intent_params)

        return {
            "success": True,
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "amount": total,
            "publishable_key": config.get("stripe_publishable_key", ""),
            "split_payment": bool(owner_stripe_account),
            "commission_rate": commission_rate if owner_stripe_account else 0,
        }

    except Exception as e:
        logging.error(f"❌ Stripe PaymentIntent error: {e}")
        raise HTTPException(status_code=500, detail=f"Error de Stripe: {str(e)}")


@router.post('/tenant/confirm-stripe-payment')
async def tenant_confirm_stripe_payment(request: Request):
    """Tenant: Confirm a successful Stripe payment and create the payment record"""
    tenant = await auth_tenant(request)
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


@router.get('/admin/rental-stripe-config')
async def get_stripe_config(request: Request):
    """Admin: Get Stripe configuration (masked keys) for rental module"""
    user = await auth_admin(request)
    config = await get_db().rental_config.find_one({"type": "company"}) or {}

    secret = config.get("stripe_secret_key", "")
    pub = config.get("stripe_publishable_key", "")

    return {
        "success": True,
        "stripe_enabled": config.get("stripe_enabled", False),
        "stripe_secret_key_masked": f"sk_...{secret[-8:]}" if len(secret) > 8 else ("Configurado" if secret else ""),
        "stripe_publishable_key": pub,
        "has_secret_key": bool(secret),
        "has_publishable_key": bool(pub),
        "payment_methods": config.get("payment_methods", {}),
    }





@router.get('/admin/stripe/test-connection')
async def admin_test_stripe_connection(request: Request):
    """Admin: Test if Stripe keys are valid"""
    await auth_admin(request)
    try:
        config = await _get_stripe_config()
        secret_key = config.get("stripe_secret_key", "")
        if not secret_key:
            return {"success": False, "error": "No hay clave secreta configurada"}
        
        import stripe
        stripe.api_key = secret_key
        account = stripe.Account.retrieve()
        return {
            "success": True,
            "account_id": account.id,
            "business_name": getattr(account, 'business_profile', {}).get('name', ''),
            "charges_enabled": account.charges_enabled,
            "payouts_enabled": account.payouts_enabled,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}



# ═══════════════════════════════════════════════════════════════
#  Stripe Connect Webhook
# ═══════════════════════════════════════════════════════════════

@router.post('/stripe/connect-webhook')
async def stripe_connect_webhook(request: Request):
    """
    Stripe Connect Webhook endpoint.
    Handles account.updated events to auto-update owner onboarding status.
    Also handles payment-related events for automatic tracking.
    """
    import os
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature', '')
    
    # Get webhook secret from env or DB config
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    if not webhook_secret:
        config = await _get_stripe_config()
        webhook_secret = config.get('stripe_webhook_secret', '')

    if not webhook_secret:
        logging.warning("⚠️ Stripe webhook secret not configured, processing without verification")
        import json
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
        logging.info(f"💳 Payment succeeded: ${amount:.2f} (PI: {pi_id})")

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



# ═══════════════════════════════════════════════════════════════════════════════
# TENANT PAYMENT METHODS (Save Cards / Bank Accounts + Auto-Pay)
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_or_create_stripe_customer(user: dict) -> str:
    """Get or create a Stripe Customer for a marketplace user."""
    import stripe as stripe_lib
    db = get_db()
    config = await _get_stripe_config()
    sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_API_KEY", "")
    if not sk:
        raise HTTPException(status_code=500, detail="Stripe no configurado")
    stripe_lib.api_key = sk

    customer_id = user.get("stripe_customer_id", "")
    if customer_id:
        return customer_id

    # Create Stripe customer
    customer = stripe_lib.Customer.create(
        email=user.get("email", ""),
        name=user.get("name", ""),
        phone=user.get("phone", ""),
        metadata={"user_id": str(user.get("_id", "")), "role": user.get("role", "tenant")},
    )
    await db.app_users.update_one(
        {"_id": ObjectId(user["_id"]) if not isinstance(user["_id"], ObjectId) else user["_id"]},
        {"$set": {"stripe_customer_id": customer.id, "updated_at": datetime.utcnow()}}
    )
    return customer.id


@router.post('/tenant/payment-methods/setup')
async def tenant_setup_payment_method(request: Request):
    """Create a SetupIntent so the tenant can save a card or bank account."""
    user = await auth_marketplace(request)
    if user.get("role") not in ("tenant", "landlord", "buyer", "admin"):
        raise HTTPException(status_code=403, detail="No autorizado")

    import stripe as stripe_lib
    config = await _get_stripe_config()
    sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_API_KEY", "")
    if not sk:
        raise HTTPException(status_code=500, detail="Stripe no configurado en el sistema")
    stripe_lib.api_key = sk

    customer_id = await _get_or_create_stripe_customer(user)

    setup_intent = stripe_lib.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
        metadata={"user_id": str(user["_id"])},
    )

    return {
        "success": True,
        "client_secret": setup_intent.client_secret,
        "setup_intent_id": setup_intent.id,
        "customer_id": customer_id,
    }


@router.get('/tenant/payment-methods')
async def tenant_list_payment_methods(request: Request):
    """List saved payment methods for the current user."""
    user = await auth_marketplace(request)

    import stripe as stripe_lib
    config = await _get_stripe_config()
    sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_API_KEY", "")
    if not sk:
        return {"success": True, "payment_methods": [], "autopay": None}
    stripe_lib.api_key = sk

    customer_id = user.get("stripe_customer_id", "")
    if not customer_id:
        # Check autopay config even without Stripe customer
        autopay = await get_db().autopay_config.find_one({"user_id": str(user["_id"])})
        return {
            "success": True,
            "payment_methods": [],
            "autopay": {
                "enabled": autopay.get("enabled", False) if autopay else False,
                "payment_method_id": autopay.get("payment_method_id", "") if autopay else "",
                "day_of_month": autopay.get("day_of_month", 1) if autopay else 1,
            } if autopay else None,
        }

    # Fetch cards from Stripe
    methods = []
    try:
        cards = stripe_lib.PaymentMethod.list(customer=customer_id, type="card")
        for pm in cards.data:
            methods.append({
                "id": pm.id,
                "type": "card",
                "brand": pm.card.brand,
                "last4": pm.card.last4,
                "exp_month": pm.card.exp_month,
                "exp_year": pm.card.exp_year,
                "is_default": pm.id == user.get("default_payment_method", ""),
            })
    except Exception as e:
        logging.error(f"Error fetching Stripe payment methods: {e}")

    # Get autopay config
    autopay = await get_db().autopay_config.find_one({"user_id": str(user["_id"])})

    return {
        "success": True,
        "payment_methods": methods,
        "autopay": {
            "enabled": autopay.get("enabled", False) if autopay else False,
            "payment_method_id": autopay.get("payment_method_id", "") if autopay else "",
            "day_of_month": autopay.get("day_of_month", 1) if autopay else 1,
        } if autopay else None,
    }


@router.delete('/tenant/payment-methods/{pm_id}')
async def tenant_delete_payment_method(request: Request, pm_id: str):
    """Remove a saved payment method."""
    user = await auth_marketplace(request)

    import stripe as stripe_lib
    config = await _get_stripe_config()
    sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_API_KEY", "")
    if not sk:
        raise HTTPException(status_code=500, detail="Stripe no configurado")
    stripe_lib.api_key = sk

    try:
        stripe_lib.PaymentMethod.detach(pm_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error eliminando método: {str(e)}")

    # If this was the autopay method, disable autopay
    autopay = await get_db().autopay_config.find_one({"user_id": str(user["_id"])})
    if autopay and autopay.get("payment_method_id") == pm_id:
        await get_db().autopay_config.update_one(
            {"user_id": str(user["_id"])},
            {"$set": {"enabled": False, "payment_method_id": "", "updated_at": datetime.utcnow()}}
        )

    return {"success": True, "message": "Método de pago eliminado"}


@router.post('/tenant/autopay/configure')
async def tenant_configure_autopay(request: Request):
    """Enable or disable autopay for rent."""
    user = await auth_marketplace(request)
    data = await request.json()

    enabled = data.get("enabled", False)
    payment_method_id = data.get("payment_method_id", "")
    day_of_month = int(data.get("day_of_month", 1))

    if day_of_month < 1 or day_of_month > 28:
        raise HTTPException(status_code=400, detail="El día debe ser entre 1 y 28")

    if enabled and not payment_method_id:
        raise HTTPException(status_code=400, detail="Selecciona un método de pago para autopago")

    now = datetime.utcnow()
    await get_db().autopay_config.update_one(
        {"user_id": str(user["_id"])},
        {"$set": {
            "user_id": str(user["_id"]),
            "user_name": user.get("name", ""),
            "user_email": user.get("email", ""),
            "enabled": enabled,
            "payment_method_id": payment_method_id,
            "day_of_month": day_of_month,
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    status = "activado" if enabled else "desactivado"
    return {"success": True, "message": f"Autopago {status} exitosamente"}


@router.get('/tenant/autopay/status')
async def tenant_autopay_status(request: Request):
    """Get current autopay configuration."""
    user = await auth_marketplace(request)
    autopay = await get_db().autopay_config.find_one({"user_id": str(user["_id"])})

    if not autopay:
        return {"success": True, "autopay": {"enabled": False, "payment_method_id": "", "day_of_month": 1}}

    return {
        "success": True,
        "autopay": {
            "enabled": autopay.get("enabled", False),
            "payment_method_id": autopay.get("payment_method_id", ""),
            "day_of_month": autopay.get("day_of_month", 1),
        }
    }
