"""
Utility Billing - Tenant utility bills payable to Ross House Rentals
- Auto-generation from Green Button usage (kWh x rate) + manual bills/adjustments
- Tenant payment via Stripe (PaymentIntent, same pattern as rent)
- Per-property billing mode: landlord (pay in app), provider (pay at provider portal), mixed
Collections: tenant_utility_bills, utility_bill_payments, properties.utility_billing (config)
"""
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from bson import ObjectId

from .shared import get_db, auth_admin, auth_tenant, auth_tenant_flex

logger = logging.getLogger("utility_billing")
router = APIRouter()

VALID_MODES = ("landlord", "provider", "mixed")
VALID_TYPES = ("electricity", "gas", "water", "internet", "trash", "other")

TYPE_LABELS_ES = {
    "electricity": "luz", "gas": "gas", "water": "agua",
    "internet": "internet", "trash": "basura", "other": "servicios",
}


async def _notify_tenant_sms(db, tenant_id: str, bill: dict) -> bool:
    """Send an SMS (Twilio) to the tenant when a new bill is created. Best-effort."""
    if not tenant_id:
        return False
    try:
        tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
        phone = (tenant or {}).get("phone")
        if not phone:
            return False
        sid = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        from_phone = os.getenv("TWILIO_PHONE_NUMBER")
        if not (sid and token and from_phone):
            return False
        digits = "".join(filter(str.isdigit, phone))
        if not phone.startswith("+"):
            phone = f"+1{digits[-10:]}"
        label = TYPE_LABELS_ES.get(bill.get("type"), "servicios")
        body = (
            f"Ross House Rentals: tienes una nueva factura de {label} por "
            f"${bill.get('amount'):.2f} (periodo {bill.get('period')}). "
            f"Págala desde la app en Mis Servicios."
        )
        from twilio.rest import Client
        Client(sid, token).messages.create(body=body, from_=from_phone, to=phone)
        logger.info(f"📱 Utility bill SMS sent to tenant {tenant_id[-4:]}")
        return True
    except Exception as e:
        logger.warning(f"Utility bill SMS failed: {str(e)[:120]}")
        return False


def _bill_out(b: dict, prop_address: str = None) -> dict:
    return {
        "id": str(b["_id"]),
        "property_id": b.get("property_id"),
        "property_address": prop_address,
        "tenant_id": b.get("tenant_id"),
        "type": b.get("type"),
        "period": b.get("period"),
        "kwh": b.get("kwh"),
        "rate_per_kwh": b.get("rate_per_kwh"),
        "amount": b.get("amount"),
        "status": b.get("status"),
        "source": b.get("source"),
        "add_to_rent": b.get("add_to_rent", False),
        "notes": b.get("notes"),
        "paid_at": b.get("paid_at").isoformat() if b.get("paid_at") else None,
        "created_at": b.get("created_at").isoformat() if b.get("created_at") else None,
    }


async def _find_property(db, property_id: str):
    try:
        return await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        return await db.properties.find_one({"_id": property_id})


async def _active_tenant_for_property(db, property_id: str):
    contract = await db.rental_contracts.find_one({"property_id": str(property_id), "status": "active"})
    return str(contract["tenant_id"]) if contract and contract.get("tenant_id") else None


# ═══════════════════════════════════════════════════════════════
# ADMIN — property billing config
# ═══════════════════════════════════════════════════════════════

