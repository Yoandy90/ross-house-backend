"""
App Adoption Router
═══════════════════════════════════════════════════════════════════════════════
Tracks which tenants have downloaded / are using the iOS mobile app.

Signals used (in order of confidence):
1. `push_token` present on app_users → app installed & logged in
2. `last_login` recency → active user
3. `push_platform` → ios vs android vs web
"""
import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from rental.shared import auth_admin, get_db, serialize

logger = logging.getLogger("app_adoption")
router = APIRouter(prefix="/admin/app-adoption", tags=["App Adoption"])

IOS_URL = "https://apps.apple.com/us/app/ross-house/id6775734340"
ANDROID_URL: Optional[str] = None  # fill when Play Store is live


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def _classify(user: dict) -> str:
    """Return one of: 'has_app_active', 'has_app_stale', 'web_only', 'inactive'."""
    push_token = (user.get("push_token") or "").strip()
    last_login = user.get("last_login")

    now = datetime.now(timezone.utc)

    def _to_utc(dt):
        if not dt:
            return None
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except Exception:
                return None
        if isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    last_login = _to_utc(last_login)

    if push_token:
        # Has app installed. Check if actively using it
        token_updated = _to_utc(user.get("push_token_updated_at"))
        # "active" = token refreshed in last 30 days
        if token_updated and (now - token_updated) < timedelta(days=30):
            return "has_app_active"
        return "has_app_stale"

    if last_login and (now - last_login) < timedelta(days=90):
        return "web_only"

    return "inactive"


async def _send_sms_direct(to_phone: str, body: str) -> bool:
    """Direct Twilio SMS helper (mirrors the one in service_providers_router)."""
    try:
        sid = os.environ.get("TWILIO_ACCOUNT_SID")
        token = os.environ.get("TWILIO_AUTH_TOKEN")
        from_phone = os.environ.get("TWILIO_PHONE_NUMBER") or os.environ.get("TWILIO_FROM_NUMBER")
        if not (sid and token and from_phone):
            logger.warning("[app-adoption] Twilio not configured — SMS skipped")
            return False
        from twilio.rest import Client
        if not to_phone.startswith("+"):
            to_phone = "+1" + re.sub(r"\D", "", to_phone)
        Client(sid, token).messages.create(body=body[:1500], from_=from_phone, to=to_phone)
        return True
    except Exception as e:
        logger.exception(f"[app-adoption] sms send failed: {e}")
        return False


