"""
Xcel Energy Green Button Connect (ESPI/NAESB) integration
- OAuth 2.0 authorization code flow (customer authorizes data sharing)
- Token storage + refresh per property connection
- ESPI Atom/XML parsing (IntervalBlock + UsageSummary) into kWh readings
- Usage endpoints for the admin dashboard (web + mobile)
"""
import os
import secrets
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from bson import ObjectId

from .shared import get_db, auth_admin, auth_tenant, auth_marketplace, serialize

logger = logging.getLogger("xcel_energy")
router = APIRouter()

XCEL_CLIENT_ID = os.environ.get("XCEL_CLIENT_ID", "")
XCEL_CLIENT_SECRET = os.environ.get("XCEL_CLIENT_SECRET", "")
XCEL_AUTH_URL = os.environ.get(
    "XCEL_AUTH_URL",
    "https://myenergy.xcelenergy.com/greenbutton-connect/gbc/espi/1_1/oauth/authorize",
)
XCEL_TOKEN_URL = os.environ.get(
    "XCEL_TOKEN_URL",
    "https://myenergy.xcelenergy.com/greenbutton-connect/gbc/espi/1_1/oauth/token",
)
XCEL_API_BASE = os.environ.get(
    "XCEL_API_BASE",
    "https://myenergy.xcelenergy.com/greenbutton-connect/gbc/espi/1_1/resource",
)
XCEL_REDIRECT_URI = os.environ.get("XCEL_REDIRECT_URI", "")
XCEL_SCOPE = os.environ.get("XCEL_SCOPE", "")

ESPI_NS = "http://naesb.org/espi"
ATOM_NS = "http://www.w3.org/2005/Atom"


def _is_configured() -> bool:
    return bool(XCEL_CLIENT_ID and XCEL_CLIENT_SECRET and XCEL_REDIRECT_URI)


