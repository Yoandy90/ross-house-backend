"""Shared helpers used by multiple Stripe sub-routers."""
import os
import logging
from datetime import datetime
from bson import ObjectId
from fastapi import HTTPException

from rental.shared import get_db


async def _get_stripe_config() -> dict:
    """Helper: Get Stripe / company configuration from rental_config."""
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    return config


async def _get_or_create_stripe_customer(user: dict) -> str:
    """Get or create a Stripe Customer for a marketplace user.

    Stores the resolved `stripe_customer_id` on the app_users doc so future
    lookups are O(1) and saved cards persist across sessions.
    """
    import stripe as stripe_lib
    db = get_db()
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

    customer_id = user.get("stripe_customer_id", "")

    # Verify existing customer is still valid in Stripe
    if customer_id:
        try:
            stripe_lib.Customer.retrieve(customer_id)
            return customer_id
        except Exception:
            # Customer doesn't exist in Stripe anymore — create a new one
            pass

    # Create new Stripe customer
    customer = stripe_lib.Customer.create(
        email=user.get("email", ""),
        name=user.get("name", f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()),
        phone=user.get("phone", ""),
        metadata={"user_id": str(user.get("_id", "")), "role": user.get("role", "tenant")},
    )
    await db.app_users.update_one(
        {"_id": ObjectId(user["_id"]) if not isinstance(user["_id"], ObjectId) else user["_id"]},
        {"$set": {"stripe_customer_id": customer.id, "updated_at": datetime.utcnow()}}
    )
    return customer.id
