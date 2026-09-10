"""Validate vault-only Helcim responses. Never charge or update a rent ledger."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

SESSION_LIFETIME = timedelta(minutes=30)


def session_expired(session, now=None):
    now = now or datetime.now(timezone.utc)
    expires = session.get("expires_at")
    if not isinstance(expires, datetime):
        created = session.get("created_at")
        if not isinstance(created, datetime):
            return True
        expires = created + SESSION_LIFETIME
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= now


def parse_signed_verification(session, raw):
    """Accept the documented envelope or the Helcim eventMessage wrapper."""
    try:
        outer = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(outer, dict):
            raise ValueError()
        envelope = outer if "hash" in outer else outer.get("data")
        if not isinstance(envelope, dict):
            raise ValueError()
        tx, supplied = envelope["data"], envelope["hash"]
        if not isinstance(tx, dict) or not isinstance(supplied, str):
            raise ValueError()
        secret = session["secret_token"]
        if not isinstance(secret, str) or not secret:
            raise ValueError()
        canonical = json.dumps(tx, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        expected = hashlib.sha256((canonical + secret).encode()).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError()
        amount = Decimal(str(tx.get("amount")))
        if not amount.is_finite() or amount != 0 or tx.get("currency") != "USD":
            raise ValueError()
    except (TypeError, ValueError, KeyError, InvalidOperation):
        raise HTTPException(400, "Respuesta de verificación inválida") from None

    kind = session.get("payment_method", "cc")
    if kind == "ach":
        token = tx.get("bankToken")
        # PENDING may save a bank token; it does not prove PAD approval or payment.
        auth = str(tx.get("statusAuth", tx.get("status", ""))).upper()
        clearing = str(tx.get("statusClearing", "")).upper()
        if auth not in {"PENDING", "APPROVED", "APPROVAL", "1", "5"} or clearing in {"4", "DECLINED", "FAILED", "CANCELLED"}:
            raise HTTPException(400, "No se pudo guardar la cuenta bancaria")
        customer = tx.get("customerCode")
        if not isinstance(customer, str) or not customer or len(customer) > 100:
            raise HTTPException(400, "Falta la referencia bancaria de Helcim")
        masked = tx.get("bankAccountNumber", "")
        fields = {"type": "bank", "bank_token": token, "customer_code": customer,
                  "brand": "ACH", "verification_status": "saved",
                  "authorization_status": "not_checked"}
    elif kind == "cc":
        card_data = tx.get("cardData") if isinstance(tx.get("cardData"), dict) else {}
        token = tx.get("cardToken") or card_data.get("cardToken")
        if str(tx.get("status", "")).upper() not in {"APPROVED", "APPROVAL"}:
            raise HTTPException(400, "No se pudo guardar la tarjeta")
        masked = tx.get("cardNumber", "")
        fields = {"type": "card", "card_token": token,
                  "customer_code": str(tx.get("customerCode") or "")[:100],
                  "brand": str(tx.get("cardType") or "Card")[:40]}
    else:
        raise HTTPException(400, "Tipo de verificación inválido")
    if not isinstance(token, str) or not token or len(token) > 512:
        raise HTTPException(400, "Falta el token de Helcim")
    # Never persist the full response, account/routing numbers, card number or CVV.
    digits = re.sub(r"\D", "", str(masked))
    fields["last4"] = digits[-4:] if len(digits) >= 4 else ""
    return fields


async def complete_saved_verification(db, session, raw):
    if session.get("purpose") != "verify" or session.get("amount_cents") != 0 or not session.get("tenant_id"):
        raise HTTPException(400, "Sesión de verificación inválida")
    if session.get("status") == "verified":
        return {"status": "verified"}
    if session_expired(session):
        raise HTTPException(410, "La sesión expiró; abre una nueva")
    fields = parse_signed_verification(session, raw)
    # Mongo's unique _id is the concurrency barrier. Retry after a crash reuses
    # the same method even if the session's final status was not persisted.
    identity = f"helcim-vault:{session['_id']}:{session['tenant_id']}"
    method_id = ObjectId(hashlib.sha256(identity.encode()).hexdigest()[:24])
    now = datetime.now(timezone.utc)
    fields.update({"tenant_id": session["tenant_id"], "created_at": now,
                   "verification_session_id": session["_id"]})
    try:
        await db.helcim_saved_methods.update_one(
            {"_id": method_id}, {"$setOnInsert": fields}, upsert=True)
    except DuplicateKeyError:
        existing = await db.helcim_saved_methods.find_one({"_id": method_id})
        if not existing or existing.get("tenant_id") != session["tenant_id"]:
            raise
    await db.helcim_checkout_sessions.update_one(
        {"_id": session["_id"], "purpose": "verify"},
        {"$set": {"status": "verified", "saved_method_id": str(method_id), "updated_at": now}})
    return {"status": "verified"}
