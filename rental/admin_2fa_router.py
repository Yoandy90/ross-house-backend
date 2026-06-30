"""
Admin 2FA / OTP Router
=======================
Two-step login for the Ross House Rentals admin panel.

Flow:
  1. POST /api/admin/auth/login-step1   {email, password, captcha_token, trusted_device_id?}
        -> If credentials OK and 2FA enabled, sends OTP and returns
           {step: "otp_required", challenge_id, channel, masked}.
        -> If trusted device matches, returns the final token directly
           {step: "complete", token, user, trusted_device_id}.
        -> If 2FA disabled (rare admin override), returns final token directly.

  2. POST /api/admin/auth/login-step2   {challenge_id, code, remember_device?}
        -> Verifies OTP, returns final token + (optional) trusted_device_id valid 30 days.

  3. POST /api/admin/auth/login-step1/resend {challenge_id}
        -> Resends OTP (rate-limited).

  4. GET  /api/admin/auth/2fa-settings   (Bearer admin)
        -> Current preferences.

  5. PATCH /api/admin/auth/2fa-settings   (Bearer admin)
        -> Update channel ("email" | "sms"), enabled flag.

  6. POST /api/admin/auth/trusted-devices/revoke-all  (Bearer admin)
        -> Invalidate all remembered devices.

Collections:
  admin_otp_challenges    — Active challenges (TTL 10m).
  admin_trusted_devices   — Devices that skipped OTP (TTL 30d).
"""
from __future__ import annotations

import os
import logging
import random
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Depends
from dotenv import load_dotenv

from rental.shared import (
    get_db, auth_admin, serialize,
    create_marketplace_token, TENANT_JWT_SECRET,
)
from rental.turnstile_helper import verify_turnstile_token

load_dotenv()
logger = logging.getLogger(__name__)
router = APIRouter()

# ── Config ─────────────────────────────────────────────────────────────────
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_MIN_SECONDS = 30      # min wait between resends
OTP_RESENDS_PER_HOUR = 5
TRUSTED_DEVICE_DAYS = 30
CHALLENGE_ID_BYTES = 32


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    name, dom = email.split("@", 1)
    visible = name[:2] if len(name) > 2 else name[:1]
    return f"{visible}***@{dom}"


def _mask_phone(phone: str) -> str:
    if not phone:
        return "***"
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "***" + digits
    return "***" + digits[-4:]


def _hash_device_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 2FA settings helpers ───────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "enabled": True,
    "channel": "email",       # "email" or "sms"
}


async def _get_settings_for(user_id: str) -> dict:
    db = get_db()
    doc = await db.admin_2fa_settings.find_one({"user_id": user_id})
    if not doc:
        return dict(DEFAULT_SETTINGS)
    return {
        "enabled": doc.get("enabled", DEFAULT_SETTINGS["enabled"]),
        "channel": doc.get("channel", DEFAULT_SETTINGS["channel"]),
    }


# ── OTP delivery ───────────────────────────────────────────────────────────
async def _send_otp_email(to_email: str, code: str, name: str = "Admin") -> bool:
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not sendgrid_key:
        cfg = await get_db().api_config.find_one({"_id": "main"})
        if cfg:
            sendgrid_key = cfg.get("sendgrid_api_key") or cfg.get("SENDGRID_API_KEY")
            from_email = cfg.get("sendgrid_from_email", from_email)
    if not sendgrid_key:
        logger.warning("⚠️ SENDGRID_API_KEY missing — admin OTP email skipped")
        return False
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        html = f"""
        <div style="font-family:Helvetica,Arial,sans-serif;max-width:520px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
          <div style="background:linear-gradient(135deg,#3b82f6,#0ea5e9);padding:14px;border-radius:10px;text-align:center;">
            <h2 style="margin:0;color:#fff;">🔐 Código de acceso · Admin Panel</h2>
          </div>
          <p style="color:#cbd5e1;margin-top:18px;">Hola {name},</p>
          <p style="color:#cbd5e1;">Estás iniciando sesión en el Panel Administrativo de Ross House Rentals. Usa este código para verificar tu identidad.</p>
          <div style="margin:18px 0;padding:18px;background:#111827;border-radius:10px;text-align:center;">
            <div style="font-size:11px;color:#94a3b8;letter-spacing:2px;text-transform:uppercase;">Tu código</div>
            <div style="font-size:34px;font-weight:bold;color:#60a5fa;letter-spacing:6px;font-family:monospace;margin-top:6px;">{code}</div>
            <div style="font-size:11px;color:#64748b;margin-top:8px;">Expira en {OTP_TTL_MINUTES} minutos</div>
          </div>
          <p style="color:#94a3b8;font-size:12px;">⚠️ Si <strong>no</strong> intentaste iniciar sesión, alguien podría conocer tu contraseña. Cámbiala de inmediato.</p>
          <p style="color:#64748b;font-size:11px;margin-top:18px;">— Ross House Rentals · Seguridad</p>
        </div>
        """
        mail = Mail(
            from_email=Email(from_email, "Ross House Rentals Security"),
            to_emails=To(to_email),
            subject=f"🔐 Tu código de admin: {code}",
            plain_text_content=Content("text/plain", f"Tu código admin de Ross House Rentals es: {code}\nExpira en {OTP_TTL_MINUTES} minutos."),
        )
        mail.add_content(Content("text/html", html))
        sg.client.mail.send.post(request_body=mail.get())
        return True
    except Exception as e:
        logger.exception(f"Admin OTP email send failed: {e}")
        return False


