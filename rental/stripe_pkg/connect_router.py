"""Stripe Connect endpoints — admin configuration, owner onboarding, payouts."""
import logging
from datetime import datetime
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin, auth_marketplace, serialize
from rental.stripe_pkg.helpers import _get_stripe_config

router = APIRouter()


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
                "message": "Enlace de onboarding regenerado",
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
            "message": "Cuenta de Stripe creada. Completa la verificación.",
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
            "message": "No has conectado tu cuenta de Stripe aún",
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
    except Exception:
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
            "message": f"Pago de ${net_amount:.2f} enviado exitosamente (comisión: ${commission:.2f})",
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
