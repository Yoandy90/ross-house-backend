"""Admin Vault (Baúl Seguro) — PIN-protected access to client payment methods.

Architecture:
  1. Admin sets a PIN once (hashed with bcrypt, stored in rental_config).
  2. To access full account numbers, admin POSTs PIN → gets a short-lived
     "vault session token" (JWT, 30 min TTL). The token includes
     {admin_id, vault_unlocked: true}.
  3. List endpoint returns masked data (last4 only) — no token required.
  4. Reveal endpoint requires the vault token + returns FULL routing/account.
  5. All accesses are audited to `vault_audit_log`.

Sensitive data is stored encrypted at rest using Fernet (symmetric AES-128).
The encryption key (VAULT_KEY) lives in env vars. If absent, a stable key is
derived from the SECRET_KEY for development.
"""
import os
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt
from cryptography.fernet import Fernet
from fastapi import APIRouter, Request, HTTPException
from bson import ObjectId

from .shared import get_db, auth_admin, serialize

router = APIRouter()
logger = logging.getLogger(__name__)

VAULT_JWT_SECRET = os.environ.get("VAULT_JWT_SECRET") or os.environ.get("JWT_SECRET", "vault-dev-secret")
VAULT_TOKEN_TTL_MIN = 30
VAULT_AUDIT_COLL = "vault_audit_log"


def _get_fernet() -> Fernet:
    """Derive a stable Fernet key from VAULT_ENCRYPTION_KEY env (or SECRET_KEY)."""
    raw_key = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not raw_key:
        # Derive from SECRET_KEY/JWT_SECRET — stable across restarts
        seed = os.environ.get("SECRET_KEY") or os.environ.get("JWT_SECRET") or "ross-vault-dev-2026"
        digest = hashlib.sha256(seed.encode()).digest()
        # Fernet keys must be url-safe base64-encoded 32 bytes
        import base64
        raw_key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)


def _get_legacy_fernet() -> Optional[Fernet]:
    """Legacy Fernet from the previous Ross Tax / Loans system. Used to
    decrypt the 16 pre-existing payment_methods docs that have
    `encrypted_number` / `encrypted_cvv`.

    Tries env LEGACY_ENCRYPTION_KEY first, then falls back to the well-known
    key from the legacy backend/.env.
    """
    legacy_key = os.environ.get("LEGACY_ENCRYPTION_KEY") or "Z74AYJ9mWLyG9BSctwl0l7OhGQVbHGgWM0viuoFdWoU="
    try:
        return Fernet(legacy_key.encode())
    except Exception as e:
        logger.warning(f"Legacy fernet init failed: {e}")
        return None


def decrypt_legacy(token: str) -> str:
    """Decrypt the OLD `encrypted_number` / `encrypted_cvv` format
    (base64-wrapped Fernet ciphertext)."""
    if not token:
        return ""
    cipher = _get_legacy_fernet()
    if not cipher:
        return ""
    import base64 as _b64
    # Try base64-wrapped first (legacy format)
    try:
        decoded = _b64.b64decode(token)
        return cipher.decrypt(decoded).decode()
    except Exception:
        pass
    # Try direct Fernet
    try:
        return cipher.decrypt(token.encode()).decode()
    except Exception:
        return ""


def encrypt(value: str) -> str:
    if not value:
        return ""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


