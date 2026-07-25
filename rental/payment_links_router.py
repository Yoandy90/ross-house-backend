"""Admin Payment Links — generate shareable Stripe payment links for one-off
charges (fines, repairs, deposits, vendor references, any amount).

Flow (per business rules):
  1. Admin creates a link from the panel choosing amount + reference.
  2. The link is a hosted Stripe Payment Link — the customer enters their card
     on Stripe's PCI-compliant page (we never touch the raw PAN/CVV).
  3. On completion, the webhook saves the payment-method REFERENCE (brand +
     last4 + stripe_payment_method_id) into the Vault (`payment_methods`) as the
     record of truth, and records the payment.

Note: full card number / CVV are intentionally NOT stored — storing CVV is
prohibited by PCI-DSS and Stripe's terms. Full bank (ACH) numbers ARE stored
encrypted via the separate /tenant/bank-accounts/add flow.
"""
import os
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from bson import ObjectId

from .shared import get_db, auth_admin, serialize

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_stripe_sk() -> str:
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    sk = (
        config.get("stripe_secret_key")
        or os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("STRIPE_API_KEY")
        or os.environ.get("STRIPE_SK")
        or os.environ.get("STRIPE_KEY", "")
    )
    if not sk:
        raise HTTPException(status_code=500, detail="Stripe no está configurado")
    return sk


@router.post("/admin/vault/payment-links")
async def create_payment_link(request: Request):
    """Create a shareable Stripe Payment Link for a custom amount.

    Body:
      amount: float (USD) — chosen by admin (required)
      reference: str — appears as the product name on the checkout (required)
      description: str (optional)
      tenant_id: str (optional) — link the payment to a tenant
      customer_email: str (optional) — prefill the email
    """
    admin = await auth_admin(request)
    data = await request.json()

    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    reference = (data.get("reference") or "").strip()
    description = (data.get("description") or "").strip()
    tenant_id = (data.get("tenant_id") or "").strip()
    customer_email = (data.get("customer_email") or "").strip()

    if amount <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a 0")
    if not reference:
        raise HTTPException(status_code=400, detail="La referencia es requerida")

    import stripe as stripe_lib
    stripe_lib.api_key = await _get_stripe_sk()
    db = get_db()

    tenant_name = ""
    if tenant_id:
        try:
            t = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
            if t:
                tenant_name = (t.get("name") or f"{t.get('first_name','')} {t.get('last_name','')}").strip()
                if not customer_email:
                    customer_email = t.get("email", "")
        except Exception:
            pass

    amount_cents = int(round(amount * 100))
    metadata = {
        "reference": reference[:200],
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "created_by": admin.get("email", ""),
        "source": "vault_payment_link",
    }

    try:
        product = stripe_lib.Product.create(name=reference[:250], metadata=metadata)
        price = stripe_lib.Price.create(
            product=product.id, unit_amount=amount_cents, currency="usd"
        )
        link = stripe_lib.PaymentLink.create(
            line_items=[{"price": price.id, "quantity": 1}],
            metadata=metadata,
            # Save the card to a customer so we can record its reference in the vault
            customer_creation="always",
            payment_intent_data={"setup_future_usage": "off_session", "metadata": metadata},
            allow_promotion_codes=False,
        )
    except Exception as e:
        logger.exception("Stripe payment link creation failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {str(e)[:200]}")

    doc = {
        "reference": reference,
        "description": description,
        "amount": amount,
        "currency": "usd",
        "tenant_id": tenant_id or None,
        "tenant_name": tenant_name or None,
        "customer_email": customer_email or None,
        "stripe_payment_link_id": link.id,
        "stripe_product_id": product.id,
        "stripe_price_id": price.id,
        "url": link.url,
        "status": "active",           # active | paid
        "paid_at": None,
        "created_by": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc),
    }
    res = await db.payment_links.insert_one(doc)

    return {
        "success": True,
        "id": str(res.inserted_id),
        "url": link.url,
        "reference": reference,
        "amount": amount,
        "message": "Link de pago creado. Cópialo y envíalo al cliente.",
    }


@router.get("/admin/vault/payment-links")
async def list_payment_links(request: Request, limit: int = 50):
    """List recent payment links with their status."""
    await auth_admin(request)
    db = get_db()
    items = []
    async for pl in db.payment_links.find({}).sort("created_at", -1).limit(limit):
        items.append(serialize(pl))
    return {"success": True, "items": items, "count": len(items)}


@router.delete("/admin/vault/payment-links/{link_id}")
async def deactivate_payment_link(link_id: str, request: Request):
    """Deactivate a payment link (so it can no longer be paid)."""
    await auth_admin(request)
    db = get_db()
    pl = await db.payment_links.find_one({"_id": ObjectId(link_id)})
    if not pl:
        raise HTTPException(status_code=404, detail="Link no encontrado")

    import stripe as stripe_lib
    stripe_lib.api_key = await _get_stripe_sk()
    try:
        stripe_lib.PaymentLink.modify(pl["stripe_payment_link_id"], active=False)
    except Exception as e:
        logger.warning(f"Could not deactivate link in Stripe: {e}")

    await db.payment_links.update_one(
        {"_id": pl["_id"]}, {"$set": {"status": "inactive", "updated_at": datetime.now(timezone.utc)}}
    )
    return {"success": True, "message": "Link desactivado"}