async def _find_active_contract_for_user(user: dict):
    """Find the active rental contract for a logged-in marketplace user.
    Looks up by (in order):
      1. tenant_id == user._id (direct app_user as tenant)
      2. tenants.app_user_id == user._id → contracts.tenant_id
      3. tenants.email matches user.email → contracts.tenant_id
      4. tenants.phone matches user.phone (normalized) → contracts.tenant_id
    Returns the contract dict or None.
    """
    db = get_db()
    user_id = str(user["_id"])

    # 1. Direct match: app_user._id is the tenant_id
    contract = await db.rental_contracts.find_one({"tenant_id": user_id, "status": "active"})
    if contract:
        return contract

    # 2. Linked: tenants.app_user_id == user._id
    tenant = await db.tenants.find_one({"app_user_id": user_id})
    if tenant:
        contract = await db.rental_contracts.find_one({
            "tenant_id": str(tenant["_id"]),
            "status": "active",
        })
        if contract:
            return contract

    # 3. By email
    user_email = (user.get("email") or "").strip().lower()
    if user_email:
        import re
        tenant = await db.tenants.find_one({
            "email": {"$regex": f"^{re.escape(user_email)}$", "$options": "i"}
        })
        if tenant:
            # Auto-link tenant ↔ app_user for future queries
            if not tenant.get("app_user_id"):
                await db.tenants.update_one(
                    {"_id": tenant["_id"]},
                    {"$set": {"app_user_id": user_id, "updated_at": datetime.utcnow()}}
                )
            contract = await db.rental_contracts.find_one({
                "tenant_id": str(tenant["_id"]),
                "status": "active",
            })
            if contract:
                return contract

    # 4. By phone (normalized)
    user_phone = (user.get("phone") or "").strip()
    if user_phone:
        def _norm(p):
            return (p or "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("+", "")
        target = _norm(user_phone)
        if target:
            async for t in db.tenants.find({"phone": {"$exists": True, "$ne": ""}}):
                if _norm(t.get("phone", "")) == target:
                    if not t.get("app_user_id"):
                        await db.tenants.update_one(
                            {"_id": t["_id"]},
                            {"$set": {"app_user_id": user_id, "updated_at": datetime.utcnow()}}
                        )
                    contract = await db.rental_contracts.find_one({
                        "tenant_id": str(t["_id"]),
                        "status": "active",
                    })
                    if contract:
                        return contract
                    break
    return None


# ═══════════════════════════════════════════════════════════════
# ESPI XML PARSING
# ═══════════════════════════════════════════════════════════════

def parse_espi_feed(xml_text: str) -> dict:
    """
    Parse an ESPI Atom feed and extract interval readings (kWh) and usage summaries.
    Handles ReadingType powerOfTenMultiplier when present (default Wh, multiplier 0).
    Returns {"interval_readings": [...], "usage_summaries": [...]}
    """
    result = {"interval_readings": [], "usage_summaries": []}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"ESPI XML parse error: {e}")
        return result

    # Determine multiplier from ReadingType (powerOfTenMultiplier), default 0
    multiplier = 0
    rt = root.find(f".//{{{ESPI_NS}}}ReadingType")
    if rt is not None:
        p10 = rt.find(f"{{{ESPI_NS}}}powerOfTenMultiplier")
        if p10 is not None and p10.text not in (None, ""):
            try:
                multiplier = int(p10.text)
            except ValueError:
                multiplier = 0

    # IntervalBlocks -> IntervalReadings
    for block in root.iter(f"{{{ESPI_NS}}}IntervalBlock"):
        for ir in block.findall(f"{{{ESPI_NS}}}IntervalReading"):
            tp = ir.find(f"{{{ESPI_NS}}}timePeriod")
            val = ir.find(f"{{{ESPI_NS}}}value")
            if tp is None or val is None or val.text is None:
                continue
            start_el = tp.find(f"{{{ESPI_NS}}}start")
            dur_el = tp.find(f"{{{ESPI_NS}}}duration")
            if start_el is None or start_el.text is None:
                continue
            try:
                start_epoch = int(start_el.text)
                duration = int(dur_el.text) if dur_el is not None and dur_el.text else 3600
                raw_value = float(val.text)
            except ValueError:
                continue
            value_kwh = raw_value * (10 ** multiplier) / 1000.0  # Wh -> kWh
            result["interval_readings"].append({
                "start_epoch": start_epoch,
                "duration_seconds": duration,
                "value_kwh": round(value_kwh, 4),
            })

    # UsageSummaries (billing periods)
    for us in root.iter(f"{{{ESPI_NS}}}UsageSummary"):
        bp = us.find(f"{{{ESPI_NS}}}billingPeriod")
        if bp is None:
            continue
        start_el = bp.find(f"{{{ESPI_NS}}}start")
        dur_el = bp.find(f"{{{ESPI_NS}}}duration")
        if start_el is None or start_el.text is None:
            continue
        try:
            start_epoch = int(start_el.text)
            duration = int(dur_el.text) if dur_el is not None and dur_el.text else 0
        except ValueError:
            continue
        summary = {"start_epoch": start_epoch, "duration_seconds": duration,
                   "total_kwh": None, "cost": None, "currency": None}
        # overallConsumptionLastPeriod or billLastPeriod for energy
        ocp = us.find(f"{{{ESPI_NS}}}overallConsumptionLastPeriod")
        if ocp is not None:
            v = ocp.find(f"{{{ESPI_NS}}}value")
            p10 = ocp.find(f"{{{ESPI_NS}}}powerOfTenMultiplier")
            if v is not None and v.text:
                try:
                    m = int(p10.text) if p10 is not None and p10.text else 0
                    summary["total_kwh"] = round(float(v.text) * (10 ** m) / 1000.0, 4)
                except ValueError:
                    pass
        cost_el = us.find(f"{{{ESPI_NS}}}billLastPeriod")
        if cost_el is not None and cost_el.text:
            try:
                summary["cost"] = float(cost_el.text) / 100000.0  # ESPI: hundred-thousandths of currency
            except ValueError:
                pass
        cur = us.find(f"{{{ESPI_NS}}}currency")
        if cur is not None and cur.text:
            summary["currency"] = cur.text
        result["usage_summaries"].append(summary)

    return result


def extract_subscription_id(token_json: dict) -> str:
    """Extract subscription id from a token response (direct field or resourceURI path)."""
    for key in ("subscription_id", "subscriptionId"):
        if token_json.get(key):
            return str(token_json[key])
    resource_uri = token_json.get("resourceURI") or token_json.get("resource_uri") or ""
    if "Subscription/" in resource_uri:
        return resource_uri.rstrip("/").split("Subscription/")[-1].split("/")[0]
    return ""