def mask(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "•" * len(value)
    return "•" * (len(value) - visible) + value[-visible:]


async def _audit(db, admin_email: str, action: str, target: str = "", meta: Optional[dict] = None):
    try:
        await db[VAULT_AUDIT_COLL].insert_one({
            "admin_email": admin_email,
            "action": action,
            "target": target,
            "meta": meta or {},
            "timestamp": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.warning(f"vault audit failed: {e}")


async def _require_vault_session(request: Request):
    """Validates the X-Vault-Token header (issued by /admin/vault/unlock)."""
    token = request.headers.get("X-Vault-Token") or request.query_params.get("vault_token")
    if not token:
        raise HTTPException(status_code=403, detail="Vault session required. Unlock with PIN first.")
    try:
        payload = jwt.decode(token, VAULT_JWT_SECRET, algorithms=["HS256"])
        if not payload.get("vault_unlocked"):
            raise HTTPException(status_code=403, detail="Invalid vault token")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Vault session expired — re-enter PIN")
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid vault token")


# ════════════════════════════════════════════════════════════════════════════
# PIN management
# ════════════════════════════════════════════════════════════════════════════

@router.get("/admin/vault/pin-status")
async def vault_pin_status(request: Request):
    """Whether a vault PIN has been set yet (used to show 'set PIN' vs 'enter PIN' UI)."""
    await auth_admin(request)
    config = await get_db().rental_config.find_one({"type": "company"}) or {}
    return {
        "has_pin": bool(config.get("vault_pin_hash")),
        "configured_at": config.get("vault_pin_set_at"),
    }


@router.post("/admin/vault/set-pin")
async def vault_set_pin(request: Request):
    """Set or change the vault PIN. If a PIN already exists, the current one
    must be supplied as `current_pin`."""
    admin = await auth_admin(request)
    data = await request.json()

    new_pin = (data.get("new_pin") or "").strip()
    current_pin = (data.get("current_pin") or "").strip()

    if not new_pin or not new_pin.isdigit() or not (4 <= len(new_pin) <= 8):
        raise HTTPException(status_code=400, detail="El PIN debe tener entre 4 y 8 dígitos numéricos")

    db = get_db()
    config = await db.rental_config.find_one({"type": "company"}) or {}

    if config.get("vault_pin_hash"):
        if not current_pin:
            raise HTTPException(status_code=400, detail="Debes proveer el PIN actual para cambiarlo")
        try:
            ok = bcrypt.checkpw(current_pin.encode(), config["vault_pin_hash"].encode())
        except Exception:
            ok = False
        if not ok:
            await _audit(db, admin.get("email", ""), "set_pin_failed", meta={"reason": "wrong_current"})
            raise HTTPException(status_code=403, detail="PIN actual incorrecto")

    new_hash = bcrypt.hashpw(new_pin.encode(), bcrypt.gensalt()).decode()
    await db.rental_config.update_one(
        {"type": "company"},
        {"$set": {
            "vault_pin_hash": new_hash,
            "vault_pin_set_at": datetime.now(timezone.utc),
            "vault_pin_set_by": admin.get("email", ""),
        }},
        upsert=True,
    )
    await _audit(db, admin.get("email", ""), "pin_set_or_changed")
    return {"success": True, "message": "PIN configurado exitosamente"}


@router.post("/admin/vault/unlock")
async def vault_unlock(request: Request):
    """Exchange PIN for a short-lived vault session token (X-Vault-Token)."""
    admin = await auth_admin(request)
    data = await request.json()
    pin = (data.get("pin") or "").strip()
    if not pin:
        raise HTTPException(status_code=400, detail="PIN requerido")

    db = get_db()
    config = await db.rental_config.find_one({"type": "company"}) or {}
    pin_hash = config.get("vault_pin_hash")
    if not pin_hash:
        raise HTTPException(status_code=400, detail="No hay PIN configurado. Configúralo primero.")

    try:
        ok = bcrypt.checkpw(pin.encode(), pin_hash.encode())
    except Exception:
        ok = False

    if not ok:
        await _audit(db, admin.get("email", ""), "unlock_failed", meta={"ip": request.client.host if request.client else ""})
        # rate-limit unlock attempts
        attempts = config.get("vault_failed_attempts", 0) + 1
        update = {"vault_failed_attempts": attempts}
        if attempts >= 5:
            update["vault_locked_until"] = datetime.now(timezone.utc) + timedelta(minutes=15)
        await db.rental_config.update_one({"type": "company"}, {"$set": update})
        raise HTTPException(status_code=403, detail="PIN incorrecto")

    # Check lockout
    locked_until = config.get("vault_locked_until")
    if locked_until and locked_until > datetime.now(timezone.utc):
        raise HTTPException(status_code=429, detail=f"Demasiados intentos fallidos. Espera hasta {locked_until.strftime('%H:%M')}")

    # Issue token
    payload = {
        "vault_unlocked": True,
        "admin_id": str(admin.get("_id", "")),
        "admin_email": admin.get("email", ""),
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=VAULT_TOKEN_TTL_MIN)).timestamp()),
    }
    token = jwt.encode(payload, VAULT_JWT_SECRET, algorithm="HS256")
    await _audit(db, admin.get("email", ""), "unlock_success")
    await db.rental_config.update_one(
        {"type": "company"},
        {"$set": {"vault_failed_attempts": 0, "vault_locked_until": None}},
    )
    return {"success": True, "vault_token": token, "expires_in": VAULT_TOKEN_TTL_MIN * 60}


