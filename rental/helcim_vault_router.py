"""Helcim Vault — métodos de pago guardados, cobro 1-tap y autopago.

Flujo: el inquilino verifica su tarjeta UNA vez en el modal seguro (paymentType
'verify') → Helcim devuelve cardToken → guardamos solo el token. Después todo
es nativo: cobro server-side con /v2/payment/purchase usando el token.
"""
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from .shared import get_db, auth_admin, auth_tenant_flex

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


def encrypt_provider_token(token: str) -> str:
    """Encrypt new Helcim tokens at rest; raw account data is never accepted."""
    from .vault_router import encrypt
    return f"enc:{encrypt(token)}" if token else ""


def decrypt_provider_token(token: str) -> str:
    """Read encrypted tokens while retaining compatibility with legacy rows."""
    if not token:
        return ""
    if not token.startswith("enc:"):
        return token
    from .vault_router import decrypt
    return decrypt(token[4:])


def provider_token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""


async def helcim_ach_with_account(api_token: str, amount_cents: int,
                                  customer_id: int, bank_account_id: int) -> dict:
    """Start an ACH debit against a Helcim-vaulted, authorized bank account."""
    body = {"customerId": int(customer_id), "bankAccountId": int(bank_account_id),
            "amount": round(amount_cents / 100, 2), "currencyId": 2}
    async with httpx.AsyncClient(timeout=30) as x:
        r = await x.put(f"{HELCIM_BASE}/ach/withdraw",
                        headers={"api-token": api_token, "accept": "application/json",
                                 "idempotency-key": str(uuid.uuid4())}, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=402, detail=f"Helcim rechazó el débito ACH: {r.text[:180]}")
    return r.json()


