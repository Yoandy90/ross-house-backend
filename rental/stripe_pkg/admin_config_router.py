"""Admin Stripe config endpoints — credential masking, payment-methods config, test connection."""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin
from rental.stripe_pkg.helpers import _get_stripe_config

router = APIRouter()


@router.get('/admin/rental-stripe-config')
async def get_stripe_config(request: Request):
    """Admin: Get Stripe configuration (masked keys) for rental module"""
    await auth_admin(request)
    config = await get_db().rental_config.find_one({"type": "company"}) or {}

    secret = config.get("stripe_secret_key", "")
    pub = config.get("stripe_publishable_key", "")

    return {
        "success": True,
        "stripe_enabled": config.get("stripe_enabled", False),
        "stripe_secret_key_masked": f"sk_...{secret[-8:]}" if len(secret) > 8 else ("Configurado" if secret else ""),
        "stripe_publishable_key": pub,
        "has_secret_key": bool(secret),
        "has_publishable_key": bool(pub),
        "payment_methods": config.get("payment_methods", {}),
        "default_late_fee_amount": float(config.get("default_late_fee_amount", 50.0)),
        "default_late_fee_grace_days": int(config.get("default_late_fee_grace_days", 5)),
    }


@router.put('/admin/rental-payment-methods')
async def update_payment_methods(request: Request):
    """Admin: Update payment methods configuration (Zelle, CashApp, Bank, Stripe).

    Body example:
    {
      "stripe_enabled": true,
      "stripe_publishable_key": "pk_live_...",
      "stripe_secret_key": "sk_live_...",
      "payment_methods": {
        "zelle":     { "enabled": true, "email": "...", "phone": "...", "name": "..." },
        "cashapp":   { "enabled": true, "tag": "$..." },
        "venmo":     { "enabled": true, "username": "@..." },
        "bank_transfer": { "enabled": true, "bank_name": "...", "account_name": "...", "routing": "...", "account_last4": "...", "instructions": "..." },
        "money_order": { "enabled": true, "payable_to": "...", "mail_to": "...", "office_address": "..." }
      }
    }
    """
    await auth_admin(request)
    data = await request.json()

    update = {"updated_at": datetime.utcnow()}

    # Top-level Stripe toggle
    if "stripe_enabled" in data:
        update["stripe_enabled"] = bool(data["stripe_enabled"])

    # ── Default late fee (company-wide) ──
    if "default_late_fee_amount" in data:
        try:
            update["default_late_fee_amount"] = float(data["default_late_fee_amount"])
        except (TypeError, ValueError):
            pass
    if "default_late_fee_grace_days" in data:
        try:
            update["default_late_fee_grace_days"] = int(data["default_late_fee_grace_days"])
        except (TypeError, ValueError):
            pass

    # Stripe credentials (allow updating individually; ignore empty/masked values)
    if data.get("stripe_publishable_key"):
        pk = str(data["stripe_publishable_key"]).strip()
        if pk and not pk.startswith("pk_...") and "..." not in pk[:5]:
            update["stripe_publishable_key"] = pk
    if data.get("stripe_secret_key"):
        sk = str(data["stripe_secret_key"]).strip()
        if sk and not sk.startswith("sk_...") and "..." not in sk[:5]:
            update["stripe_secret_key"] = sk

    # Payment methods (full replace for simplicity — admin sends complete object)
    if "payment_methods" in data and isinstance(data["payment_methods"], dict):
        # Sanitize: keep only known methods + known fields per method
        allowed = {
            "zelle":         {"enabled", "email", "phone", "name", "notes"},
            "cashapp":       {"enabled", "tag", "name", "notes"},
            "venmo":         {"enabled", "username", "name", "notes"},
            "bank_transfer": {"enabled", "bank_name", "account_name", "routing", "account_last4", "instructions", "notes"},
            "money_order":   {"enabled", "payable_to", "mail_to", "office_address", "notes"},
            "check":         {"enabled", "payable_to", "mail_to", "office_address", "notes"},
            "cash":          {"enabled", "office_address", "office_hours", "notes"},
        }
        sanitized = {}
        for method_key, fields in data["payment_methods"].items():
            if method_key not in allowed or not isinstance(fields, dict):
                continue
            clean = {}
            for k, v in fields.items():
                if k in allowed[method_key]:
                    if k == "enabled":
                        clean[k] = bool(v)
                    else:
                        clean[k] = str(v) if v is not None else ""
            sanitized[method_key] = clean
        update["payment_methods"] = sanitized

    await get_db().rental_config.update_one(
        {"type": "company"},
        {"$set": update},
        upsert=True,
    )

    # Return masked config
    fresh = await get_db().rental_config.find_one({"type": "company"}) or {}
    secret = fresh.get("stripe_secret_key", "")
    return {
        "success": True,
        "message": "Configuración de métodos de pago actualizada",
        "stripe_enabled": fresh.get("stripe_enabled", False),
        "stripe_publishable_key": fresh.get("stripe_publishable_key", ""),
        "stripe_secret_key_masked": f"sk_...{secret[-8:]}" if len(secret) > 8 else ("Configurado" if secret else ""),
        "payment_methods": fresh.get("payment_methods", {}),
    }


@router.get('/admin/stripe/test-connection')
async def admin_test_stripe_connection(request: Request):
    """Admin: Test if Stripe keys are valid"""
    await auth_admin(request)
    try:
        config = await _get_stripe_config()
        secret_key = config.get("stripe_secret_key", "")
        if not secret_key:
            return {"success": False, "error": "No hay clave secreta configurada"}

        import stripe
        stripe.api_key = secret_key
        account = stripe.Account.retrieve()
        return {
            "success": True,
            "account_id": account.id,
            "business_name": getattr(account, 'business_profile', {}).get('name', ''),
            "charges_enabled": account.charges_enabled,
            "payouts_enabled": account.payouts_enabled,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