# ════════════════════════════════════════════════════════════════════════════
# Vault data — list & reveal
# ════════════════════════════════════════════════════════════════════════════

@router.get("/admin/vault/payment-methods")
async def vault_list_payment_methods(request: Request):
    """List all saved payment methods (cards + bank accounts) across all users.
    Sensitive fields are masked (last4 only). No vault session needed."""
    await auth_admin(request)
    db = get_db()
    items = []

    # ── Stripe cards (saved via /tenant/payment-methods/setup) ──
    async for pm in db.payment_methods.find({}).sort("created_at", -1):
        # Normalize legacy field names
        card_last4 = pm.get("card_last4") or pm.get("last4") or pm.get("last_4", "")
        card_brand = pm.get("card_brand") or pm.get("brand", "")
        is_legacy = bool(pm.get("encrypted_number") or pm.get("nmi_vault_id"))
        items.append({
            "id": str(pm["_id"]),
            "type": pm.get("type") or ("card" if (card_last4 or pm.get("encrypted_number")) else "bank"),
            "user_id": str(pm.get("user_id", "")),
            "user_name": pm.get("user_name") or pm.get("client_name") or pm.get("cardholder_name", ""),
            "user_email": pm.get("user_email", ""),
            "card_brand": card_brand,
            "card_last4": card_last4,
            "card_exp": pm.get("card_exp") or (f"{pm.get('exp_month', '')}/{pm.get('exp_year', '')}" if pm.get("exp_month") else ""),
            "bank_name": pm.get("bank_name", ""),
            "account_type": pm.get("account_type", ""),
            "account_last4": pm.get("account_last4", ""),
            "routing_masked": (mask(decrypt(pm.get("routing_encrypted", "")), visible=4)
                               if pm.get("routing_encrypted")
                               else (mask(pm.get("routing_number", ""), visible=4) if pm.get("routing_number") else "")),
            "is_default": bool(pm.get("is_default", False)),
            "is_active_for_autopay": bool(pm.get("is_active_for_autopay", False)),
            "stripe_payment_method_id": pm.get("stripe_payment_method_id", ""),
            "created_at": pm.get("created_at"),
            "source": pm.get("source", "stripe"),
            "is_legacy": is_legacy,
            "has_nmi_vault": bool(pm.get("nmi_vault_id")),
        })

    return {"success": True, "items": items, "count": len(items)}


@router.get("/admin/vault/payment-methods/{method_id}/reveal")
async def vault_reveal_method(method_id: str, request: Request):
    """Reveal full routing + account numbers — requires vault session token.

    Supports BOTH data formats:
      - New format (routing_encrypted / account_encrypted) — Fernet-decrypted
      - Legacy plain-text format (routing_number / account_number) — passed through
      - Legacy NMI/loans card format (nmi_vault_id) — only metadata returned
    """
    admin = await auth_admin(request)
    session = await _require_vault_session(request)
    db = get_db()

    pm = await db.payment_methods.find_one({"_id": ObjectId(method_id)})
    if not pm:
        raise HTTPException(status_code=404, detail="Método de pago no encontrado")

    # ── Try multiple field names (new + legacy) ─────────────
    routing_full = ""
    account_full = ""
    decrypt_error = None

    # NEW format (our /tenant/bank-accounts/add)
    if pm.get("routing_encrypted"):
        try:
            routing_full = decrypt(pm["routing_encrypted"])
            if not routing_full:
                decrypt_error = "Encriptación con llave diferente"
        except Exception:
            decrypt_error = "Error al desencriptar (llave incorrecta)"
    if pm.get("account_encrypted"):
        try:
            decrypted = decrypt(pm["account_encrypted"])
            if decrypted:
                account_full = decrypted
        except Exception:
            pass

    # LEGACY plain-text format (older loan flow)
    if not routing_full and pm.get("routing_number"):
        routing_full = str(pm.get("routing_number", ""))
    if not account_full and pm.get("account_number"):
        account_full = str(pm.get("account_number", ""))

    # LEGACY card-only with no bank info → nothing to reveal
    is_card_only = bool(pm.get("encrypted_number") or pm.get("nmi_vault_id") or pm.get("card_brand")) and not (routing_full or account_full)

    # ── Reveal legacy card number + CVV ───
    card_full = ""
    cvv_full = ""
    if pm.get("encrypted_number"):
        card_full = decrypt_legacy(pm.get("encrypted_number", ""))
    if pm.get("encrypted_cvv"):
        cvv_full = decrypt_legacy(pm.get("encrypted_cvv", ""))

    await _audit(db, admin.get("email", ""), "reveal", target=method_id, meta={
        "user_id": str(pm.get("user_id", "")),
        "type": pm.get("type"),
        "had_routing": bool(routing_full),
        "had_account": bool(account_full),
        "had_card_full": bool(card_full),
        "had_cvv": bool(cvv_full),
        "decrypt_error": decrypt_error,
    })

    return {
        "success": True,
        "id": str(pm["_id"]),
        "type": pm.get("type") or ("card" if is_card_only else "bank"),
        "routing_full": routing_full,
        "account_full": account_full,
        "card_full": card_full,            # Full PAN (legacy decrypted)
        "cvv_full": cvv_full,              # Full CVV (legacy decrypted)
        "card_last4": pm.get("card_last4") or pm.get("last4") or pm.get("last_4", ""),
        "card_brand": pm.get("card_brand") or pm.get("brand", ""),
        "card_exp": pm.get("card_exp") or (f"{pm.get('exp_month', '')}/{pm.get('exp_year', '')}" if pm.get("exp_month") else ""),
        "exp_month": pm.get("exp_month", ""),
        "exp_year": pm.get("exp_year", ""),
        "bank_name": pm.get("bank_name", ""),
        "account_type": pm.get("account_type", ""),
        "account_last4": pm.get("account_last4", ""),
        "user_name": pm.get("user_name") or pm.get("client_name") or pm.get("cardholder_name", ""),
        "user_email": pm.get("user_email", ""),
        "nmi_vault_id": pm.get("nmi_vault_id", ""),
        "legacy_format": is_card_only or bool(pm.get("encrypted_number")),
        "decrypt_warning": decrypt_error,
        "message": None if (card_full or routing_full) else (
            "⚠️ No se pueden mostrar los datos completos — falta la llave de encriptación legacy o el registro solo tiene un NMI vault_id."
            if is_card_only else None
        ),
    }


