"""API Keys Manager — store 3rd-party API keys in MongoDB (encrypted with
Fernet) so the admin can rotate them from the Admin Panel WITHOUT rebuilding
or redeploying the apps.

How it works:
  1. On boot, `load_db_keys_into_env()` (sync, pymongo) reads the encrypted
     keys from `admin_config` {type:"api_keys"} and injects them into
     os.environ BEFORE routers are imported → every existing os.getenv()
     call in the codebase transparently picks up the DB value.
  2. When the admin saves a key via PUT, we encrypt + store it in the DB AND
     update os.environ immediately → the new key takes effect live, no
     restart needed (all managed integrations read env at request time).
  3. DELETE removes the DB override and restores the original .env value.

Values are encrypted at rest with the same VAULT_ENCRYPTION_KEY used by the
payment vault. All changes are audited to `vault_audit_log`.
"""
import os
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException

from .shared import get_db, auth_admin
from .vault_router import encrypt, decrypt, mask, _audit

router = APIRouter()
logger = logging.getLogger(__name__)

CONFIG_TYPE = "api_keys"

# ── Registry of manageable keys ─────────────────────────────────────────────
# secret=True → value is masked in list responses and rendered as password.
KEY_REGISTRY = [
    # SendGrid (Emails)
    {"key": "SENDGRID_API_KEY", "label": "SendGrid API Key", "category": "SendGrid (Emails)", "secret": True, "placeholder": "SG.xxxxx..."},
    {"key": "SENDGRID_FROM_EMAIL", "label": "Email Remitente", "category": "SendGrid (Emails)", "secret": False, "placeholder": "info@rosshouserentals.com"},
    # Twilio (SMS)
    {"key": "TWILIO_ACCOUNT_SID", "label": "Account SID", "category": "Twilio (SMS)", "secret": False, "placeholder": "ACxxxxxxxx..."},
    {"key": "TWILIO_AUTH_TOKEN", "label": "Auth Token", "category": "Twilio (SMS)", "secret": True, "placeholder": "xxxxxxxx..."},
    {"key": "TWILIO_PHONE_NUMBER", "label": "Número de Teléfono", "category": "Twilio (SMS)", "secret": False, "placeholder": "+18069342018"},
    # Lob (Direct Mail)
    {"key": "LOB_API_KEY", "label": "Lob API Key (live)", "category": "Lob (Correo Directo)", "secret": True, "placeholder": "live_xxxxxxxx..."},
    # Plaid (Bancos)
    {"key": "PLAID_CLIENT_ID", "label": "Client ID", "category": "Plaid (Bancos)", "secret": False, "placeholder": "xxxxxxxx"},
    {"key": "PLAID_SECRET", "label": "Secret", "category": "Plaid (Bancos)", "secret": True, "placeholder": "xxxxxxxx"},
    {"key": "PLAID_ENV", "label": "Environment (sandbox/production)", "category": "Plaid (Bancos)", "secret": False, "placeholder": "sandbox"},
    # TikTok
    {"key": "TIKTOK_CLIENT_KEY", "label": "Client Key", "category": "TikTok (Marketing)", "secret": False, "placeholder": "sbawxxxxxxxx"},
    {"key": "TIKTOK_CLIENT_SECRET", "label": "Client Secret", "category": "TikTok (Marketing)", "secret": True, "placeholder": "xxxxxxxx"},
    # Facebook / Meta
    {"key": "META_APP_ID", "label": "App ID", "category": "Facebook (Meta)", "secret": False, "placeholder": "27927083xxxxxxx"},
    {"key": "META_APP_SECRET", "label": "App Secret", "category": "Facebook (Meta)", "secret": True, "placeholder": "xxxxxxxx"},
    {"key": "META_CONFIG_ID", "label": "Configuration ID (Login for Business)", "category": "Facebook (Meta)", "secret": False, "placeholder": "15754333xxxxxxx"},
    # OpenAI
    {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "category": "OpenAI (IA)", "secret": True, "placeholder": "sk-xxxxxxxx..."},
    # Emergent LLM
    {"key": "EMERGENT_LLM_KEY", "label": "Emergent Universal Key", "category": "Emergent (IA / Escáner de Recibos)", "secret": True, "placeholder": "sk-emergent-xxxxxxxx"},
    # Mashvisor / RapidAPI
    {"key": "RAPIDAPI_KEY", "label": "RapidAPI Key (Mashvisor)", "category": "Mashvisor (Análisis de Mercado)", "secret": True, "placeholder": "xxxxxxxx"},
    # Tracerfy (Skip Tracing)
    {"key": "TRACERFY_API_KEY", "label": "Tracerfy API Key", "category": "Tracerfy (Skip Tracing — contacto de dueños)", "secret": True, "placeholder": "xxxxxxxx"},
    # Expo push
    {"key": "EXPO_ACCESS_TOKEN", "label": "Expo Access Token (Push)", "category": "Expo (Notificaciones Push)", "secret": True, "placeholder": "xxxxxxxx"},
]

