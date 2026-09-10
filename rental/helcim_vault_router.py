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
from .helcim_saved_verification import SESSION_LIFETIME, session_expired

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
    """Create a zero-dollar card/bank verification; legacy clients default to cc."""
    tenant = await auth_tenant_flex(request)
    data = await request.json()
    if not isinstance(data, dict) or data.get("payment_method", "cc") not in ("cc", "ach"):
        raise HTTPException(422, "Selecciona tarjeta o cuenta bancaria")
    payment_method = data.get("payment_method", "cc")
    cfg = await _helcim_cfg()
    if not cfg.get("api_token"):
        raise HTTPException(400, "Helcim no configurado")
    async with httpx.AsyncClient(timeout=20) as x:
        r = await x.post(f"{HELCIM_BASE}/helcim-pay/initialize",
                         headers={"api-token": cfg["api_token"], "accept": "application/json"},
                         json={"paymentType": "verify", "amount": 0, "currency": "USD",
                               "paymentMethod": payment_method,
                               "customStyling": {"appearance": "system",
                                                 "brandColor": "B30D2F",
                                                 "cornerRadius": "rounded"},
                               "confirmationScreen": True})
    if r.status_code >= 400:
        raise HTTPException(502, "No se pudo iniciar la verificación con Helcim")
    data = r.json()
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k] for k in ("checkoutToken", "secretToken")):
        raise HTTPException(502, "Respuesta de Helcim incompleta")
    sid = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    from .payment_processors_router import _public_base_url
    await get_db().helcim_checkout_sessions.insert_one({
        "_id": sid, "checkout_token": data["checkoutToken"],
        "secret_token": data["secretToken"], "amount_cents": 0,
        "purpose": "verify", "tenant_id": str(tenant["_id"]),
        "payment_method": payment_method, "expires_at": now + SESSION_LIFETIME,
        "tenant_name": tenant.get("name", ""), "status": "pending",
        "created_at": now})
    return {"success": True, "session_id": sid,
            "expires_at": (now + SESSION_LIFETIME).isoformat(),
            "url": f"{_public_base_url()}/api/public/helcim-checkout/{sid}"}


@router.get("/tenant/helcim/methods")
async def list_methods(request: Request):
    tenant = await auth_tenant_flex(request)
    items = []
    async for m in get_db().helcim_saved_methods.find({"tenant_id": str(tenant["_id"])}):
        # Old app versions assume every listed method is a chargeable card.
        # Only clients explicitly opting in can see bank records.
        bank = m.get("type") == "bank"
        if bank and request.query_params.get("include_bank") != "true":
            continue
        items.append({"id": str(m["_id"]), "brand": m.get("brand", "Tarjeta"),
                      "last4": m.get("last4", ""), "type": m.get("type", "card"),
                      "can_pay": not bank, "can_autopay": not bank,
                      "authorization_status": m.get("authorization_status", "not_checked") if bank else "not_applicable",
                      "created_at": m["created_at"].isoformat()})
    ap = await get_db().autopay_config.find_one({"user_id": str(tenant["_id"]),
                                                 "processor": "helcim"}) or {}
    return {"success": True, "methods": items,
            "capabilities": {"saved_ach": True, "saved_ach_payments": False},
            "autopay": {"enabled": bool(ap.get("enabled")),
                        "method_id": str(ap.get("helcim_method_id", "")),
                        "day_of_month": ap.get("day_of_month", 1)}}


@router.get("/tenant/helcim/save-method-sessions/{sid}")
async def save_method_status(sid: str, request: Request):
    tenant = await auth_tenant_flex(request)
    session = await get_db().helcim_checkout_sessions.find_one(
        {"_id": sid, "tenant_id": str(tenant["_id"]), "purpose": "verify"})
    if not session:
        raise HTTPException(404, "Sesión no encontrada")
    status = "verified" if session.get("status") == "verified" else (
        "expired" if session_expired(session) else "pending")
    return {"success": True, "status": status}


def _method_id(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise HTTPException(422, "Método de pago inválido")
    return ObjectId(value)


def _require_card(method):
    if method.get("type", "card") != "card" or not method.get("card_token"):
        raise HTTPException(409, "Los pagos con cuentas bancarias guardadas aún no están disponibles")


@router.delete("/tenant/helcim/methods/{mid}")
async def delete_method(mid: str, request: Request):
    tenant = await auth_tenant_flex(request)
    await get_db().helcim_saved_methods.delete_one(
        {"_id": _method_id(mid), "tenant_id": str(tenant["_id"])})
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
        {"_id": _method_id(data.get("method_id")), "tenant_id": str(tenant["_id"])})
    if not m:
        raise HTTPException(404, "Método de pago no encontrado")
    _require_card(m)

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
            {"_id": _method_id(mid), "tenant_id": str(tenant["_id"])})
        if not m:
            raise HTTPException(400, "Selecciona un método de pago guardado")
        _require_card(m)
        update["helcim_method_id"] = mid
        update["helcim_card_token"] = m["card_token"]
        update["helcim_customer_code"] = m.get("customer_code", "")
    await db.autopay_config.update_one(
        {"user_id": str(tenant["_id"]), "processor": "helcim"},
        {"$set": update}, upsert=True)
    return {"success": True, "enabled": enabled}