@router.put("/admin/properties/{property_id}/utility-config")
async def set_utility_config(request: Request, property_id: str):
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    mode = data.get("billing_mode", "landlord")
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"billing_mode debe ser uno de {VALID_MODES}")
    prop = await _find_property(db, property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    config = {
        "billing_mode": mode,
        "electricity_rate_per_kwh": float(data.get("electricity_rate_per_kwh", 0) or 0),
        "base_fee": float(data.get("base_fee", 0) or 0),
        "provider_payment_url": data.get("provider_payment_url", "https://www.xcelenergy.com/billing_and_payment"),
        "updated_at": datetime.now(timezone.utc),
    }
    await db.properties.update_one({"_id": prop["_id"]}, {"$set": {"utility_billing": config}})
    return {"success": True, "utility_billing": {**config, "updated_at": config["updated_at"].isoformat()}}


# ═══════════════════════════════════════════════════════════════
# ADMIN — bills
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/utility-bills/generate")
async def generate_bill_from_greenbutton(request: Request):
    """Generate the monthly electricity bill from Green Button usage (kWh x rate)."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    property_id = data.get("property_id")
    period = data.get("period")  # 'YYYY-MM'
    if not property_id or not period or len(period) != 7:
        raise HTTPException(status_code=400, detail="property_id y period (YYYY-MM) son requeridos")

    prop = await _find_property(db, property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    cfg = prop.get("utility_billing", {})

    rate = float(data.get("rate_per_kwh") or cfg.get("electricity_rate_per_kwh") or 0)
    base_fee = float(data.get("base_fee") if data.get("base_fee") is not None else cfg.get("base_fee", 0) or 0)
    if rate <= 0:
        raise HTTPException(status_code=400, detail="Configura la tarifa $/kWh de la propiedad o envía rate_per_kwh")

    # Sum Green Button daily usage for the month
    kwh = 0.0
    async for doc in db.xcel_usage_daily.find({"property_id": str(property_id), "date": {"$regex": f"^{period}"}}):
        kwh += doc.get("kwh", 0.0)
    if kwh <= 0:
        raise HTTPException(status_code=400, detail=f"Sin datos de consumo Green Button para {period}. Sincroniza primero en Energía.")

    amount = round(kwh * rate + base_fee, 2)
    tenant_id = data.get("tenant_id") or await _active_tenant_for_property(db, property_id)
    now = datetime.now(timezone.utc)

    existing = await db.tenant_utility_bills.find_one({
        "property_id": str(property_id), "period": period, "type": "electricity",
    })
    if existing and existing.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Ya existe una factura PAGADA para ese periodo")

    doc = {
        "property_id": str(property_id),
        "tenant_id": tenant_id,
        "type": "electricity",
        "period": period,
        "kwh": round(kwh, 2),
        "rate_per_kwh": rate,
        "base_fee": base_fee,
        "amount": amount,
        "status": "pending",
        "source": "greenbutton",
        "add_to_rent": bool(data.get("add_to_rent", False)),
        "notes": data.get("notes", ""),
        "updated_at": now,
    }
    if existing:
        await db.tenant_utility_bills.update_one({"_id": existing["_id"]}, {"$set": doc})
        bill_id = existing["_id"]
    else:
        doc["created_at"] = now
        res = await db.tenant_utility_bills.insert_one(doc)
        bill_id = res.inserted_id

    sms_sent = await _notify_tenant_sms(db, tenant_id, doc)
    return {"success": True, "bill": _bill_out({**doc, "_id": bill_id, "created_at": doc.get("created_at", now)}),
            "tenant_assigned": bool(tenant_id), "sms_sent": sms_sent}


@router.post("/admin/utility-bills")
async def create_manual_bill(request: Request):
    """Create or adjust a manual utility bill for a tenant."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    property_id = data.get("property_id")
    amount = float(data.get("amount", 0) or 0)
    btype = data.get("type", "electricity")
    period = data.get("period")
    if not property_id or amount <= 0 or not period:
        raise HTTPException(status_code=400, detail="property_id, period y amount > 0 son requeridos")
    if btype not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"type debe ser uno de {VALID_TYPES}")

    tenant_id = data.get("tenant_id") or await _active_tenant_for_property(db, property_id)
    now = datetime.now(timezone.utc)
    doc = {
        "property_id": str(property_id),
        "tenant_id": tenant_id,
        "type": btype,
        "period": period,
        "kwh": data.get("kwh"),
        "rate_per_kwh": data.get("rate_per_kwh"),
        "amount": round(amount, 2),
        "status": "pending",
        "source": "manual",
        "add_to_rent": bool(data.get("add_to_rent", False)),
        "notes": data.get("notes", ""),
        "created_at": now,
        "updated_at": now,
    }
    res = await db.tenant_utility_bills.insert_one(doc)
    sms_sent = await _notify_tenant_sms(db, tenant_id, doc)
    return {"success": True, "bill": _bill_out({**doc, "_id": res.inserted_id}), "sms_sent": sms_sent}


