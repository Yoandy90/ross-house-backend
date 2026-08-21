"""Helcim Vault — métodos de pago guardados, cobro 1-tap y autopago.

Flujo: el inquilino verifica su tarjeta UNA vez en el modal seguro (paymentType
'verify') → Helcim devuelve cardToken → guardamos solo el token. Después todo
es nativo: cobro server-side con /v2/payment/purchase usando el token.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from .shared import get_db, auth_tenant_flex

logger = logging.getLogger("helcim_vault")
router = APIRouter(tags=["helcim-vault"])
HELCIM_BASE = "https://api.helcim.com/v2"


async def _helcim_cfg() -> dict:
    from .payment_processors_router import _get_doc, _active_creds
    doc = await _get_doc()
    return _active_creds(doc["processors"].get("helcim", {}))


async def helcim_purchase_with_token(api_token: str, amount_cents: int,
                                     card_token: str, customer_code: str = "",
                                     ip: str = "127.0.0.1") -> dict:
    """Cobro server-side con token guardado. Devuelve dict de la transacción."""
    body = {"ipAddress": ip, "amount": round(amount_cents / 100, 2),
            "currency": "USD", "cardData": {"cardToken": card_token}}
    if customer_code:
        body["customerCode"] = customer_code
    async with httpx.AsyncClient(timeout=30) as x:
        r = await x.post(f"{HELCIM_BASE}/payment/purchase",
                         headers={"api-token": api_token, "accept": "application/json",
                                  "idempotency-key": uuid.uuid4().hex[:25]},
                         json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=402, detail=f"Helcim rechazó el cobro: {r.text[:180]}")
    return r.json()


@router.post("/tenant/helcim/save-method-session")
async def save_method_session(request: Request):
    """Crea sesión 'verify' de HelcimPay para guardar tarjeta (sin cobrar)."""
    tenant = await auth_tenant_flex(request)
    cfg = await _helcim_cfg()
    if not cfg.get("api_token"):
        raise HTTPException(400, "Helcim no configurado")
    async with httpx.AsyncClient(timeout=20) as x:
        r = await x.post(f"{HELCIM_BASE}/helcim-pay/initialize",
                         headers={"api-token": cfg["api_token"], "accept": "application/json"},
                         json={"paymentType": "verify", "amount": 0, "currency": "USD",
                               "paymentMethod": "cc"})
    if r.status_code >= 400:
        raise HTTPException(502, f"Helcim: {r.text[:150]}")
    data = r.json()
    sid = uuid.uuid4().hex
    from .payment_processors_router import _public_base_url
    await get_db().helcim_checkout_sessions.insert_one({
        "_id": sid, "checkout_token": data["checkoutToken"],
        "secret_token": data["secretToken"], "amount_cents": 0,
        "purpose": "verify", "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name", ""), "status": "pending",
        "created_at": datetime.now(timezone.utc)})
    return {"success": True,
            "url": f"{_public_base_url()}/api/public/helcim-checkout/{sid}"}


@router.get("/tenant/helcim/methods")
async def list_methods(request: Request):
    tenant = await auth_tenant_flex(request)
    items = []
    async for m in get_db().helcim_saved_methods.find({"tenant_id": str(tenant["_id"])}):
        items.append({"id": str(m["_id"]), "brand": m.get("brand", "Tarjeta"),
                      "last4": m.get("last4", ""), "type": m.get("type", "card"),
                      "created_at": m["created_at"].isoformat()})
    ap = await get_db().autopay_config.find_one({"user_id": str(tenant["_id"]),
                                                 "processor": "helcim"}) or {}
    return {"success": True, "methods": items,
            "autopay": {"enabled": bool(ap.get("enabled")),
                        "method_id": str(ap.get("helcim_method_id", "")),
                        "day_of_month": ap.get("day_of_month", 1)}}


@router.delete("/tenant/helcim/methods/{mid}")
async def delete_method(mid: str, request: Request):
    tenant = await auth_tenant_flex(request)
    await get_db().helcim_saved_methods.delete_one(
        {"_id": ObjectId(mid), "tenant_id": str(tenant["_id"])})
    await get_db().autopay_config.update_many(
        {"user_id": str(tenant["_id"]), "helcim_method_id": mid},
        {"$set": {"enabled": False}})
    return {"success": True}


@router.post("/tenant/helcim/pay-with-method")
async def pay_with_method(request: Request):
    """Cobro 1-tap: paga la renta pendiente con un método guardado."""
    tenant = await auth_tenant_flex(request)
    data = await request.json()
    db = get_db()
    m = await db.helcim_saved_methods.find_one(
        {"_id": ObjectId(data.get("method_id", "0" * 24)), "tenant_id": str(tenant["_id"])})
    if not m:
        raise HTTPException(404, "Método de pago no encontrado")

    ids = {tenant["_id"], str(tenant["_id"])}
    contract = await db.rental_contracts.find_one(
        {"tenant_id": {"$in": list(ids)}, "status": {"$in": ["active", "activo"]}})
    if not contract:
        raise HTTPException(404, "Sin contrato activo")
    pending = await db.rental_payments.find_one(
        {"contract_id": str(contract["_id"]),
         "status": {"$in": ["pending", "late", "partial"]}}, sort=[("due_date", 1)])
    if not pending:
        raise HTTPException(400, "No tienes pagos pendientes este mes 🎉")
    total_cents = int(round((float(pending.get("amount") or 0)
                             + float(pending.get("late_fee") or 0)) * 100))
    if total_cents <= 0:
        raise HTTPException(400, "Monto inválido")

    cfg = await _helcim_cfg()
    tx = await helcim_purchase_with_token(
        cfg["api_token"], total_cents, m["card_token"], m.get("customer_code", ""),
        (request.client.host if request.client else "127.0.0.1"))
    status = str(tx.get("status", "")).upper()
    if status not in ("APPROVED", "APPROVAL"):
        raise HTTPException(402, f"Pago no aprobado ({status or 'DECLINED'})")

    now = datetime.now(timezone.utc)
    receipt = f"HLC-{now.strftime('%Y%m%d')}-{str(tenant['_id'])[-4:]}"
    await db.rental_payments.update_one({"_id": pending["_id"]}, {"$set": {
        "status": "completed", "payment_method": "helcim_saved",
        "receipt_number": receipt, "reference_number": str(tx.get("transactionId", "")),
        "total_paid": total_cents / 100, "payment_date": now.isoformat(),
        "updated_at": now}})
    logger.info("💳 1-tap Helcim: %s pagó $%.2f (%s)", tenant.get("name"),
                total_cents / 100, receipt)
    return {"success": True, "receipt_number": receipt,
            "transaction_id": str(tx.get("transactionId", ""))}


@router.post("/tenant/helcim/autopay")
async def set_autopay(request: Request):
    tenant = await auth_tenant_flex(request)
    data = await request.json()
    db = get_db()
    enabled = bool(data.get("enabled"))
    update = {"enabled": enabled, "processor": "helcim",
              "user_id": str(tenant["_id"]),
              "day_of_month": max(1, min(28, int(data.get("day_of_month") or 1))),
              "updated_at": datetime.now(timezone.utc)}
    if enabled:
        mid = data.get("method_id", "")
        m = await db.helcim_saved_methods.find_one(
            {"_id": ObjectId(mid), "tenant_id": str(tenant["_id"])})
        if not m:
            raise HTTPException(400, "Selecciona un método de pago guardado")
        update["helcim_method_id"] = mid
        update["helcim_card_token"] = m["card_token"]
        update["helcim_customer_code"] = m.get("customer_code", "")
    await db.autopay_config.update_one(
        {"user_id": str(tenant["_id"]), "processor": "helcim"},
        {"$set": update}, upsert=True)
    return {"success": True, "enabled": enabled}
