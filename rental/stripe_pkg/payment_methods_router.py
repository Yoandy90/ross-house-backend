"""Tenant saved payment methods endpoints (setup / list / delete)."""
import os
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_marketplace
from rental.stripe_pkg.helpers import _get_stripe_config, _get_or_create_stripe_customer

router = APIRouter()


@router.post('/tenant/payment-methods/setup')
async def tenant_setup_payment_method(request: Request):
    """Create a SetupIntent so the tenant can save a card or bank account."""
    import stripe as stripe_lib

    try:
        user = await auth_marketplace(request)
        if user.get("role") not in ("tenant", "landlord", "buyer", "admin"):
            raise HTTPException(status_code=403, detail="No autorizado")

        config = await _get_stripe_config()
        sk = (
            config.get("stripe_secret_key")
            or os.environ.get("STRIPE_SECRET_KEY")
            or os.environ.get("STRIPE_API_KEY")
            or os.environ.get("STRIPE_SK")
            or os.environ.get("STRIPE_KEY", "")
        )

        if not sk:
            raise HTTPException(status_code=500, detail="Stripe no configurado en el sistema")

        # Validate key format
        sk = sk.strip()
        if not sk.startswith("sk_"):
            raise HTTPException(status_code=500, detail="Clave de Stripe inválida (debe empezar con sk_)")

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
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "Invalid API Key" in error_msg or "authentication" in error_msg.lower():
            raise HTTPException(status_code=500, detail=f"Error de autenticación con Stripe: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Error de Stripe: {error_msg}")


@router.get('/tenant/payment-methods')
async def tenant_list_payment_methods(request: Request):
    """List saved payment methods for the current user."""
    user = await auth_marketplace(request)

    import stripe as stripe_lib
    config = await _get_stripe_config()
    sk = (
        config.get("stripe_secret_key")
        or os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("STRIPE_API_KEY")
        or os.environ.get("STRIPE_SK")
        or os.environ.get("STRIPE_KEY", "")
    )
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
    sk = (
        config.get("stripe_secret_key")
        or os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("STRIPE_API_KEY")
        or os.environ.get("STRIPE_SK")
        or os.environ.get("STRIPE_KEY", "")
    )
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