async def _send_email_direct(to_email: str, subject: str, html: str) -> bool:
    """Direct SendGrid helper (mirrors pm_waitlist_router)."""
    try:
        api_key = os.environ.get("SENDGRID_API_KEY")
        from_email = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
        if not api_key:
            logger.warning("[app-adoption] SENDGRID_API_KEY missing, skipping email")
            return False
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        msg = Mail(
            from_email=(from_email, "Ross House Rentals"),
            to_emails=to_email,
            subject=subject,
            html_content=html,
        )
        sg = SendGridAPIClient(api_key)
        resp = sg.send(msg)
        return 200 <= resp.status_code < 300
    except Exception as e:
        logger.exception(f"[app-adoption] email send failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════
@router.get("/stats")
async def app_adoption_stats(
    request: Request,
    role: str = Query(default="tenant", description="Filter by role: tenant, buyer, landlord, all"),
):
    """Aggregate KPIs about app adoption."""
    await auth_admin(request)
    db = get_db()

    query: dict = {}
    if role != "all":
        query["role"] = role

    total = await db.app_users.count_documents(query)

    has_app = await db.app_users.count_documents({**query, "push_token": {"$exists": True, "$nin": [None, ""]}})
    ios_users = await db.app_users.count_documents({**query, "push_platform": "ios", "push_token": {"$exists": True, "$nin": [None, ""]}})
    android_users = await db.app_users.count_documents({**query, "push_platform": "android", "push_token": {"$exists": True, "$nin": [None, ""]}})

    # Active in last 30 days
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    active_last_30d = await db.app_users.count_documents({
        **query,
        "push_token": {"$exists": True, "$nin": [None, ""]},
        "push_token_updated_at": {"$gte": thirty_days_ago},
    })

    # Never logged in
    never_logged_in = await db.app_users.count_documents({**query, "last_login": {"$in": [None]}})

    web_only = total - has_app - never_logged_in
    web_only = max(0, web_only)

    adoption_rate = round((has_app / total * 100), 1) if total > 0 else 0.0

    return {
        "status": "success",
        "role_filter": role,
        "totals": {
            "total_users": total,
            "has_app": has_app,
            "ios_users": ios_users,
            "android_users": android_users,
            "active_last_30d": active_last_30d,
            "web_only": web_only,
            "inactive": never_logged_in,
            "adoption_rate_pct": adoption_rate,
        },
    }


@router.get("/users")
async def app_adoption_users(
    request: Request,
    role: str = Query(default="tenant"),
    status: str = Query(default="all", description="all, has_app_active, has_app_stale, web_only, inactive"),
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """List users with adoption metadata."""
    await auth_admin(request)
    db = get_db()

    query: dict = {}
    if role != "all":
        query["role"] = role

    if search:
        s = re.escape(search.strip())
        query["$or"] = [
            {"email": {"$regex": s, "$options": "i"}},
            {"name": {"$regex": s, "$options": "i"}},
            {"phone": {"$regex": s, "$options": "i"}},
        ]

    cursor = db.app_users.find(query).sort("last_login", -1)
    all_docs = await cursor.to_list(length=5000)

    # Enrich + classify in-memory (dataset is small)
    enriched = []
    for u in all_docs:
        cls = _classify(u)
        if status != "all" and cls != status:
            continue
        enriched.append({
            "id": str(u.get("_id")),
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "role": u.get("role", ""),
            "status": cls,
            "has_push_token": bool((u.get("push_token") or "").strip()),
            "push_platform": u.get("push_platform", ""),
            "push_device_name": u.get("push_device_name", ""),
            "push_token_updated_at": u.get("push_token_updated_at").isoformat() if isinstance(u.get("push_token_updated_at"), datetime) else None,
            "last_login": u.get("last_login").isoformat() if isinstance(u.get("last_login"), datetime) else None,
            "created_at": u.get("created_at").isoformat() if isinstance(u.get("created_at"), datetime) else None,
        })

    total_filtered = len(enriched)
    start = (page - 1) * page_size
    end = start + page_size
    paged = enriched[start:end]

    return {
        "status": "success",
        "users": paged,
        "total": total_filtered,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_filtered + page_size - 1) // page_size,
    }


class InvitePayload(BaseModel):
    user_id: str
    channel: str = "sms"  # sms | email | both
    custom_message: Optional[str] = None


@router.post("/send-invite")
async def send_app_invite(payload: InvitePayload, request: Request):
    """Send the App Store download link to a specific user via SMS or email."""
    admin = await auth_admin(request)
    db = get_db()

    from bson import ObjectId
    try:
        oid = ObjectId(payload.user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="user_id inválido")

    user = await db.app_users.find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    first_name = (user.get("name") or "").split(" ")[0] or "amigo"

    default_msg = (
        f"Hola {first_name}, soy de Ross House Rentals. "
        f"Descarga nuestra app iOS para pagar renta, ver tu contrato y solicitar mantenimiento: "
        f"{IOS_URL}?utm_source=sms&utm_medium=admin_invite&utm_campaign=app_adoption"
    )
    message = (payload.custom_message or default_msg).strip()

    result = {"sms_sent": False, "email_sent": False}

    # ── SMS ──
    if payload.channel in ("sms", "both"):
        phone = (user.get("phone") or "").strip()
        if phone:
            result["sms_sent"] = await _send_sms_direct(phone, message)
        else:
            logger.info(f"[app-adoption] no phone for user {payload.user_id}")

    # ── Email ──
    if payload.channel in ("email", "both"):
        email = (user.get("email") or "").strip()
        if email:
            try:
                subject = "📱 Descarga la app de Ross House Rentals"
                html = f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width:600px; margin:auto;">
                  <p>Hola {first_name},</p>
                  <p>Tu portal de inquilino ahora está disponible como app nativa para iPhone. Con ella puedes:</p>
                  <ul>
                    <li>Pagar renta en 2 toques</li>
                    <li>Enviar solicitudes de mantenimiento con foto</li>
                    <li>Ver tu contrato y recibos</li>
                    <li>Recibir avisos importantes al instante</li>
                  </ul>
                  <p style="text-align:center; margin: 32px 0;">
                    <a href="{IOS_URL}?utm_source=email&utm_medium=admin_invite&utm_campaign=app_adoption"
                       style="background:#1a73e8; color:#fff; padding:14px 28px; border-radius:8px; text-decoration:none; font-weight:bold;">
                      Descargar en App Store
                    </a>
                  </p>
                  <p style="color:#666; font-size:12px;">Ross House Rentals LLC · Dumas, TX</p>
                </div>
                """
                result["email_sent"] = await _send_email_direct(email, subject, html)
            except Exception as e:
                logger.exception(f"[app-adoption] email send failed: {e}")

    # ── Audit ──
    await db.app_adoption_invites.insert_one({
        "user_id": payload.user_id,
        "user_email": user.get("email", ""),
        "user_phone": user.get("phone", ""),
        "channel": payload.channel,
        "sms_sent": result["sms_sent"],
        "email_sent": result["email_sent"],
        "sent_by_admin": admin.get("email", ""),
        "sent_at": datetime.now(timezone.utc),
    })

    return {"status": "success", **result}


@router.get("/export.csv")
async def export_adoption_csv(
    request: Request,
    role: str = Query(default="tenant"),
    status: str = Query(default="all"),
):
    """Export filtered users as CSV — handy for Facebook Ads Custom Audiences."""
    from fastapi.responses import Response
    import csv
    import io

    await auth_admin(request)
    db = get_db()

    query: dict = {}
    if role != "all":
        query["role"] = role

    all_docs = await db.app_users.find(query).sort("last_login", -1).to_list(length=5000)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "name", "email", "phone", "role", "status",
        "push_platform", "device", "last_app_open", "last_login", "created_at",
    ])

    for u in all_docs:
        cls = _classify(u)
        if status != "all" and cls != status:
            continue
        w.writerow([
            u.get("name", ""),
            u.get("email", ""),
            u.get("phone", ""),
            u.get("role", ""),
            cls,
            u.get("push_platform", ""),
            u.get("push_device_name", ""),
            u.get("push_token_updated_at").isoformat() if isinstance(u.get("push_token_updated_at"), datetime) else "",
            u.get("last_login").isoformat() if isinstance(u.get("last_login"), datetime) else "",
            u.get("created_at").isoformat() if isinstance(u.get("created_at"), datetime) else "",
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=app_adoption_{role}_{status}.csv"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — BULK PUSH, TIMELINE, RE-ENGAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

class BulkPushPayload(BaseModel):
    role: str = "tenant"          # tenant, buyer, landlord, all
    status: str = "has_app_stale"  # target segment
    title: str
    body: str
    deep_link: Optional[str] = None  # optional deep-link path for the app


@router.post("/bulk-push")
async def bulk_push_to_segment(payload: BulkPushPayload, request: Request):
    """
    Send an Expo push notification to all users matching a segment.
    Only users with a valid push_token can receive it.
    """
    admin = await auth_admin(request)
    db = get_db()

    if not payload.title.strip() or not payload.body.strip():
        raise HTTPException(status_code=400, detail="title y body son requeridos")

    query: dict = {"push_token": {"$exists": True, "$nin": [None, ""]}}
    if payload.role != "all":
        query["role"] = payload.role

    users = await db.app_users.find(query).to_list(length=5000)

    if payload.status != "all":
        users = [u for u in users if _classify(u) == payload.status]

    if not users:
        return {"status": "success", "sent": 0, "failed": 0, "message": "No users match segment"}

    sent = 0
    failed = 0
    from push_notification_service import send_push_notification

    for u in users:
        token = (u.get("push_token") or "").strip()
        if not token:
            failed += 1
            continue
        try:
            await send_push_notification(
                expo_push_token=token,
                title=payload.title.strip()[:60],
                body=payload.body.strip()[:200],
                data={
                    "type": "admin_broadcast",
                    "deep_link": payload.deep_link or "",
                    "campaign": "app_adoption_bulk",
                },
            )
            sent += 1
        except Exception as e:
            logger.warning(f"[app-adoption] bulk push to {u.get('email')} failed: {e}")
            failed += 1

    # Audit
    await db.app_adoption_broadcasts.insert_one({
        "role": payload.role,
        "status": payload.status,
        "title": payload.title,
        "body": payload.body,
        "deep_link": payload.deep_link,
        "target_count": len(users),
        "sent": sent,
        "failed": failed,
        "sent_by_admin": admin.get("email", ""),
        "sent_at": datetime.now(timezone.utc),
    })

    return {"status": "success", "sent": sent, "failed": failed, "target_count": len(users)}


@router.get("/broadcasts")
async def list_broadcasts(request: Request, limit: int = Query(default=20, le=100)):
    """List last N broadcasts (audit log)."""
    await auth_admin(request)
    db = get_db()
    docs = await db.app_adoption_broadcasts.find().sort("sent_at", -1).limit(limit).to_list(None)
    for d in docs:
        d["id"] = str(d.pop("_id"))
        if isinstance(d.get("sent_at"), datetime):
            d["sent_at"] = d["sent_at"].isoformat()
    return {"status": "success", "broadcasts": docs}


@router.get("/users/{user_id}/timeline")
async def user_timeline(user_id: str, request: Request):
    """
    Aggregate a per-user activity timeline from multiple sources.
    Sources: created_at, last_login, push_token, invites, broadcasts received, payments.
    """
    await auth_admin(request)
    db = get_db()

    from bson import ObjectId
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="user_id inválido")

    user = await db.app_users.find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    events = []

    # 1. Account created
    if user.get("created_at"):
        events.append({
            "ts": user["created_at"].isoformat() if isinstance(user["created_at"], datetime) else user["created_at"],
            "type": "account_created",
            "icon": "user_plus",
            "title": "Cuenta creada",
            "detail": user.get("email", ""),
        })

    # 2. Push token registered (first time app opened)
    if user.get("push_token_updated_at"):
        events.append({
            "ts": user["push_token_updated_at"].isoformat() if isinstance(user["push_token_updated_at"], datetime) else user["push_token_updated_at"],
            "type": "app_opened",
            "icon": "smartphone",
            "title": "App abierta",
            "detail": f"{user.get('push_platform', 'ios')} · {user.get('push_device_name', 'Dispositivo')}",
        })

    # 3. Last login
    if user.get("last_login"):
        events.append({
            "ts": user["last_login"].isoformat() if isinstance(user["last_login"], datetime) else user["last_login"],
            "type": "login",
            "icon": "log_in",
            "title": "Último login",
            "detail": "Web o app",
        })

    # 4. Invites sent
    invites = await db.app_adoption_invites.find({"user_id": user_id}).sort("sent_at", -1).limit(50).to_list(None)
    for inv in invites:
        channels = []
        if inv.get("sms_sent"):
            channels.append("SMS")
        if inv.get("email_sent"):
            channels.append("Email")
        events.append({
            "ts": inv["sent_at"].isoformat() if isinstance(inv.get("sent_at"), datetime) else "",
            "type": "invite_sent",
            "icon": "send",
            "title": f"Invitación por {', '.join(channels) if channels else inv.get('channel', 'canal desconocido')}",
            "detail": f"Enviado por {inv.get('sent_by_admin', 'admin')}",
        })

    # 5. Rental payments (only for tenants)
    if user.get("role") == "tenant":
        try:
            payments = await db.rental_payments.find({"tenant_id": user_id}).sort("payment_date", -1).limit(20).to_list(None)
            for p in payments:
                events.append({
                    "ts": p["payment_date"].isoformat() if isinstance(p.get("payment_date"), datetime) else "",
                    "type": "payment",
                    "icon": "dollar",
                    "title": f"Pago de renta · ${p.get('total_paid', p.get('amount', 0)):.0f}",
                    "detail": f"{p.get('period_month', '')} {p.get('period_year', '')} · {p.get('payment_method', 'unknown')}",
                })
        except Exception as e:
            logger.warning(f"[timeline] payments query failed: {e}")

    # Sort by timestamp desc
    events = [e for e in events if e.get("ts")]
    events.sort(key=lambda x: x["ts"], reverse=True)

    return {
        "status": "success",
        "user": {
            "id": str(user["_id"]),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
            "role": user.get("role", ""),
            "status": _classify(user),
        },
        "events": events,
        "total_events": len(events),
    }


# ── Re-engagement email config (stored in settings collection) ─────────────
REENGAGE_CFG_ID = "app_adoption_reengagement"


class ReengageConfig(BaseModel):
    enabled: bool = True
    weekday: int = 0             # 0=Monday, 6=Sunday
    hour_ct: int = 10            # 10am Central
    target_role: str = "tenant"
    min_days_since_login: int = 14
    subject: Optional[str] = None
    body_html: Optional[str] = None


DEFAULT_SUBJECT = "📱 ¿Ya descargaste nuestra app?"
DEFAULT_BODY = """
<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width:600px; margin:auto; padding:20px;">
  <p>Hola {name},</p>
  <p>Vimos que aún no has descargado la <b>app de Ross House Rentals</b>. Con ella puedes:</p>
  <ul>
    <li>Pagar renta en 2 toques (sin ir al portal web)</li>
    <li>Enviar solicitudes de mantenimiento con foto directo de la cámara</li>
    <li>Ver tu contrato firmado y todos tus recibos</li>
    <li>Recibir avisos importantes al instante</li>
  </ul>
  <p style="text-align:center; margin: 32px 0;">
    <a href="{ios_url}"
       style="background:#4f46e5; color:#fff; padding:14px 28px; border-radius:8px; text-decoration:none; font-weight:bold; font-size:16px;">
      Descargar en App Store
    </a>
  </p>
  <p style="color:#666; font-size:12px;">Ross House Rentals LLC · Dumas, TX · <a href="https://www.rosshouserentals.com">rosshouserentals.com</a></p>
</div>
"""


@router.get("/reengagement/config")
async def get_reengage_config(request: Request):
    await auth_admin(request)
    db = get_db()
    doc = await db.app_settings.find_one({"_id": REENGAGE_CFG_ID}) or {}
    return {
        "status": "success",
        "config": {
            "enabled": doc.get("enabled", True),
            "weekday": doc.get("weekday", 0),
            "hour_ct": doc.get("hour_ct", 10),
            "target_role": doc.get("target_role", "tenant"),
            "min_days_since_login": doc.get("min_days_since_login", 14),
            "subject": doc.get("subject", DEFAULT_SUBJECT),
            "body_html": doc.get("body_html", DEFAULT_BODY),
            "last_run_at": doc.get("last_run_at").isoformat() if isinstance(doc.get("last_run_at"), datetime) else None,
            "last_run_sent": doc.get("last_run_sent", 0),
        },
    }


@router.put("/reengagement/config")
async def update_reengage_config(payload: ReengageConfig, request: Request):
    await auth_admin(request)
    db = get_db()
    await db.app_settings.update_one(
        {"_id": REENGAGE_CFG_ID},
        {"$set": {
            "enabled": payload.enabled,
            "weekday": payload.weekday,
            "hour_ct": payload.hour_ct,
            "target_role": payload.target_role,
            "min_days_since_login": payload.min_days_since_login,
            "subject": (payload.subject or DEFAULT_SUBJECT).strip(),
            "body_html": (payload.body_html or DEFAULT_BODY).strip(),
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"status": "success"}


async def run_reengagement_campaign(db, force: bool = False) -> dict:
    """
    Core re-engagement logic — invoked by the cron and by the manual endpoint.
    Returns {sent, failed, skipped_reason?}.
    """
    cfg = await db.app_settings.find_one({"_id": REENGAGE_CFG_ID}) or {}
    if not cfg.get("enabled", True) and not force:
        return {"skipped_reason": "disabled", "sent": 0, "failed": 0}

    target_role = cfg.get("target_role", "tenant")
    min_days = int(cfg.get("min_days_since_login", 14))
    subject = cfg.get("subject", DEFAULT_SUBJECT)
    body_tpl = cfg.get("body_html", DEFAULT_BODY)

    threshold = datetime.now(timezone.utc) - timedelta(days=min_days)

    # Users to target: role match, no push_token OR stale token, activity old enough
    query = {
        "role": target_role,
        "$or": [
            {"push_token": {"$in": [None, ""]}},
            {"push_token": {"$exists": False}},
        ],
    }
    users = await db.app_users.find(query).to_list(length=5000)

    # Additional filter: last_login old enough (or never) AND has an email
    candidates = []
    for u in users:
        email = (u.get("email") or "").strip()
        if not email:
            continue
        last_login = u.get("last_login")
        if last_login and isinstance(last_login, datetime):
            if last_login.tzinfo is None:
                last_login = last_login.replace(tzinfo=timezone.utc)
            if last_login > threshold:
                continue  # too recent, skip
        candidates.append(u)

    sent = 0
    failed = 0
    for u in candidates:
        first_name = (u.get("name") or "").split(" ")[0] or "amigo"
        ios_url = f"{IOS_URL}?utm_source=email&utm_medium=reengagement&utm_campaign=weekly_cron"
        html = body_tpl.replace("{name}", first_name).replace("{ios_url}", ios_url)
        ok = await _send_email_direct((u.get("email") or "").strip(), subject, html)
        if ok:
            sent += 1
        else:
            failed += 1

        # Audit each send
        await db.app_adoption_invites.insert_one({
            "user_id": str(u["_id"]),
            "user_email": u.get("email", ""),
            "user_phone": u.get("phone", ""),
            "channel": "email",
            "sms_sent": False,
            "email_sent": ok,
            "sent_by_admin": "cron:reengagement",
            "sent_at": datetime.now(timezone.utc),
        })

    # Record run
    await db.app_settings.update_one(
        {"_id": REENGAGE_CFG_ID},
        {"$set": {
            "last_run_at": datetime.now(timezone.utc),
            "last_run_sent": sent,
            "last_run_failed": failed,
            "last_run_candidates": len(candidates),
        }},
        upsert=True,
    )

    logger.info(f"[reengagement] run complete — sent={sent} failed={failed} candidates={len(candidates)}")
    return {"sent": sent, "failed": failed, "candidates": len(candidates)}


@router.post("/reengagement/run-now")
async def run_reengage_now(request: Request):
    """Manually trigger the re-engagement email campaign (admin only)."""
    await auth_admin(request)
    db = get_db()
    result = await run_reengagement_campaign(db, force=True)
    return {"status": "success", **result}