# ═══════════════════════════════════════════════════════════════
# TOKEN MANAGEMENT
# ═══════════════════════════════════════════════════════════════

async def _refresh_token_if_needed(conn: dict) -> dict:
    """Refresh the access token if it expires in <120s. Returns updated connection doc."""
    db = get_db()
    expires_at = conn.get("access_token_expires_at") or 0
    now = datetime.now(timezone.utc).timestamp()
    if conn.get("access_token") and now < expires_at - 120:
        return conn

    refresh_token = conn.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Conexión sin refresh token; reautoriza en Xcel Energy")

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": XCEL_CLIENT_ID,
        "client_secret": XCEL_CLIENT_SECRET,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(XCEL_TOKEN_URL, data=data)
    if resp.status_code != 200:
        await db.xcel_connections.update_one(
            {"_id": conn["_id"]},
            {"$set": {"status": "needs_reauth", "last_error": resp.text[:500],
                      "updated_at": datetime.now(timezone.utc)}},
        )
        raise HTTPException(status_code=502, detail="No se pudo renovar el token de Xcel; reautoriza la conexión")

    tj = resp.json()
    update = {
        "access_token": tj.get("access_token"),
        "refresh_token": tj.get("refresh_token") or refresh_token,
        "access_token_expires_at": now + int(tj.get("expires_in", 3600)),
        "status": "active",
        "last_error": None,
        "updated_at": datetime.now(timezone.utc),
    }
    new_sub = extract_subscription_id(tj)
    if new_sub:
        update["subscription_id"] = new_sub
    await db.xcel_connections.update_one({"_id": conn["_id"]}, {"$set": update})
    conn.update(update)
    return conn


# ═══════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/xcel/status")
async def xcel_status(request: Request):
    await auth_admin(request)
    db = get_db()
    total = await db.xcel_connections.count_documents({})
    active = await db.xcel_connections.count_documents({"status": "active"})
    return {
        "configured": _is_configured(),
        "redirect_uri": XCEL_REDIRECT_URI,
        "connections_total": total,
        "connections_active": active,
    }


@router.get("/admin/xcel/connect-url")
async def xcel_connect_url(request: Request, property_id: str):
    """Generate the Xcel authorization URL for a property (admin opens it in browser)."""
    await auth_admin(request)
    if not _is_configured():
        raise HTTPException(status_code=500, detail="Integración Xcel no configurada (faltan credenciales)")
    db = get_db()

    prop = None
    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        prop = await db.properties.find_one({"_id": property_id})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    state = secrets.token_urlsafe(24)
    await db.xcel_oauth_states.insert_one({
        "state": state,
        "property_id": str(prop["_id"]),
        "created_at": datetime.now(timezone.utc),
    })

    params = {
        "response_type": "code",
        "client_id": XCEL_CLIENT_ID,
        "redirect_uri": XCEL_REDIRECT_URI,
        "state": state,
    }
    if XCEL_SCOPE:
        params["scope"] = XCEL_SCOPE
    return {"authorization_url": f"{XCEL_AUTH_URL}?{urlencode(params)}"}


@router.get("/tenant/xcel/connect-url")
async def tenant_xcel_connect_url(request: Request):
    """The tenant authorizes Xcel data sharing for the property of their active contract."""
    tenant = await auth_marketplace(request)
    if not _is_configured():
        raise HTTPException(status_code=500, detail="Integración Xcel no configurada")
    contract = await _find_active_contract_for_user(tenant)
    if not contract or not contract.get("property_id"):
        raise HTTPException(status_code=400, detail="No tienes un contrato activo con propiedad asignada")

    state = secrets.token_urlsafe(24)
    await db.xcel_oauth_states.insert_one({
        "state": state,
        "property_id": str(contract["property_id"]),
        "initiated_by": f"tenant:{tenant['_id']}",
        "created_at": datetime.now(timezone.utc),
    })
    params = {
        "response_type": "code",
        "client_id": XCEL_CLIENT_ID,
        "redirect_uri": XCEL_REDIRECT_URI,
        "state": state,
    }
    if XCEL_SCOPE:
        params["scope"] = XCEL_SCOPE
    return {"authorization_url": f"{XCEL_AUTH_URL}?{urlencode(params)}"}