@router.delete("/admin/vault/payment-methods/{method_id}")
async def vault_delete_method(method_id: str, request: Request):
    """Permanently delete a payment method from the vault.
    Requires the vault session token (PIN-protected)."""
    admin = await auth_admin(request)
    session = await _require_vault_session(request)
    db = get_db()

    pm = await db.payment_methods.find_one({"_id": ObjectId(method_id)})
    if not pm:
        raise HTTPException(status_code=404, detail="Método de pago no encontrado")

    user_id = str(pm.get("user_id", ""))
    pm_type = pm.get("type") or ("card" if (pm.get("card_last4") or pm.get("last4") or pm.get("encrypted_number")) else "bank")
    last4 = pm.get("card_last4") or pm.get("last4") or pm.get("account_last4") or pm.get("last_4", "")

    # Soft-delete: archive the record so we can recover if needed, then remove
    await db.payment_methods_deleted.insert_one({
        **pm,
        "_original_id": pm["_id"],
        "deleted_at": datetime.now(timezone.utc),
        "deleted_by": admin.get("email", ""),
    })
    await db.payment_methods.delete_one({"_id": ObjectId(method_id)})

    # If this was the user's autopay PM, disable autopay for them
    if user_id:
        await db.autopay_config.update_many(
            {"payment_method_id": pm.get("stripe_payment_method_id", "")},
            {"$set": {"enabled": False, "disabled_reason": "payment_method_deleted_by_admin"}}
        )

    await _audit(db, admin.get("email", ""), "delete", target=method_id, meta={
        "user_id": user_id, "type": pm_type, "last4": last4,
    })

    return {
        "success": True,
        "message": f"Método de pago eliminado ({pm_type} ····{last4})",
    }


@router.get("/admin/vault/audit-log")
async def vault_audit_log(request: Request, limit: int = 100):
    """Last N vault access events."""
    await auth_admin(request)
    db = get_db()
    cursor = db[VAULT_AUDIT_COLL].find({}).sort("timestamp", -1).limit(limit)
    items = []
    async for ev in cursor:
        items.append({
            "id": str(ev["_id"]),
            "admin_email": ev.get("admin_email", ""),
            "action": ev.get("action", ""),
            "target": ev.get("target", ""),
            "meta": ev.get("meta", {}),
            "timestamp": ev.get("timestamp"),
        })
    return {"success": True, "items": items}


