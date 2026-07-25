"""Vault Cards — PCI-compliant tokenized card storage using Stripe SetupIntent
+ Stripe Elements (client-side tokenization).

Design principles:
  * The raw PAN and CVV are entered on the CLIENT and sent DIRECTLY to Stripe
    via Stripe.js / Elements. They NEVER touch this server, RAM or MongoDB.
  * This backend only ever handles Stripe TOKENS (customer id `cus_...`,
    payment method id `pm_...`, setup/payment intent client secrets).
  * The Vault (`payment_methods`) persists ONLY token references + display
    metadata (brand, last4, exp) — never sensitive card data.

Flows:
  A) Online checkout / self-service: tenant opens the app → SetupIntent →
     Elements → card saved (tokenized) to their Stripe customer + vault.
  B) Manual (admin): admin generates a one-time secure link → sends it to the
     client → client enters card on a hosted Elements page → token saved to the
     vault → admin later charges it off_session from the panel.

NMI note: the identical pattern applies to NMI's Collect.js + Customer Vault
(tokenize on the client, store only the NMI `customer_vault_id`/token here).
NMI is wired as an extension point in `_gateway()` but Stripe is the active
gateway since it is the one configured with live keys.
"""
import os
import secrets
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException
from bson import ObjectId

from .shared import get_db, auth_admin, serialize

router = APIRouter()
logger = logging.getLogger(__name__)

CARD_SAVE_TOKEN_TTL_HOURS = 72


async def _stripe():
    """Return the Stripe module configured with the secret key."""
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    sk = (
        config.get("stripe_secret_key")
        or os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("STRIPE_API_KEY", "")
    )
    if not sk:
        raise HTTPException(status_code=500, detail="Stripe no está configurado")
    import stripe as stripe_lib
    stripe_lib.api_key = sk
    return stripe_lib


async def _publishable_key() -> str:
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    return (
        config.get("stripe_publishable_key")
        or os.environ.get("STRIPE_PUBLISHABLE_KEY")
        or os.environ.get("STRIPE_PUBLIC_KEY", "")
    )


async def _get_or_create_customer(stripe_lib, db, *, email: str, name: str, tenant_id: str) -> str:
    """Find or create a Stripe customer, reusing any id stored on the tenant."""
    if tenant_id:
        try:
            t = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
            if t and t.get("stripe_customer_id"):
                return t["stripe_customer_id"]
        except Exception:
            t = None
    cust = stripe_lib.Customer.create(
        email=email or None,
        name=name or None,
        metadata={"tenant_id": tenant_id, "source": "vault"},
    )
    if tenant_id:
        try:
            await db.tenants.update_one(
                {"_id": ObjectId(tenant_id)}, {"$set": {"stripe_customer_id": cust.id}}
            )
        except Exception:
            pass
    return cust.id


async def _save_pm_reference(db, stripe_lib, *, pm_id: str, customer_id: str,
                             tenant_id: str, tenant_name: str, email: str, source: str):
    """Persist ONLY the token reference + display metadata in the vault."""
    pm = stripe_lib.PaymentMethod.retrieve(pm_id)
    card = getattr(pm, "card", None)
    existing = await db.payment_methods.find_one({"stripe_payment_method_id": pm_id})
    doc = {
        "type": "card",
        "user_id": tenant_id or "",
        "user_name": tenant_name or "",
        "user_email": email or "",
        "card_brand": (card.brand or "").title() if card else "",
        "card_last4": card.last4 if card else "",
        "card_exp": f"{card.exp_month:02d}/{str(card.exp_year)[-2:]}" if card else "",
        "stripe_payment_method_id": pm_id,
        "stripe_customer_id": customer_id,
        "is_default": False,
        "is_active_for_autopay": False,
        "source": source,
        "updated_at": datetime.now(timezone.utc),
    }
    if existing:
        await db.payment_methods.update_one({"_id": existing["_id"]}, {"$set": doc})
        return str(existing["_id"])
    doc["created_at"] = datetime.now(timezone.utc)
    res = await db.payment_methods.insert_one(doc)
    return str(res.inserted_id)


# ════════════════════════════════════════════════════════════════════════════
# Public config (safe — publishable key only)
# ════════════════════════════════════════════════════════════════════════════

@router.get("/vault/config")
async def vault_public_config():
    return {"publishable_key": await _publishable_key()}


# ════════════════════════════════════════════════════════════════════════════
# Flow A — self-service (authenticated tenant saves a card)
# ════════════════════════════════════════════════════════════════════════════