@router.get("/admin/xcel/connections")
async def xcel_connections(request: Request):
    await auth_admin(request)
    db = get_db()
    conns = []
    async for c in db.xcel_connections.find({}).sort("created_at", -1):
        prop_address = None
        try:
            prop = await db.properties.find_one({"_id": ObjectId(c["property_id"])})
        except Exception:
            prop = await db.properties.find_one({"_id": c["property_id"]})
        if prop:
            prop_address = prop.get("address") or prop.get("name")
        conns.append({
            "id": str(c["_id"]),
            "property_id": c["property_id"],
            "property_address": prop_address,
            "status": c.get("status"),
            "subscription_id": c.get("subscription_id"),
            "last_sync": c.get("last_sync").isoformat() if c.get("last_sync") else None,
            "last_error": c.get("last_error"),
            "created_at": c.get("created_at").isoformat() if c.get("created_at") else None,
        })
    return {"connections": conns}


@router.delete("/admin/xcel/connections/{conn_id}")
async def xcel_delete_connection(request: Request, conn_id: str):
    await auth_admin(request)
    db = get_db()
    res = await db.xcel_connections.delete_one({"_id": ObjectId(conn_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")
    return {"success": True}


@router.post("/admin/xcel/connections/{conn_id}/sync")
async def xcel_sync_connection(request: Request, conn_id: str):
    """Fetch usage data from Xcel for a connection and store kWh readings."""
    await auth_admin(request)
    db = get_db()
    conn = await db.xcel_connections.find_one({"_id": ObjectId(conn_id)})
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")

    conn = await _refresh_token_if_needed(conn)
    sub_id = conn.get("subscription_id")
    if not sub_id:
        raise HTTPException(status_code=400, detail="La conexión no tiene subscription_id; reautoriza en Xcel")

    headers = {"Authorization": f"Bearer {conn['access_token']}", "Accept": "application/atom+xml"}
    url = f"{XCEL_API_BASE.rstrip('/')}/Batch/Subscription/{sub_id}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        await db.xcel_connections.update_one(
            {"_id": conn["_id"]},
            {"$set": {"last_error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                      "updated_at": datetime.now(timezone.utc)}},
        )
        raise HTTPException(status_code=502, detail=f"Xcel respondió {resp.status_code} al pedir datos")

    parsed = parse_espi_feed(resp.text)
    property_id = conn["property_id"]
    now = datetime.now(timezone.utc)

    # Aggregate interval readings into daily kWh and upsert
    daily: dict = {}
    for r in parsed["interval_readings"]:
        day = datetime.fromtimestamp(r["start_epoch"], tz=timezone.utc).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + r["value_kwh"]
    for day, kwh in daily.items():
        await db.xcel_usage_daily.update_one(
            {"property_id": property_id, "date": day},
            {"$set": {"kwh": round(kwh, 3), "updated_at": now},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    # Store billing summaries
    for s in parsed["usage_summaries"]:
        period_start = datetime.fromtimestamp(s["start_epoch"], tz=timezone.utc)
        await db.xcel_usage_summaries.update_one(
            {"property_id": property_id, "period_start": period_start},
            {"$set": {
                "period_days": round(s["duration_seconds"] / 86400) if s["duration_seconds"] else None,
                "total_kwh": s["total_kwh"],
                "cost": s["cost"],
                "currency": s["currency"],
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    await db.xcel_connections.update_one(
        {"_id": conn["_id"]},
        {"$set": {"last_sync": now, "last_error": None, "status": "active", "updated_at": now}},
    )
    return {
        "success": True,
        "interval_readings": len(parsed["interval_readings"]),
        "days_updated": len(daily),
        "summaries": len(parsed["usage_summaries"]),
    }


@router.get("/admin/xcel/usage/{property_id}")
async def xcel_usage(request: Request, property_id: str, months: int = 12):
    """Monthly kWh series for a property's dashboard chart."""
    await auth_admin(request)
    db = get_db()

    conn = await db.xcel_connections.find_one({"property_id": property_id})
    monthly: dict = {}
    async for doc in db.xcel_usage_daily.find({"property_id": property_id}):
        month = doc["date"][:7]
        monthly[month] = monthly.get(month, 0.0) + doc.get("kwh", 0.0)

    series = [{"month": m, "kwh": round(v, 2)} for m, v in sorted(monthly.items())][-months:]
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    prev_month = (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    current_kwh = monthly.get(current_month, 0.0)
    prev_kwh = monthly.get(prev_month, 0.0)
    delta_pct = round((current_kwh - prev_kwh) / prev_kwh * 100, 1) if prev_kwh else None

    summaries = []
    async for s in db.xcel_usage_summaries.find({"property_id": property_id}).sort("period_start", -1).limit(12):
        summaries.append({
            "period_start": s["period_start"].isoformat(),
            "period_days": s.get("period_days"),
            "total_kwh": s.get("total_kwh"),
            "cost": s.get("cost"),
        })

    return {
        "connected": bool(conn),
        "status": conn.get("status") if conn else None,
        "last_sync": conn.get("last_sync").isoformat() if conn and conn.get("last_sync") else None,
        "monthly": series,
        "current_month_kwh": round(current_kwh, 2),
        "prev_month_kwh": round(prev_kwh, 2),
        "delta_pct": delta_pct,
        "billing_summaries": summaries,
    }


@router.get("/tenant/xcel/usage")
async def tenant_xcel_usage(request: Request, months: int = 6):
    """Return Green Button kWh usage data for the logged-in tenant's active property.
    Used by the mobile app to render the consumption dashboard inside 'Mis Servicios'.
    """
    user = await auth_marketplace(request)
    db = get_db()

    contract = await _find_active_contract_for_user(user)
    if not contract or not contract.get("property_id"):
        return {
            "connected": False,
            "reason": "no_active_contract",
            "message": "No tienes un contrato activo asignado a una propiedad.",
            "monthly": [],
            "daily_current_month": [],
        }

    property_id = str(contract["property_id"])
    conn = await db.xcel_connections.find_one({"property_id": property_id})

    # Monthly + daily aggregations
    monthly: dict = {}
    daily_current_month: list = []
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    async for doc in db.xcel_usage_daily.find({"property_id": property_id}).sort("date", 1):
        m = doc["date"][:7]
        monthly[m] = monthly.get(m, 0.0) + doc.get("kwh", 0.0)
        if m == current_month:
            daily_current_month.append({
                "date": doc["date"],
                "kwh": round(doc.get("kwh", 0.0), 2),
            })

    series = [{"month": m, "kwh": round(v, 2)} for m, v in sorted(monthly.items())][-months:]
    prev_month = (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    current_kwh = monthly.get(current_month, 0.0)
    prev_kwh = monthly.get(prev_month, 0.0)
    delta_pct = round((current_kwh - prev_kwh) / prev_kwh * 100, 1) if prev_kwh else None

    summaries = []
    async for s in db.xcel_usage_summaries.find({"property_id": property_id}).sort("period_start", -1).limit(6):
        summaries.append({
            "period_start": s["period_start"].isoformat(),
            "period_days": s.get("period_days"),
            "total_kwh": s.get("total_kwh"),
            "cost": s.get("cost"),
        })

    # Approximate rate to estimate the live cost (configurable per environment)
    try:
        estimated_rate = float(os.environ.get("XCEL_ESTIMATED_RATE_PER_KWH", "0.14"))
    except ValueError:
        estimated_rate = 0.14

    # Property address for context
    prop_address = None
    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        prop = await db.properties.find_one({"_id": property_id})
    if prop:
        prop_address = prop.get("address") or prop.get("name")

    is_connected = bool(conn) and conn.get("status") == "active"
    has_data = len(monthly) > 0

    return {
        "connected": is_connected,
        "status": conn.get("status") if conn else "not_connected",
        "has_data": has_data,
        "last_sync": conn.get("last_sync").isoformat() if conn and conn.get("last_sync") else None,
        "property_id": property_id,
        "property_address": prop_address,
        "monthly": series,
        "daily_current_month": daily_current_month,
        "current_month": current_month,
        "current_month_kwh": round(current_kwh, 2),
        "prev_month_kwh": round(prev_kwh, 2),
        "delta_pct": delta_pct,
        "estimated_rate_per_kwh": estimated_rate,
        "estimated_current_cost": round(current_kwh * estimated_rate, 2),
        "billing_summaries": summaries,
    }


# ═══════════════════════════════════════════════════════════════
# PUBLIC ENDPOINTS (Xcel calls these)
# ═══════════════════════════════════════════════════════════════

def _result_html(title: str, message: str, ok: bool) -> str:
    color = "#16A34A" if ok else "#DC2626"
    icon = "✓" if ok else "✕"
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0F172A;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh">
<div style="text-align:center;padding:40px;max-width:420px">
<div style="width:72px;height:72px;border-radius:50%;background:{color};display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:36px">{icon}</div>
<h1 style="font-size:22px;margin:0 0 10px">{title}</h1>
<p style="color:#94A3B8;font-size:15px;line-height:1.5">{message}</p>
<p style="color:#475569;font-size:13px;margin-top:24px">Ross House Rentals LLC · Xcel Energy Green Button</p>
</div></body></html>"""


async def _exchange_code_and_save(code: str, state: str) -> tuple:
    """Validate state, exchange the code for tokens and save the connection.
    Returns (ok: bool, title: str, message: str)."""
    db = get_db()
    st = await db.xcel_oauth_states.find_one({"state": state})
    if not st:
        return False, "Enlace expirado", "El enlace de autorización expiró o ya fue usado. Genera uno nuevo desde el panel."
    created = st["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(minutes=30):
        await db.xcel_oauth_states.delete_one({"_id": st["_id"]})
        return False, "Enlace expirado", "Genera un nuevo enlace desde el panel de Energía."

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": XCEL_REDIRECT_URI,
        "client_id": XCEL_CLIENT_ID,
        "client_secret": XCEL_CLIENT_SECRET,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(XCEL_TOKEN_URL, data=data)
    if resp.status_code != 200:
        logger.error(f"Xcel token exchange failed: {resp.status_code} {resp.text[:300]}")
        return False, "Error al conectar", "Xcel Energy rechazó el intercambio de tokens. Intenta de nuevo o contacta soporte."

    tj = resp.json()
    now_ts = datetime.now(timezone.utc).timestamp()
    now = datetime.now(timezone.utc)
    doc = {
        "property_id": st["property_id"],
        "access_token": tj.get("access_token"),
        "refresh_token": tj.get("refresh_token"),
        "access_token_expires_at": now_ts + int(tj.get("expires_in", 3600)),
        "subscription_id": extract_subscription_id(tj),
        "scope": tj.get("scope"),
        "status": "active",
        "last_error": None,
        "raw_token_response": {k: v for k, v in tj.items() if k not in ("access_token", "refresh_token")},
        "updated_at": now,
    }
    await db.xcel_connections.update_one(
        {"property_id": st["property_id"]},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    await db.xcel_oauth_states.delete_one({"_id": st["_id"]})
    return True, "¡Conexión exitosa!", "La cuenta de Xcel Energy quedó conectada. El consumo eléctrico aparecerá en el panel de Energía de Ross House Rentals."


@router.get("/xcel/oauth/callback", response_class=HTMLResponse)
async def xcel_oauth_callback(request: Request):
    """Public OAuth callback: Xcel redirects the customer here after authorization."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(_result_html("Autorización cancelada",
                                         f"Xcel Energy reportó: {error}. Puedes cerrar esta ventana e intentarlo de nuevo.", False))
    if not code or not state:
        return HTMLResponse(_result_html("Solicitud inválida", "Faltan parámetros de autorización.", False), status_code=400)

    ok, title, message = await _exchange_code_and_save(code, state)
    if ok:
        message += " Ya puedes cerrar esta ventana."
    return HTMLResponse(_result_html(title, message, ok), status_code=200 if ok else 400)


@router.post("/greenbutton/exchange")
async def greenbutton_exchange(payload: dict):
    """Public JSON exchange endpoint used by the web page at
    rosshouserentals.com/tenant/utilities?callback=greenbutton (registered Redirect URL)."""
    code = payload.get("code")
    state = payload.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan parámetros code/state")
    ok, title, message = await _exchange_code_and_save(code, state)
    return {"success": ok, "title": title, "message": message}


@router.post("/xcel/notify")
@router.post("/greenbutton/notify")
async def xcel_notify(request: Request):
    """Public Notification URL: Xcel posts here when new data is available.
    Registered in the Xcel portal as /api/greenbutton/notify."""
    db = get_db()
    body = (await request.body())[:10000].decode("utf-8", errors="replace")
    await db.xcel_notifications.insert_one({
        "received_at": datetime.now(timezone.utc),
        "body": body,
        "processed": False,
    })
    logger.info("Xcel Green Button notification received")
    return {"status": "ok"}