async def _send_otp_sms(to_phone: str, code: str) -> bool:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    tok = os.getenv("TWILIO_AUTH_TOKEN")
    from_phone = os.getenv("TWILIO_PHONE_NUMBER", "")
    if not (sid and tok and from_phone):
        cfg = await get_db().api_config.find_one({"_id": "main"})
        if cfg:
            sid = sid or cfg.get("twilio_account_sid")
            tok = tok or cfg.get("twilio_auth_token")
            from_phone = from_phone or cfg.get("twilio_phone_number", "")
    if not (sid and tok and from_phone):
        logger.warning("⚠️ Twilio creds missing — admin OTP SMS skipped")
        return False
    try:
        from twilio.rest import Client
        client = Client(sid, tok)
        msg = client.messages.create(
            body=f"Ross House Admin · Tu codigo: {code}. Expira en {OTP_TTL_MINUTES} min. Si no fuiste tu, ignora.",
            from_=from_phone,
            to=to_phone,
        )
        logger.info(f"✅ Admin OTP SMS sent SID={msg.sid}")
        return True
    except Exception as e:
        logger.exception(f"Admin OTP SMS send failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1 — credentials + (maybe) trigger OTP
# ═══════════════════════════════════════════════════════════════════════════
@router.post("/admin/auth/login-step1")
async def admin_login_step1(request: Request):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    password = (body.get("password") or "").strip()
    captcha_token = body.get("captcha_token")
    trusted_device_id = (body.get("trusted_device_id") or "").strip()

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña son requeridos")

    # CAPTCHA gate (cheap bot filter before we touch DB / bcrypt).
    await verify_turnstile_token(captcha_token, request)

    db = get_db()
    user = await db.app_users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not user or user.get("role") != "admin":
        # Generic message to avoid user enumeration.
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    pwd_hash = user.get("password_hash") or ""
    if not pwd_hash or not _verify_password(password, pwd_hash):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    user_id = str(user["_id"])
    settings = await _get_settings_for(user_id)

    # ── Skip 2FA if user has it disabled (admin override). ──
    if not settings.get("enabled", True):
        token = create_marketplace_token(user_id, email, "admin")
        logger.info(f"[admin-2fa] {email} logged in with 2FA disabled")
        return {
            "step": "complete",
            "token": token,
            "user": _serialize_user(user),
        }

    # ── Skip 2FA if device is trusted. ──
    if trusted_device_id:
        td = await db.admin_trusted_devices.find_one({
            "user_id": user_id,
            "token_hash": _hash_device_token(trusted_device_id),
            "expires_at": {"$gt": _now_utc()},
        })
        if td:
            await db.admin_trusted_devices.update_one(
                {"_id": td["_id"]},
                {"$set": {"last_used_at": _now_utc(),
                          "last_ip": (request.client.host if request.client else None)}},
            )
            token = create_marketplace_token(user_id, email, "admin")
            return {
                "step": "complete",
                "token": token,
                "user": _serialize_user(user),
                "trusted_device_id": trusted_device_id,
            }

    # ── Build OTP challenge ──
    channel = settings.get("channel", "email")
    phone = (user.get("phone") or "").strip()
    if channel == "sms" and not phone:
        # Fallback to email if no phone on file.
        channel = "email"

    code = f"{random.randint(0, 999999):06d}"
    code_hash = bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()
    challenge_id = secrets.token_urlsafe(CHALLENGE_ID_BYTES)
    now = _now_utc()
    expires = now + timedelta(minutes=OTP_TTL_MINUTES)

    await db.admin_otp_challenges.insert_one({
        "challenge_id": challenge_id,
        "user_id": user_id,
        "email": email,
        "channel": channel,
        "code_hash": code_hash,
        "attempts": 0,
        "resends": 0,
        "verified": False,
        "created_at": now,
        "expires_at": expires,
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:200],
    })

    sent_ok = False
    if channel == "email":
        sent_ok = await _send_otp_email(email, code, user.get("name") or "Admin")
        masked = _mask_email(email)
    else:
        sent_ok = await _send_otp_sms(phone, code)
        masked = _mask_phone(phone)

    if not sent_ok:
        # Don't reveal which provider failed; still let client know to retry.
        logger.error(f"[admin-2fa] OTP delivery failed for {email} via {channel}")
        raise HTTPException(status_code=502, detail="No se pudo enviar el código. Intenta de nuevo.")

    logger.info(f"[admin-2fa] OTP issued for {email} via {channel}")

    return {
        "step": "otp_required",
        "challenge_id": challenge_id,
        "channel": channel,
        "masked": masked,
        "expires_in_seconds": OTP_TTL_MINUTES * 60,
    }


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2 — verify OTP, emit token
# ═══════════════════════════════════════════════════════════════════════════
@router.post("/admin/auth/login-step2")
async def admin_login_step2(request: Request):
    body = await request.json()
    challenge_id = (body.get("challenge_id") or "").strip()
    code = (body.get("code") or "").strip().replace(" ", "")
    remember_device = bool(body.get("remember_device"))

    if not challenge_id or not code:
        raise HTTPException(status_code=400, detail="Datos incompletos")

    db = get_db()
    ch = await db.admin_otp_challenges.find_one({"challenge_id": challenge_id})
    if not ch:
        raise HTTPException(status_code=400, detail="Sesión de verificación no encontrada")
    if ch.get("verified"):
        raise HTTPException(status_code=400, detail="Este código ya fue usado")
    if ch.get("expires_at") and ch["expires_at"].replace(tzinfo=timezone.utc) < _now_utc():
        await db.admin_otp_challenges.delete_one({"_id": ch["_id"]})
        raise HTTPException(status_code=400, detail="El código expiró. Vuelve a iniciar sesión.")
    if ch.get("attempts", 0) >= OTP_MAX_ATTEMPTS:
        await db.admin_otp_challenges.delete_one({"_id": ch["_id"]})
        raise HTTPException(status_code=429, detail="Demasiados intentos. Vuelve a iniciar sesión.")

    if not _verify_password(code, ch["code_hash"]):
        await db.admin_otp_challenges.update_one(
            {"_id": ch["_id"]}, {"$inc": {"attempts": 1}}
        )
        remaining = max(0, OTP_MAX_ATTEMPTS - ch.get("attempts", 0) - 1)
        raise HTTPException(status_code=401, detail=f"Código incorrecto. Te quedan {remaining} intentos.")

    # ✅ Match. Mark verified and clean.
    await db.admin_otp_challenges.update_one(
        {"_id": ch["_id"]}, {"$set": {"verified": True, "verified_at": _now_utc()}}
    )

    user = await db.app_users.find_one({"_id": ObjectId(ch["user_id"])})
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=401, detail="Cuenta no válida")

    token = create_marketplace_token(str(user["_id"]), user["email"], "admin")

    trusted_device_id = None
    if remember_device:
        raw = secrets.token_urlsafe(32)
        await db.admin_trusted_devices.insert_one({
            "user_id": str(user["_id"]),
            "token_hash": _hash_device_token(raw),
            "label": (ch.get("user_agent") or "Dispositivo")[:120],
            "created_at": _now_utc(),
            "expires_at": _now_utc() + timedelta(days=TRUSTED_DEVICE_DAYS),
            "last_used_at": _now_utc(),
            "last_ip": request.client.host if request.client else None,
        })
        trusted_device_id = raw

    logger.info(f"[admin-2fa] {user.get('email')} verified successfully")

    return {
        "step": "complete",
        "token": token,
        "user": _serialize_user(user),
        "trusted_device_id": trusted_device_id,
        "trusted_device_days": TRUSTED_DEVICE_DAYS if trusted_device_id else 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# RESEND OTP
# ═══════════════════════════════════════════════════════════════════════════
@router.post("/admin/auth/login-step1/resend")
async def admin_login_resend(request: Request):
    body = await request.json()
    challenge_id = (body.get("challenge_id") or "").strip()
    if not challenge_id:
        raise HTTPException(status_code=400, detail="challenge_id requerido")

    db = get_db()
    ch = await db.admin_otp_challenges.find_one({"challenge_id": challenge_id})
    if not ch:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if ch.get("expires_at") and ch["expires_at"].replace(tzinfo=timezone.utc) < _now_utc():
        raise HTTPException(status_code=400, detail="La sesión expiró. Vuelve a iniciar.")
    if ch.get("resends", 0) >= OTP_RESENDS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Demasiados reenvíos. Espera unos minutos.")
    last_at = ch.get("last_resend_at") or ch.get("created_at")
    if last_at and (_now_utc() - last_at.replace(tzinfo=timezone.utc)).total_seconds() < OTP_RESEND_MIN_SECONDS:
        raise HTTPException(status_code=429, detail=f"Espera {OTP_RESEND_MIN_SECONDS} segundos antes de reenviar.")

    # Generate new code, replace hash.
    code = f"{random.randint(0, 999999):06d}"
    new_hash = bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()
    await db.admin_otp_challenges.update_one(
        {"_id": ch["_id"]},
        {"$set": {
            "code_hash": new_hash,
            "attempts": 0,
            "last_resend_at": _now_utc(),
            "expires_at": _now_utc() + timedelta(minutes=OTP_TTL_MINUTES),
        }, "$inc": {"resends": 1}},
    )

    channel = ch.get("channel", "email")
    user = await db.app_users.find_one({"_id": ObjectId(ch["user_id"])}) or {}
    if channel == "sms":
        ok = await _send_otp_sms(user.get("phone", ""), code)
        masked = _mask_phone(user.get("phone", ""))
    else:
        ok = await _send_otp_email(user.get("email", ""), code, user.get("name") or "Admin")
        masked = _mask_email(user.get("email", ""))

    if not ok:
        raise HTTPException(status_code=502, detail="No se pudo reenviar")

    return {"success": True, "channel": channel, "masked": masked, "expires_in_seconds": OTP_TTL_MINUTES * 60}


# ═══════════════════════════════════════════════════════════════════════════
# 2FA SETTINGS (admin self-serve)
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/admin/auth/2fa-settings")
async def get_2fa_settings(admin=Depends(auth_admin)):
    user_id = str(admin["_id"]) if isinstance(admin.get("_id"), ObjectId) else admin["_id"]
    s = await _get_settings_for(user_id)
    db = get_db()
    devices_count = await db.admin_trusted_devices.count_documents(
        {"user_id": user_id, "expires_at": {"$gt": _now_utc()}}
    )
    return {
        "enabled": s["enabled"],
        "channel": s["channel"],
        "phone_on_file": bool(admin.get("phone")),
        "email": admin.get("email"),
        "trusted_devices_count": devices_count,
        "available_channels": ["email"] + (["sms"] if admin.get("phone") else []),
    }


@router.patch("/admin/auth/2fa-settings")
async def update_2fa_settings(request: Request, admin=Depends(auth_admin)):
    body = await request.json()
    user_id = str(admin["_id"]) if isinstance(admin.get("_id"), ObjectId) else admin["_id"]
    update: dict = {}

    if "channel" in body:
        ch = (body["channel"] or "").strip().lower()
        if ch not in ("email", "sms"):
            raise HTTPException(status_code=400, detail="Canal inválido (email o sms)")
        if ch == "sms" and not admin.get("phone"):
            raise HTTPException(status_code=400, detail="Agrega tu número de teléfono antes de activar SMS")
        update["channel"] = ch

    if "enabled" in body:
        enabled = bool(body["enabled"])
        update["enabled"] = enabled

    if not update:
        raise HTTPException(status_code=400, detail="Nada para actualizar")

    update["updated_at"] = _now_utc()
    await get_db().admin_2fa_settings.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, **update}},
        upsert=True,
    )
    return await _get_settings_for(user_id) | {"saved": True}


