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
_SAVE_SESSION_PUBLIC_STATUSES = frozenset({"pending", "verified", "failed"})
AUTOPAY_AUTHORIZATION_VERSION = "helcim-autopay-v1"
AUTOPAY_AUTHORIZATION_SCOPE = "monthly_canonical_rent_balance"


def _authorization_event(config: dict, request_id: str):
    for event in config.get("authorization_history") or []:
        if event.get("authorization_request_id") == request_id:
            return event
    return None


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
                               "paymentMethod": "cc",
                               "customStyling": {"appearance": "system",
                                                 "brandColor": "B30D2F",
                                                 "cornerRadius": "rounded"},
                               "confirmationScreen": True})
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
    return {"success": True, "session_id": sid,
            "url": f"{_public_base_url()}/api/public/helcim-checkout/{sid}"}


@router.get("/tenant/helcim/save-method-sessions/{session_id}")
async def save_method_session_status(session_id: str, request: Request):
    """Return the authenticated tenant's verification state without vault data.

    Mobile clients may poll this endpoint to close the Helcim WebView only after
    the server has validated Helcim's signed response.  Checkout/secret/card
    tokens and provider response data are deliberately never returned.
    """
    tenant = await auth_tenant_flex(request)
    session = await get_db().helcim_checkout_sessions.find_one({
        "_id": session_id,
        "tenant_id": str(tenant["_id"]),
        "purpose": "verify",
    })
    if not session:
        raise HTTPException(status_code=404, detail="Sesión de verificación no encontrada")

    status = str(session.get("status") or "pending").lower()
    if status not in _SAVE_SESSION_PUBLIC_STATUSES:
        status = "failed"
    return {
        "success": True,
        "session_id": session_id,
        "status": status,
        "method_saved": status == "verified",
    }


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
                        "day_of_month": ap.get("day_of_month", 1),
                        "authorization_version": AUTOPAY_AUTHORIZATION_VERSION}}


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
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(400, "Estado de autopago inválido")
    if enabled:
        raise HTTPException(
            409, "Actualiza la aplicación para registrar la autorización de autopago")

    # Older clients must still be able to revoke, but cannot bypass the
    # explicit authorization contract when enabling or changing a method.
    now = datetime.now(timezone.utc)
    event = {
        "authorization_request_id": uuid.uuid4().hex,
        "authorization_version": AUTOPAY_AUTHORIZATION_VERSION,
        "scope": AUTOPAY_AUTHORIZATION_SCOPE,
        "action": "revoked",
        "accepted": False,
        "source": "legacy_endpoint",
        "recorded_at": now,
    }
    await get_db().autopay_config.update_many(
        {"user_id": str(tenant["_id"]), "processor": "helcim"},
        {"$set": {"enabled": False, "authorization": event, "updated_at": now},
         "$unset": {"helcim_method_id": "", "helcim_card_token": "",
                    "helcim_customer_code": ""},
         "$push": {"authorization_history": event}},
    )
    return {"success": True, "enabled": False}


@router.post("/tenant/helcim/autopay/authorization")
async def authorize_autopay(request: Request):
    """Enable or revoke autopay with an append-only authorization snapshot.

    This additive v2 contract lets deployed clients migrate without changing
    the legacy endpoint in place.  The event contains display/audit metadata,
    never the Helcim card token or customer code used by the charge worker.
    """
    from .security import client_ip_hash

    tenant = await auth_tenant_flex(request)
    data = await request.json()
    enabled = data.get("enabled")
    request_id = str(data.get("authorization_request_id") or "").lower()
    version = str(data.get("authorization_version") or "")
    if not isinstance(enabled, bool):
        raise HTTPException(400, "Estado de autopago inválido")
    if not (len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id)):
        raise HTTPException(400, "Identificador de autorización inválido")
    if version != AUTOPAY_AUTHORIZATION_VERSION:
        raise HTTPException(409, "Actualiza la aplicación para autorizar el autopago")
    if enabled and data.get("accepted") is not True:
        raise HTTPException(400, "Debes aceptar expresamente la autorización de autopago")

    tenant_id = str(tenant["_id"])
    day = max(1, min(28, int(data.get("day_of_month") or 1)))
    method_id = str(data.get("method_id") or "")
    db = get_db()
    existing = await db.autopay_config.find_one(
        {"user_id": tenant_id, "processor": "helcim"})
    prior_event = _authorization_event(existing or {}, request_id)
    if prior_event:
        return {"success": True, "enabled": prior_event.get("action") == "authorized",
                "authorization_id": request_id, "duplicate": True}

    method = None
    if enabled:
        try:
            method = await db.helcim_saved_methods.find_one({
                "_id": ObjectId(method_id), "tenant_id": tenant_id})
        except Exception:
            method = None
        if not method:
            raise HTTPException(400, "Selecciona un método de pago guardado")

    now = datetime.now(timezone.utc)
    event = {
        "authorization_request_id": request_id,
        "authorization_version": AUTOPAY_AUTHORIZATION_VERSION,
        "scope": AUTOPAY_AUTHORIZATION_SCOPE,
        "action": "authorized" if enabled else "revoked",
        "accepted": enabled,
        "method_id": method_id if enabled else "",
        "method_type": str((method or {}).get("type") or "card") if enabled else "",
        "method_brand": str((method or {}).get("brand") or "")[:40] if enabled else "",
        "method_last4": str((method or {}).get("last4") or "")[-4:] if enabled else "",
        "day_of_month": day,
        "recorded_at": now,
        "ip_hash": client_ip_hash(request),
        "user_agent": str(request.headers.get("user-agent") or "")[:300],
    }
    update = {
        "$set": {"enabled": enabled, "processor": "helcim", "user_id": tenant_id,
                 "day_of_month": day, "authorization": event, "updated_at": now},
        "$push": {"authorization_history": event},
    }
    if enabled:
        update["$set"].update({
            "helcim_method_id": method_id,
            "helcim_card_token": method["card_token"],
            "helcim_customer_code": method.get("customer_code", ""),
        })
    else:
        update["$unset"] = {"helcim_method_id": "", "helcim_card_token": "",
                              "helcim_customer_code": ""}

    config_id = existing.get("_id") if existing else f"helcim:{tenant_id}"
    try:
        result = await db.autopay_config.update_one(
            {"_id": config_id,
             "authorization_history.authorization_request_id": {"$ne": request_id}},
            update, upsert=not bool(existing))
    except Exception:
        duplicate = await db.autopay_config.find_one({"_id": config_id})
        duplicate_event = _authorization_event(duplicate or {}, request_id)
        if duplicate_event:
            return {"success": True,
                    "enabled": duplicate_event.get("action") == "authorized",
                    "authorization_id": request_id, "duplicate": True}
        raise
    if not (getattr(result, "modified_count", 0) or getattr(result, "upserted_id", None)):
        duplicate = await db.autopay_config.find_one({"_id": config_id})
        duplicate_event = _authorization_event(duplicate or {}, request_id)
        if duplicate_event:
            return {"success": True,
                    "enabled": duplicate_event.get("action") == "authorized",
                    "authorization_id": request_id, "duplicate": True}
        raise HTTPException(409, "No se pudo registrar la autorización")
    return {"success": True, "enabled": enabled,
            "authorization_id": request_id, "duplicate": False}