# ════════════════════════════════════════════════════════════════════════════
# Add bank account (ACH) — used by tenant flow too
# ════════════════════════════════════════════════════════════════════════════

@router.post("/tenant/bank-accounts/add")
async def tenant_add_bank_account(request: Request):
    """Tenant: Add a bank account (ACH) for paying rent via Stripe ACH.

    Body:
      account_holder_name: str
      routing_number: 9-digit string
      account_number: variable length numeric string
      account_type: 'checking' | 'savings'
      make_default: bool (optional)

    Saves both:
      - Encrypted full numbers in `payment_methods` (for the vault)
      - Stripe Customer + BankAccount source so it can be charged via ACH
    """
    from .shared import auth_tenant_flex
    user = await auth_tenant_flex(request)
    data = await request.json()

    holder = (data.get("account_holder_name") or "").strip()
    routing = (data.get("routing_number") or "").strip()
    account = (data.get("account_number") or "").strip()
    acc_type = (data.get("account_type") or "checking").lower()

    if not holder or not routing or not account:
        raise HTTPException(status_code=400, detail="Faltan datos del banco")
    if not routing.isdigit() or len(routing) != 9:
        raise HTTPException(status_code=400, detail="Routing number debe tener 9 dígitos")
    if not account.isdigit() or not (4 <= len(account) <= 17):
        raise HTTPException(status_code=400, detail="Account number inválido")
    if acc_type not in ("checking", "savings"):
        acc_type = "checking"

    db = get_db()
    config = await db.rental_config.find_one({"type": "company"}) or {}
    stripe_sk = config.get("stripe_secret_key") or os.environ.get("STRIPE_SECRET_KEY", "")

    stripe_pm_id = ""
    stripe_customer_id = (user.get("stripe_customer_id") or "")

    # Stripe is optional — if not configured, just save encrypted (manual ACH)
    if stripe_sk:
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_sk
            if not stripe_customer_id:
                cust = stripe_lib.Customer.create(
                    email=user.get("email"),
                    name=holder,
                    metadata={"tenant_id": str(user["_id"])},
                )
                stripe_customer_id = cust.id
                # Save customer id to user
                try:
                    await db.app_users.update_one(
                        {"_id": ObjectId(str(user["_id"]))},
                        {"$set": {"stripe_customer_id": stripe_customer_id}}
                    )
                except Exception:
                    pass

            # Create ACH PaymentMethod via Stripe (manual entry, requires micro-deposit verification)
            pm = stripe_lib.PaymentMethod.create(
                type="us_bank_account",
                us_bank_account={
                    "routing_number": routing,
                    "account_number": account,
                    "account_holder_type": "individual",
                    "account_type": acc_type,
                },
                billing_details={"name": holder, "email": user.get("email")},
            )
            stripe_lib.PaymentMethod.attach(pm.id, customer=stripe_customer_id)
            stripe_pm_id = pm.id
        except Exception as e:
            logger.warning(f"Stripe ACH attach failed: {e} — falling back to manual storage")

    # Persist in vault
    doc = {
        "type": "bank",
        "user_id": str(user["_id"]),
        "user_name": holder or user.get("name", ""),
        "user_email": user.get("email", ""),
        "bank_name": data.get("bank_name", ""),
        "account_holder_name": holder,
        "account_type": acc_type,
        "account_last4": account[-4:],
        "routing_encrypted": encrypt(routing),
        "account_encrypted": encrypt(account),
        "stripe_payment_method_id": stripe_pm_id,
        "stripe_customer_id": stripe_customer_id,
        "is_default": bool(data.get("make_default", False)),
        "is_active_for_autopay": False,
        "needs_verification": bool(stripe_pm_id),  # ACH requires micro-deposit verification
        "verified": False,
        "source": "tenant_ach",
        "created_at": datetime.now(timezone.utc),
    }
    if doc["is_default"]:
        await db.payment_methods.update_many(
            {"user_id": str(user["_id"]), "type": "bank"},
            {"$set": {"is_default": False}}
        )
    res = await db.payment_methods.insert_one(doc)

    return {
        "success": True,
        "id": str(res.inserted_id),
        "stripe_payment_method_id": stripe_pm_id,
        "needs_verification": doc["needs_verification"],
        "message": (
            "Cuenta bancaria guardada. Stripe enviará 2 micro-depósitos en 2-3 días "
            "para verificarla antes de usarla en autopago."
            if stripe_pm_id else "Cuenta bancaria guardada (verificación manual)."
        ),
    }