def _payload_items(payload, *keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
        if isinstance(payload.get("data"), (list, dict)):
            return _payload_items(payload["data"], *keys)
    return []


def _helcim_customer_request(tenant: dict) -> dict:
    """Build Helcim's non-sensitive customer object from the tenant profile.

    Billing details are included only when Helcim's required street/postal
    pair is available. Card, bank-account and CVV data never pass through this
    helper or our API.
    """
    name = str(tenant.get("name") or "").strip()[:100]
    customer = {"contactName": name or "Ross House Rentals tenant"}

    phone = re.sub(r"[^0-9+]", "", str(tenant.get("phone") or ""))
    if 10 <= len(phone.lstrip("+")) <= 16:
        customer["cellPhone"] = phone

    raw_address = tenant.get("billing_address")
    address = raw_address if isinstance(raw_address, dict) else {}
    street = str(address.get("street1") or tenant.get("address") or "").strip()
    postal = str(address.get("postalCode") or tenant.get("postal_code") or
                 tenant.get("zip_code") or tenant.get("zip") or "").strip()
    if not postal and street:
        postal_match = re.search(r"\b\d{5}(?:-\d{4})?\b", street)
        postal = postal_match.group(0) if postal_match else ""

    if street and postal:
        billing = {
            "name": name or "Ross House Rentals tenant",
            "street1": street[:100],
            "postalCode": postal[:10],
        }
        optional = {
            "street2": address.get("street2"),
            "city": address.get("city") or tenant.get("city"),
            "province": address.get("province") or tenant.get("state"),
            "email": tenant.get("email"),
            "phone": phone,
        }
        for key, value in optional.items():
            value = str(value or "").strip()
            if value:
                billing[key] = value[:100]
        province = str(billing.get("province") or "").strip()
        if province:
            billing["country"] = str(address.get("country") or "USA").strip()[:3]
        customer["billingAddress"] = billing
    return customer


async def _helcim_customer_context(db, tenant: dict) -> dict:
    """Reuse an existing Helcim customer; otherwise create one from profile."""
    tenant_id = str(tenant["_id"])
    existing = await db.helcim_saved_methods.find_one(
        {"tenant_id": tenant_id, "customer_code": {"$type": "string", "$ne": ""}},
        sort=[("created_at", -1)],
    )
    customer_code = str((existing or {}).get("customer_code") or "").strip()
    if customer_code:
        return {"customerCode": customer_code}
    return {"customerRequest": _helcim_customer_request(tenant)}


async def resolve_ach_vault_ids(api_token: str, customer_code: str,
                                bank_token: str, last4: str) -> tuple[int | None, int | None]:
    """Resolve opaque Helcim customer/bank IDs after hosted tokenization."""
    if not customer_code:
        return None, None
    headers = {"api-token": api_token, "accept": "application/json"}
    async with httpx.AsyncClient(timeout=20) as x:
        response = await x.get(f"{HELCIM_BASE}/customers", headers=headers,
                               params={"customerCode": customer_code, "limit": 10})
        if response.status_code >= 400:
            return None, None
        customers = _payload_items(response.json(), "customers")
        customer = next((item for item in customers
                         if str(item.get("customerCode") or "") == customer_code), None)
        if not customer:
            return None, None
        customer_id = customer.get("id") or customer.get("customerId")
        if not customer_id:
            return None, None
        response = await x.get(
            f"{HELCIM_BASE}/customers/{customer_id}/bank-accounts", headers=headers)
        if response.status_code >= 400:
            return int(customer_id), None
        banks = _payload_items(response.json(), "bankAccounts", "banks")
        def matches(item):
            token = str(item.get("bankToken") or "")
            number = str(item.get("bankAccountNumber") or item.get("accountNumber") or
                         item.get("bankAccountL4") or "")
            return bool((bank_token and token == bank_token) or
                        (last4 and number.endswith(last4)))
        bank = next((item for item in banks if matches(item)), None)
        if not bank and len(banks) == 1:
            bank = banks[0]
        bank_id = (bank or {}).get("id") or (bank or {}).get("bankAccountId")
        return int(customer_id), int(bank_id) if bank_id else None


@router.post("/tenant/helcim/save-method-session")
async def save_method_session(request: Request):
    """Create a hosted Helcim vault session for a card or ACH account."""
    tenant = await auth_tenant_flex(request)
    data_in = await request.json()
    method_type = str(data_in.get("method_type") or "card").lower()
    if method_type not in {"card", "ach"}:
        raise HTTPException(400, "Tipo de método de pago inválido")
    helcim_method = "cc" if method_type == "card" else "ach"
    cfg = await _helcim_cfg()
    if not cfg.get("api_token"):
        raise HTTPException(400, "Helcim no configurado")
    db = get_db()
    customer_context = await _helcim_customer_context(db, tenant)
    initialize_payload = {
        "paymentType": "verify", "amount": 0, "currency": "USD",
        "paymentMethod": helcim_method,
        "setAsDefaultPaymentMethod": 1,
        # The app already owns this profile data. Do not make tenants type it
        # again in Helcim's hosted UI.
        "displayContactFields": 0,
        "customStyling": {"appearance": "system", "brandColor": "B30D2F",
                          "cornerRadius": "rounded"},
        "confirmationScreen": True,
        **customer_context,
    }
    async with httpx.AsyncClient(timeout=20) as x:
        r = await x.post(f"{HELCIM_BASE}/helcim-pay/initialize",
                         headers={"api-token": cfg["api_token"], "accept": "application/json"},
                         json=initialize_payload)
    if r.status_code >= 400:
        raise HTTPException(502, f"Helcim: {r.text[:150]}")
    data = r.json()
    sid = uuid.uuid4().hex
    from .payment_processors_router import _public_base_url
    redirect_url = f"rossrentals://pay/methods?helcim_session={sid}"
    await db.helcim_checkout_sessions.insert_one({
        "_id": sid, "checkout_token": data["checkoutToken"],
        "secret_token": data["secretToken"], "amount_cents": 0,
        "purpose": "verify", "method_type": method_type,
        "redirect_url": redirect_url, "tenant_id": str(tenant["_id"]),
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
                      "ready_for_payments": bool(m.get("ready_for_payments", True)),
                      "created_at": m["created_at"].isoformat()})
    ap = await get_db().autopay_config.find_one({"user_id": str(tenant["_id"]),
                                                 "processor": "helcim"}) or {}
    return {"success": True, "methods": items,
            "autopay": {"enabled": bool(ap.get("enabled")),
                        "method_id": str(ap.get("helcim_method_id", "")),
                        "day_of_month": ap.get("day_of_month", 1),
                        "authorization_version": AUTOPAY_AUTHORIZATION_VERSION}}


@router.get("/admin/helcim/vault-methods")
async def admin_list_tokenized_methods(request: Request):
    """Masked admin inventory. Provider tokens and raw payment data never leave the API."""
    await auth_admin(request)
    db = get_db()
    items = []
    async for method in db.helcim_saved_methods.find({}).sort("created_at", -1):
        tenant_id = str(method.get("tenant_id") or "")
        tenant = None
        for value in (tenant_id, ObjectId(tenant_id) if ObjectId.is_valid(tenant_id) else None):
            if value is not None:
                tenant = await db.app_users.find_one({"_id": value})
                if tenant:
                    break
        items.append({
            "id": str(method["_id"]),
            "tenant_id": tenant_id,
            "tenant_name": str((tenant or {}).get("name") or ""),
            "tenant_email": str((tenant or {}).get("email") or ""),
            "type": str(method.get("type") or "card"),
            "brand": str(method.get("brand") or "Método Helcim"),
            "last4": str(method.get("last4") or "")[-4:],
            "ready_for_payments": bool(method.get("ready_for_payments", True)),
            "created_at": method.get("created_at").isoformat()
                if isinstance(method.get("created_at"), datetime) else "",
        })
    return {"success": True, "methods": items,
            "security": "helcim_tokenized_masked_only"}


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
    method_id = data.get("method_id")
    if not isinstance(method_id, str) or not ObjectId.is_valid(method_id):
        raise HTTPException(400, "Método de pago inválido")
    m = await db.helcim_saved_methods.find_one(
        {"_id": ObjectId(method_id), "tenant_id": str(tenant["_id"])})
    if not m:
        raise HTTPException(404, "Método de pago no encontrado")

    ids = {tenant["_id"], str(tenant["_id"])}
    contract = await db.rental_contracts.find_one(
        {"tenant_id": {"$in": list(ids)}, "status": {"$in": ["active", "activo"]}})
    if not contract:
        raise HTTPException(404, "Sin contrato activo")
    from .rent_charge_policy import resolve_current_rent_charge
    from .rent_charge_claim import claim_rent_charge
    try:
        charge = await resolve_current_rent_charge(db, contract)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, "La renta actual requiere revisión") from exc
    total = charge["outstanding"]
    if total <= 0:
        raise HTTPException(400, "No tienes saldo pendiente este mes")
    method_type = str(m.get("type") or "card")
    if method_type == "card" and not m.get("card_token"):
        raise HTTPException(400, "Selecciona una tarjeta guardada válida")
    if method_type == "ach" and not (m.get("customer_id") and m.get("bank_account_id")):
        raise HTTPException(409, "La cuenta bancaria todavía está siendo verificada por Helcim")
    cfg = await _helcim_cfg()
    if not cfg.get("api_token"):
        raise HTTPException(400, "Helcim no configurado")
    attempt = await claim_rent_charge(
        db, charge["invoice_id"], source="helcim_saved", amount=total,
        contract_id=contract["_id"])
    if not attempt:
        raise HTTPException(409, "Ya existe un intento de pago o el saldo cambió; requiere revisión")
    query = {"_id": charge["invoice"]["_id"], "charge_attempt.id": attempt["id"]}
    try:
        if method_type == "ach":
            tx = await helcim_ach_with_account(
                cfg["api_token"], int(round(total * 100)),
                int(m["customer_id"]), int(m["bank_account_id"]))
            ach_tx = tx.get("transaction") if isinstance(tx.get("transaction"), dict) else tx
            transaction_id = str(ach_tx.get("id") or ach_tx.get("transactionId") or "")
            await db.rental_payments.update_one(query, {"$set": {
                "charge_attempt.status": "reconciliation_required",
                "charge_attempt.provider_status": "ACH_PENDING",
                "charge_attempt.transaction_id": transaction_id}})
            return {"success": True, "status": "ach_pending",
                    "transaction_id": transaction_id}

        tx = await helcim_purchase_with_token(
            cfg["api_token"], int(round(total * 100)),
            decrypt_provider_token(m["card_token"]), m.get("customer_code", ""),
            request.client.host if request.client else "127.0.0.1")
        status = str(tx.get("status", "")).upper()
        transaction_id = str(tx.get("transactionId") or "")
        await db.rental_payments.update_one(query, {"$set": {
            "charge_attempt.status": "reconciliation_required",
            "charge_attempt.provider_status": status,
            "charge_attempt.transaction_id": transaction_id}})
        if status not in ("APPROVED", "APPROVAL") or not transaction_id:
            raise HTTPException(409, "El pago requiere confirmación; no vuelvas a intentarlo")
        now = datetime.now(timezone.utc)
        receipt = f"HLC-{attempt['id']}"
        # Do not overwrite partial payments or a concurrent accounting change.
        completion_query = {**query, "status": charge["invoice"]["status"]}
        for field in ("amount", "late_fee", "total_due", "total_paid"):
            completion_query[field] = (charge["invoice"][field] if field in charge["invoice"]
                                       else {"$exists": False})
        result = await db.rental_payments.update_one(completion_query, {"$set": {
            "status": "completed", "paid": True, "payment_method": "helcim_saved",
            "receipt_number": receipt, "reference_number": transaction_id,
            "total_paid": charge["total_due"], "payment_date": now,
            "updated_at": now, "charge_attempt.status": "completed"}})
        if result.modified_count != 1:
            raise HTTPException(409, "Pago recibido; conciliación pendiente")
        return {"success": True, "receipt_number": receipt, "transaction_id": transaction_id}
    except HTTPException:
        raise
    except Exception as exc:
        # Keep the durable claim even if the provider response or local write is lost.
        logger.warning("Saved-card charge requires reconciliation: %s", attempt["id"])
        raise HTTPException(503, "Estado del pago pendiente de verificación; no repitas el cobro") from exc


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
                    "helcim_customer_code": "", "helcim_method_type": "",
                    "helcim_customer_id": "", "helcim_bank_account_id": ""},
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
        if not method.get("ready_for_payments", True):
            raise HTTPException(409, "El método todavía está siendo verificado por Helcim")

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
            "helcim_method_type": str(method.get("type") or "card"),
            "helcim_card_token": method.get("card_token", ""),
            "helcim_customer_id": method.get("customer_id"),
            "helcim_bank_account_id": method.get("bank_account_id"),
            "helcim_customer_code": method.get("customer_code", ""),
        })
    else:
        update["$unset"] = {"helcim_method_id": "", "helcim_card_token": "",
                              "helcim_customer_code": "", "helcim_method_type": "",
                              "helcim_customer_id": "", "helcim_bank_account_id": ""}

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