@router.post("/admin/auth/trusted-devices/revoke-all")
async def revoke_all_trusted(admin=Depends(auth_admin)):
    user_id = str(admin["_id"]) if isinstance(admin.get("_id"), ObjectId) else admin["_id"]
    res = await get_db().admin_trusted_devices.delete_many({"user_id": user_id})
    return {"success": True, "revoked": res.deleted_count}


# ═══════════════════════════════════════════════════════════════════════════
# Indexes
# ═══════════════════════════════════════════════════════════════════════════
async def ensure_indexes(db) -> None:
    try:
        await db.admin_otp_challenges.create_index("challenge_id", unique=True)
        await db.admin_otp_challenges.create_index("expires_at", expireAfterSeconds=0)
        await db.admin_trusted_devices.create_index([("user_id", 1), ("token_hash", 1)])
        await db.admin_trusted_devices.create_index("expires_at", expireAfterSeconds=0)
        await db.admin_2fa_settings.create_index("user_id", unique=True)
        logger.info("  ✅ Admin 2FA indexes created")
    except Exception as e:
        logger.warning(f"Admin 2FA index creation: {e}")


# ─── helpers ───────────────────────────────────────────────────────────────
def _serialize_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", "admin"),
        "phone": user.get("phone", ""),
        "has_password": bool(user.get("password_hash")),
    }