_REGISTRY_MAP = {k["key"]: k for k in KEY_REGISTRY}

# Snapshot of the ORIGINAL environment (.env values) taken at import time,
# BEFORE any DB values are injected. Used to restore fallbacks on DELETE.
_ORIGINAL_ENV = {k["key"]: os.environ.get(k["key"]) for k in KEY_REGISTRY}


# ════════════════════════════════════════════════════════════════════════════
# Boot-time loader (sync — called from server.py before routers import)
# ════════════════════════════════════════════════════════════════════════════

def load_db_keys_into_env(mongo_url: str, db_name: str) -> int:
    """Read encrypted keys from admin_config and inject into os.environ.
    Returns the number of keys loaded. Safe to call multiple times."""
    global _LOAD_STATS
    from pymongo import MongoClient
    from cryptography.fernet import Fernet

    raw_key = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not raw_key:
        logger.warning("VAULT_ENCRYPTION_KEY missing — DB API keys not loaded")
        _LOAD_STATS = {"vault_key_present": False, "loaded": 0, "failed_decrypt": 0}
        return 0

    cipher = Fernet(raw_key.encode())
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    try:
        doc = client[db_name].admin_config.find_one({"type": CONFIG_TYPE}) or {}
        loaded = 0
        failed = 0
        for key_name, enc_value in (doc.get("keys") or {}).items():
            if key_name not in _REGISTRY_MAP or not enc_value:
                continue
            try:
                value = cipher.decrypt(enc_value.encode()).decode()
            except Exception:
                logger.warning(f"API key {key_name}: decryption failed — skipped")
                failed += 1
                continue
            if value:
                os.environ[key_name] = value
                loaded += 1
        if loaded:
            logger.info(f"🔑 {loaded} API key(s) loaded from DB into environment")
        _LOAD_STATS = {"vault_key_present": True, "loaded": loaded, "failed_decrypt": failed}
        return loaded
    finally:
        client.close()


_LOAD_STATS = {"vault_key_present": None, "loaded": 0, "failed_decrypt": 0}


def get_last_load_stats() -> dict:
    return dict(_LOAD_STATS)


# ════════════════════════════════════════════════════════════════════════════
# Admin endpoints
# ════════════════════════════════════════════════════════════════════════════