@router.get("/admin/utility-bills")
async def admin_list_bills(request: Request, property_id: str = None, status: str = None):
    await auth_admin(request)
    db = get_db()
    q = {}
    if property_id:
        q["property_id"] = property_id
    if status:
        q["status"] = status
    bills = []
    async for b in db.tenant_utility_bills.find(q).sort("created_at", -1).limit(200):
        prop = await _find_property(db, b["property_id"])
        bills.append(_bill_out(b, prop.get("address") if prop else None))
    return {"bills": bills}


@router.put("/admin/utility-bills/{bill_id}")
async def admin_update_bill(request: Request, bill_id: str):
    """Adjust amount / status / add_to_rent / notes (manual adjustment)."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    update = {"updated_at": datetime.now(timezone.utc)}
    if "amount" in data:
        update["amount"] = round(float(data["amount"]), 2)
    if "status" in data:
        if data["status"] not in ("pending", "paid", "cancelled"):
            raise HTTPException(status_code=400, detail="status inválido")
        update["status"] = data["status"]
        if data["status"] == "paid":
            update["paid_at"] = datetime.now(timezone.utc)
            update["payment_method"] = data.get("payment_method", "manual")
    if "add_to_rent" in data:
        update["add_to_rent"] = bool(data["add_to_rent"])
    if "notes" in data:
        update["notes"] = data["notes"]
    res = await db.tenant_utility_bills.update_one({"_id": ObjectId(bill_id)}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return {"success": True}


@router.delete("/admin/utility-bills/{bill_id}")
async def admin_delete_bill(request: Request, bill_id: str):
    await auth_admin(request)
    db = get_db()
    res = await db.tenant_utility_bills.delete_one({"_id": ObjectId(bill_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return {"success": True}


# ═══════════════════════════════════════════════════════════════
# TENANT — view & pay bills
# ═══════════════════════════════════════════════════════════════

@router.get("/tenant/utility-bills")
async def tenant_list_bills(request: Request):
    tenant = await auth_tenant_flex(request)
    db = get_db()
    tenant_id = str(tenant["_id"])
    bills = []
    billing_mode = "landlord"
    provider_url = None
    async for b in db.tenant_utility_bills.find({"tenant_id": tenant_id}).sort("created_at", -1).limit(50):
        bills.append(_bill_out(b))
    # Billing config from the tenant's active contract property
    contract = await db.rental_contracts.find_one({"tenant_id": tenant_id, "status": "active"})
    xcel_connected = False
    if contract and contract.get("property_id"):
        prop = await _find_property(db, contract["property_id"])
        if prop and prop.get("utility_billing"):
            billing_mode = prop["utility_billing"].get("billing_mode", "landlord")
            provider_url = prop["utility_billing"].get("provider_payment_url")
        xcel_connected = bool(await db.xcel_connections.find_one(
            {"property_id": str(contract["property_id"]), "status": "active"}
        ))
    pending = [b for b in bills if b["status"] == "pending"]
    return {
        "bills": bills,
        "pending_total": round(sum(b["amount"] for b in pending), 2),
        "pending_count": len(pending),
        "billing_mode": billing_mode,
        "provider_payment_url": provider_url,
        "xcel_connected": xcel_connected,
    }


@router.post("/tenant/utility-bills/{bill_id}/create-payment")
async def tenant_create_bill_payment(request: Request, bill_id: str):
    """Create a Stripe PaymentIntent to pay a utility bill in the app."""
    tenant = await auth_tenant_flex(request)
    db = get_db()
    bill = await db.tenant_utility_bills.find_one({"_id": ObjectId(bill_id), "tenant_id": str(tenant["_id"])})
    if not bill:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    if bill.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Esta factura no está pendiente de pago")

    config = await db.rental_config.find_one({"type": "company"}) or {}
    stripe_secret = config.get("stripe_secret_key", "")
    if not config.get("stripe_enabled") or not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no está configurado. Contacta al administrador.")

    try:
        import stripe
        stripe.api_key = stripe_secret
        intent = stripe.PaymentIntent.create(
            amount=int(round(bill["amount"] * 100)),
            currency="usd",
            metadata={
                "kind": "utility_bill",
                "bill_id": str(bill["_id"]),
                "tenant_id": str(tenant["_id"]),
                "property_id": bill.get("property_id", ""),
                "period": bill.get("period", ""),
                "bill_type": bill.get("type", ""),
            },
            description=f"Servicios {bill.get('type')} {bill.get('period')} - {tenant.get('name', '')}",
            receipt_email=tenant.get("email"),
        )
        return {
            "success": True,
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
            "amount": bill["amount"],
            "publishable_key": config.get("stripe_publishable_key", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stripe utility bill intent error: {e}")
        raise HTTPException(status_code=500, detail=f"Error de Stripe: {str(e)}")


@router.post("/tenant/utility-bills/{bill_id}/confirm-payment")
async def tenant_confirm_bill_payment(request: Request, bill_id: str):
    """Verify the PaymentIntent succeeded and mark the bill as paid."""
    tenant = await auth_tenant_flex(request)
    db = get_db()
    data = await request.json()
    payment_intent_id = (data.get("payment_intent_id") or "").strip()
    if not payment_intent_id:
        raise HTTPException(status_code=400, detail="payment_intent_id requerido")

    bill = await db.tenant_utility_bills.find_one({"_id": ObjectId(bill_id), "tenant_id": str(tenant["_id"])})
    if not bill:
        raise HTTPException(status_code=404, detail="Factura no encontrada")

    existing = await db.utility_bill_payments.find_one({"stripe_payment_intent": payment_intent_id})
    if existing:
        return {"success": True, "message": "Pago ya registrado"}

    config = await db.rental_config.find_one({"type": "company"}) or {}
    stripe_secret = config.get("stripe_secret_key", "")
    if not stripe_secret:
        raise HTTPException(status_code=400, detail="Stripe no configurado")

    try:
        import stripe
        stripe.api_key = stripe_secret
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error verificando con Stripe: {str(e)}")

    if intent.status != "succeeded":
        raise HTTPException(status_code=400, detail=f"Pago no completado. Estado: {intent.status}")
    if intent.metadata.get("bill_id") != str(bill["_id"]):
        raise HTTPException(status_code=400, detail="El pago no corresponde a esta factura")

    now = datetime.now(timezone.utc)
    await db.tenant_utility_bills.update_one(
        {"_id": bill["_id"]},
        {"$set": {"status": "paid", "paid_at": now, "payment_method": "stripe",
                  "stripe_payment_intent": payment_intent_id, "updated_at": now}},
    )
    await db.utility_bill_payments.insert_one({
        "bill_id": str(bill["_id"]),
        "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name", ""),
        "property_id": bill.get("property_id"),
        "type": bill.get("type"),
        "period": bill.get("period"),
        "amount": intent.amount / 100,
        "stripe_payment_intent": payment_intent_id,
        "receipt_number": f"UTL-{now.strftime('%Y%m%d')}-{str(tenant['_id'])[-4:]}",
        "paid_at": now,
        "created_at": now,
    })
    return {"success": True, "message": "Factura pagada exitosamente"}