@router.post("/vault/setup-intent")
async def create_setup_intent(request: Request):
    """Create a SetupIntent so the client can tokenize a card via Elements.

    Body: {tenant_id?, customer_email?, customer_name?}
    Returns: {client_secret, customer_id, publishable_key}
    """
    data = await request.json()
    db = get_db()
    stripe_lib = await _stripe()

    tenant_id = (data.get("tenant_id") or "").strip()
    email = (data.get("customer_email") or "").strip()
    name = (data.get("customer_name") or "").strip()

    customer_id = await _get_or_create_customer(
        stripe_lib, db, email=email, name=name, tenant_id=tenant_id
    )
    si = stripe_lib.SetupIntent.create(
        customer=customer_id,
        usage="off_session",
        payment_method_types=["card"],
        metadata={"tenant_id": tenant_id, "source": "vault_setup_intent"},
    )
    return {
        "success": True,
        "client_secret": si.client_secret,
        "customer_id": customer_id,
        "publishable_key": await _publishable_key(),
    }


@router.post("/vault/payment-method/save")
async def save_payment_method(request: Request):
    """Called by the client AFTER Elements confirms the SetupIntent.
    Persists only the token reference in the vault.

    Body: {payment_method_id, customer_id, tenant_id?, tenant_name?, customer_email?}
    """
    data = await request.json()
    pm_id = (data.get("payment_method_id") or "").strip()
    customer_id = (data.get("customer_id") or "").strip()
    if not pm_id or not customer_id:
        raise HTTPException(status_code=400, detail="payment_method_id y customer_id requeridos")

    db = get_db()
    stripe_lib = await _stripe()
    saved_id = await _save_pm_reference(
        db, stripe_lib, pm_id=pm_id, customer_id=customer_id,
        tenant_id=(data.get("tenant_id") or "").strip(),
        tenant_name=(data.get("tenant_name") or "").strip(),
        email=(data.get("customer_email") or "").strip(),
        source=data.get("source") or "vault_self_service",
    )
    return {"success": True, "id": saved_id, "message": "Tarjeta guardada de forma segura (solo token)."}


# ════════════════════════════════════════════════════════════════════════════
# Flow B — manual: admin creates a secure "save card" link
# ════════════════════════════════════════════════════════════════════════════

@router.post("/admin/vault/card-save-link")
async def create_card_save_link(request: Request):
    """Admin generates a one-time secure link to send to a client so they can
    enter their card (tokenized) without the admin handling card data.

    Body: {tenant_id?, customer_email?, customer_name?}
    """
    admin = await auth_admin(request)
    data = await request.json()
    db = get_db()

    tenant_id = (data.get("tenant_id") or "").strip()
    email = (data.get("customer_email") or "").strip()
    name = (data.get("customer_name") or "").strip()
    if tenant_id and not name:
        try:
            t = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
            if t:
                name = (t.get("name") or f"{t.get('first_name','')} {t.get('last_name','')}").strip()
                email = email or t.get("email", "")
        except Exception:
            pass

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await db.card_save_tokens.insert_one({
        "token": token,
        "tenant_id": tenant_id or None,
        "customer_name": name or None,
        "customer_email": email or None,
        "status": "pending",
        "created_by": admin.get("email", ""),
        "created_at": now,
        "expires_at": now + timedelta(hours=CARD_SAVE_TOKEN_TTL_HOURS),
    })

    base = (
        os.environ.get("PUBLIC_WEB_URL")
        or os.environ.get("FRONTEND_URL")
        or "https://www.rosshouserentals.com"
    ).rstrip("/")
    return {
        "success": True,
        "url": f"{base}/save-card/{token}",
        "expires_in_hours": CARD_SAVE_TOKEN_TTL_HOURS,
        "message": "Envía este link al cliente para que registre su tarjeta de forma segura.",
    }


@router.get("/vault/card-save-link/{token}")
async def validate_card_save_link(token: str):
    """Public: the save-card page validates the token and shows who it's for."""
    db = get_db()
    rec = await db.card_save_tokens.find_one({"token": token})
    if not rec:
        raise HTTPException(status_code=404, detail="Link inválido")
    exp = rec.get("expires_at")
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if rec.get("status") == "used":
        raise HTTPException(status_code=410, detail="Este link ya fue utilizado")
    if exp and exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Este link expiró")
    return {
        "success": True,
        "customer_name": rec.get("customer_name"),
        "customer_email": rec.get("customer_email"),
    }