@router.get("/admin/api-keys")
async def list_api_keys(request: Request):
    """All manageable keys, grouped by service, with masked values and source."""
    await auth_admin(request)
    db = get_db()
    doc = await db.admin_config.find_one({"type": CONFIG_TYPE}) or {}
    db_keys = doc.get("keys") or {}
    meta = doc.get("meta") or {}

    groups: dict = {}
    for entry in KEY_REGISTRY:
        k = entry["key"]
        if db_keys.get(k):
            value = decrypt(db_keys[k])
            source = "db" if value else "error"
        else:
            value = _ORIGINAL_ENV.get(k) or ""
            source = "env" if value else "missing"

        item = {
            "key": k,
            "label": entry["label"],
            "secret": entry["secret"],
            "placeholder": entry["placeholder"],
            "source": source,
            "has_value": bool(value),
            "masked": mask(value, visible=4) if entry["secret"] else value,
            "updated_at": (meta.get(k) or {}).get("at"),
            "updated_by": (meta.get(k) or {}).get("by"),
        }
        groups.setdefault(entry["category"], []).append(item)

    return {
        "success": True,
        "groups": [{"category": c, "keys": items} for c, items in groups.items()],
    }


@router.put("/admin/api-keys/{key_name}")
async def save_api_key(key_name: str, request: Request):
    """Encrypt + save a key to the DB and apply it live (os.environ).
    Takes effect immediately — no rebuild or restart needed."""
    admin = await auth_admin(request)
    entry = _REGISTRY_MAP.get(key_name)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Key desconocida: {key_name}")

    data = await request.json()
    value = (data.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="El valor no puede estar vacío")
    if len(value) > 2000:
        raise HTTPException(status_code=400, detail="Valor demasiado largo")

    db = get_db()
    now = datetime.now(timezone.utc)
    await db.admin_config.update_one(
        {"type": CONFIG_TYPE},
        {"$set": {
            f"keys.{key_name}": encrypt(value),
            f"meta.{key_name}": {"at": now, "by": admin.get("email", "")},
            "updated_at": now,
        }},
        upsert=True,
    )

    # Apply live — every os.getenv() call picks this up on the next request.
    os.environ[key_name] = value

    await _audit(db, admin.get("email", ""), "api_key_updated", target=key_name,
                 meta={"last4": value[-4:] if len(value) > 4 else "****"})
    logger.info(f"🔑 API key {key_name} rotated by {admin.get('email', '')} (live)")

    return {
        "success": True,
        "message": f"{entry['label']} guardada y aplicada en vivo — no se necesita rebuild.",
        "masked": mask(value, visible=4) if entry["secret"] else value,
        "source": "db",
    }


@router.get("/admin/api-keys/{key_name}/reveal")
async def reveal_api_key(key_name: str, request: Request):
    """Reveal the full current value of a key (audited)."""
    admin = await auth_admin(request)
    if key_name not in _REGISTRY_MAP:
        raise HTTPException(status_code=404, detail=f"Key desconocida: {key_name}")

    db = get_db()
    doc = await db.admin_config.find_one({"type": CONFIG_TYPE}) or {}
    enc = (doc.get("keys") or {}).get(key_name)
    value = decrypt(enc) if enc else (_ORIGINAL_ENV.get(key_name) or "")
    if not value:
        raise HTTPException(status_code=404, detail="Esta key no está configurada")

    await _audit(db, admin.get("email", ""), "api_key_revealed", target=key_name)
    return {"success": True, "key": key_name, "value": value}


@router.delete("/admin/api-keys/{key_name}")
async def delete_api_key(key_name: str, request: Request):
    """Remove the DB override and revert to the original .env value (if any)."""
    admin = await auth_admin(request)
    if key_name not in _REGISTRY_MAP:
        raise HTTPException(status_code=404, detail=f"Key desconocida: {key_name}")

    db = get_db()
    await db.admin_config.update_one(
        {"type": CONFIG_TYPE},
        {"$unset": {f"keys.{key_name}": "", f"meta.{key_name}": ""}},
    )

    # Restore the original .env value (or clear entirely)
    original = _ORIGINAL_ENV.get(key_name)
    if original:
        os.environ[key_name] = original
    else:
        os.environ.pop(key_name, None)

    await _audit(db, admin.get("email", ""), "api_key_deleted", target=key_name)
    return {
        "success": True,
        "message": "Override eliminado — se restauró el valor original del .env" if original
        else "Key eliminada — ya no hay valor configurado",
        "source": "env" if original else "missing",
    }