@router.post("/vault/card-save-link/{token}/setup-intent")
async def card_save_link_setup_intent(token: str):
    """Public: create a SetupIntent for the client behind a valid save-card link."""
    db = get_db()
    rec = await db.card_save_tokens.find_one({"token": token})
    if not rec or rec.get("status") == "used":
        raise HTTPException(status_code=404, detail="Link inválido o ya utilizado")
    exp = rec.get("expires_at")
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp and exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Este link expiró")

    stripe_lib = await _stripe()
    customer_id = await _get_or_create_customer(
        stripe_lib, db,
        email=rec.get("customer_email") or "",
        name=rec.get("customer_name") or "",
        tenant_id=rec.get("tenant_id") or "",
    )
    si = stripe_lib.SetupIntent.create(
        customer=customer_id, usage="off_session", payment_method_types=["card"],
        metadata={"token": token, "source": "vault_card_save_link"},
    )
    await db.card_save_tokens.update_one({"token": token}, {"$set": {"stripe_customer_id": customer_id}})
    return {
        "success": True,
        "client_secret": si.client_secret,
        "customer_id": customer_id,
        "publishable_key": await _publishable_key(),
    }


@router.post("/vault/card-save-link/{token}/complete")
async def card_save_link_complete(token: str, request: Request):
    """Public: after Elements confirms, persist the token reference + close the link."""
    data = await request.json()
    pm_id = (data.get("payment_method_id") or "").strip()
    customer_id = (data.get("customer_id") or "").strip()
    if not pm_id or not customer_id:
        raise HTTPException(status_code=400, detail="Datos incompletos")

    db = get_db()
    rec = await db.card_save_tokens.find_one({"token": token})
    if not rec or rec.get("status") == "used":
        raise HTTPException(status_code=404, detail="Link inválido o ya utilizado")

    stripe_lib = await _stripe()
    saved_id = await _save_pm_reference(
        db, stripe_lib, pm_id=pm_id, customer_id=customer_id,
        tenant_id=rec.get("tenant_id") or "",
        tenant_name=rec.get("customer_name") or "",
        email=rec.get("customer_email") or "",
        source="vault_card_save_link",
    )
    await db.card_save_tokens.update_one(
        {"token": token},
        {"$set": {"status": "used", "used_at": datetime.now(timezone.utc), "vault_id": saved_id}},
    )
    return {"success": True, "message": "¡Tarjeta registrada de forma segura!"}


# ════════════════════════════════════════════════════════════════════════════
# Charge a saved (tokenized) card off_session — admin only
# ════════════════════════════════════════════════════════════════════════════

@router.post("/admin/vault/charge")
async def charge_saved_method(request: Request):
    """Charge a previously-saved payment method off_session.

    Body: {payment_method_id, amount, description?}
    """
    admin = await auth_admin(request)
    data = await request.json()
    pm_id = (data.get("payment_method_id") or "").strip()
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    description = (data.get("description") or "").strip()

    if not pm_id:
        raise HTTPException(status_code=400, detail="payment_method_id requerido")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")

    db = get_db()
    pm_doc = await db.payment_methods.find_one({"stripe_payment_method_id": pm_id})
    if not pm_doc or not pm_doc.get("stripe_customer_id"):
        raise HTTPException(status_code=404, detail="Método de pago no encontrado en el baúl")

    stripe_lib = await _stripe()
    try:
        pi = stripe_lib.PaymentIntent.create(
            amount=int(round(amount * 100)),
            currency="usd",
            customer=pm_doc["stripe_customer_id"],
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            description=description or "Cobro Ross House Rentals",
            metadata={"charged_by": admin.get("email", ""), "source": "vault_manual_charge"},
        )
    except stripe_lib.error.CardError as e:
        raise HTTPException(status_code=402, detail=f"Tarjeta rechazada: {e.user_message or str(e)}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe error: {str(e)[:200]}")

    now = datetime.now(timezone.utc)
    await db.vault_charges.insert_one({
        "payment_method_id": pm_id,
        "stripe_payment_intent_id": pi.id,
        "amount": amount,
        "description": description,
        "status": pi.status,
        "user_email": pm_doc.get("user_email"),
        "charged_by": admin.get("email", ""),
        "created_at": now,
    })
    await db.vault_audit_log.insert_one({
        "admin_email": admin.get("email", ""), "action": "manual_charge",
        "target": pm_id, "meta": {"amount": amount, "status": pi.status}, "timestamp": now,
    })
    return {
        "success": pi.status == "succeeded",
        "status": pi.status,
        "payment_intent_id": pi.id,
        "amount": amount,
        "message": "Cobro exitoso" if pi.status == "succeeded" else f"Estado: {pi.status}",
    }


@router.get("/admin/vault/charges")
async def list_charges(request: Request, limit: int = 50):
    await auth_admin(request)
    db = get_db()
    items = []
    async for ch in db.vault_charges.find({}).sort("created_at", -1).limit(limit):
        items.append(serialize(ch))
    return {"success": True, "items": items}
