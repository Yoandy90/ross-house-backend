"""
Xcel Energy Green Button Connect (ESPI/NAESB) integration
- OAuth 2.0 authorization code flow (customer authorizes data sharing)
- Token storage + refresh per property connection
- ESPI Atom/XML parsing (IntervalBlock + UsageSummary) into kWh readings
- Usage endpoints for the admin dashboard (web + mobile)
- Demo data fallback when no real data is available yet
- AI-powered saving tips (Emergent LLM key → Claude / GPT-4o)
"""
import os
import math
import json
import random
import secrets
import logging
from datetime import datetime, timezone, timedelta, date
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
# Scope for the authorization_code (customer) flow. Per NAESB v3.3 the scope
# is typically a custom string like "FB=4_5_15_16_17_18_19_20_21_22_23_24_25;..."
# that Xcel emails to the Service Provider after approval. Configurable via env.
XCEL_SCOPE = os.environ.get("XCEL_SCOPE", "")
# Scope for the client_credentials flow used to call ReadServiceStatus and
# the Authorization resource. Per the GBC Vendor Startup Guide this MUST be
# "FB=34_35" (see "Client Credentials Grant" section of the guide).
XCEL_ADMIN_SCOPE = os.environ.get("XCEL_ADMIN_SCOPE", "FB=34_35")
# Application Information ID + Registration Token are issued by Xcel when the
# Service Provider is approved. They allow calling the ApplicationInformation
# resource directly (no customer auth needed) — useful as a smoke test.
XCEL_APPLICATION_ID = os.environ.get("XCEL_APPLICATION_ID", "")
XCEL_REGISTRATION_TOKEN = os.environ.get("XCEL_REGISTRATION_TOKEN", "")

ESPI_NS = "http://naesb.org/espi"
ATOM_NS = "http://www.w3.org/2005/Atom"

# In-memory cache of the client_credentials token (one per process is enough).
_CLIENT_TOKEN_CACHE: dict = {"access_token": None, "expires_at": 0}


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
    }
    # ESPI requires HTTP Basic Auth (same as auth-code exchange).
    auth = httpx.BasicAuth(XCEL_CLIENT_ID, XCEL_CLIENT_SECRET)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(XCEL_TOKEN_URL, data=data, auth=auth, headers=headers)
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
# CLIENT CREDENTIALS TOKEN (for ReadServiceStatus / Authorization)
# ═══════════════════════════════════════════════════════════════

async def _get_client_credentials_token() -> str:
    """Get (and cache) a client_credentials access token. Per the GBC Vendor
    Startup Guide, this is required for ReadServiceStatus and to read the
    Authorization resource. Scope = FB=34_35.
    """
    now = datetime.now(timezone.utc).timestamp()
    if _CLIENT_TOKEN_CACHE["access_token"] and now < _CLIENT_TOKEN_CACHE["expires_at"] - 60:
        return _CLIENT_TOKEN_CACHE["access_token"]

    if not (XCEL_CLIENT_ID and XCEL_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Faltan XCEL_CLIENT_ID / XCEL_CLIENT_SECRET")

    auth = httpx.BasicAuth(XCEL_CLIENT_ID, XCEL_CLIENT_SECRET)
    data = {"grant_type": "client_credentials", "scope": XCEL_ADMIN_SCOPE}
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(XCEL_TOKEN_URL, data=data, auth=auth, headers=headers)
    if resp.status_code != 200:
        logger.error(f"[Xcel] client_credentials failed: {resp.status_code} {resp.text[:300]}")
        raise HTTPException(
            status_code=502,
            detail=f"Xcel rechazó client_credentials (HTTP {resp.status_code}): {resp.text[:200]}",
        )
    tj = resp.json()
    _CLIENT_TOKEN_CACHE["access_token"] = tj.get("access_token")
    _CLIENT_TOKEN_CACHE["expires_at"] = now + int(tj.get("expires_in", 3600))
    return _CLIENT_TOKEN_CACHE["access_token"]

@router.get("/admin/xcel/audit-log")
async def xcel_audit_log(request: Request):
    """Admin: Recent Xcel OAuth/exchange events for diagnosing connection failures."""
    await auth_admin(request)
    db = get_db()
    items = []
    cursor = db.xcel_audit_log.find().sort("at", -1).limit(50)
    async for d in cursor:
        d["_id"] = str(d["_id"])
        items.append(d)
    # Also include the count of pending oauth states
    pending = await db.xcel_oauth_states.count_documents({})
    connections = await db.xcel_connections.count_documents({})
    return {
        "success": True,
        "audit_entries": len(items),
        "pending_oauth_states": pending,
        "active_connections": connections,
        "items": items,
    }


@router.get("/admin/xcel/status")
async def xcel_status(request: Request):
    await auth_admin(request)
    db = get_db()
    total = await db.xcel_connections.count_documents({})
    active = await db.xcel_connections.count_documents({"status": "active"})
    return {
        "configured": _is_configured(),
        "redirect_uri": XCEL_REDIRECT_URI,
        "auth_url": XCEL_AUTH_URL,
        "token_url": XCEL_TOKEN_URL,
        "api_base": XCEL_API_BASE,
        "scope_customer": XCEL_SCOPE or "(empty — set XCEL_SCOPE)",
        "scope_admin": XCEL_ADMIN_SCOPE,
        "application_id": XCEL_APPLICATION_ID or "(empty)",
        "registration_token_set": bool(XCEL_REGISTRATION_TOKEN),
        "notification_url": f"{os.environ.get('PUBLIC_BACKEND_URL','').rstrip('/')}/api/greenbutton/notify",
        "connections_total": total,
        "connections_active": active,
    }


@router.get("/admin/xcel/read-service-status")
async def xcel_read_service_status(request: Request):
    """Call Xcel's ReadServiceStatus endpoint using client_credentials.

    The GBC Vendor Startup Guide lists this as one of the first calls a
    Service Provider should make to verify the integration is alive before
    requesting customer data. Returns the raw response for diagnostics.
    """
    await auth_admin(request)
    try:
        token = await _get_client_credentials_token()
    except HTTPException as e:
        return {"ok": False, "stage": "token", "detail": e.detail}
    url = f"{XCEL_API_BASE.rstrip('/')}/ReadServiceStatus"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/atom+xml"}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=headers)
    return {
        "ok": resp.status_code == 200,
        "status_code": resp.status_code,
        "body_preview": resp.text[:1000],
        "url_called": url,
    }


@router.get("/admin/xcel/application-information")
async def xcel_application_information(request: Request):
    """Smoke test: fetch the ApplicationInformation resource using the
    Registration Token provided by Xcel when the Service Provider is approved.

    Per the GBC Vendor Startup Guide, this resource describes the Service
    Provider's configuration (client_id, redirect_uri, notification_uri,
    scopes, etc.) as Xcel has it on file. If this call succeeds → credentials
    and registration are valid.

    Requires env vars: XCEL_APPLICATION_ID + XCEL_REGISTRATION_TOKEN.
    """
    await auth_admin(request)
    if not XCEL_APPLICATION_ID:
        raise HTTPException(status_code=400, detail="Falta XCEL_APPLICATION_ID en el environment")
    if not XCEL_REGISTRATION_TOKEN:
        raise HTTPException(status_code=400, detail="Falta XCEL_REGISTRATION_TOKEN en el environment")

    url = f"{XCEL_API_BASE.rstrip('/')}/ApplicationInformation/{XCEL_APPLICATION_ID}"
    headers = {
        "Authorization": f"Bearer {XCEL_REGISTRATION_TOKEN}",
        "Accept": "application/atom+xml",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=headers)

    return {
        "ok": resp.status_code == 200,
        "status_code": resp.status_code,
        "url_called": url,
        "body_preview": resp.text[:2000],
        "headers": dict(resp.headers),
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
    db = get_db()
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
    """Trigger an asynchronous Batch download from Xcel for a connection.

    Per the GBC Vendor Startup Guide, the Batch resource is asynchronous:
      1. POST to /Batch/Subscription/{subscription_id} → HTTP 202 Accepted
      2. Xcel queues the request and (when ready) calls our Notification URL
         with a payload pointing to the ready file_id.
      3. Our Notification handler downloads the XML via /Batch/{file_id}.

    This endpoint only initiates step 1. The data ingestion happens
    asynchronously when Xcel calls back. Returns 202 to mirror Xcel's behavior.
    """
    await auth_admin(request)
    db = get_db()
    conn = await db.xcel_connections.find_one({"_id": ObjectId(conn_id)})
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada")

    conn = await _refresh_token_if_needed(conn)
    sub_id = conn.get("subscription_id")
    if not sub_id:
        raise HTTPException(status_code=400, detail="La conexión no tiene subscription_id; reautoriza en Xcel")

    headers = {
        "Authorization": f"Bearer {conn['access_token']}",
        "Accept": "application/atom+xml",
    }
    url = f"{XCEL_API_BASE.rstrip('/')}/Batch/Subscription/{sub_id}"

    async with httpx.AsyncClient(timeout=60) as client:
        # NAESB v3.3 Batch is async — POST returns 202 + Location header pointing
        # to a future file. The data arrives later via Notification webhook.
        resp = await client.post(url, headers=headers)

    now = datetime.now(timezone.utc)
    await db.xcel_audit_log.insert_one({
        "event": "batch_request",
        "property_id": conn["property_id"],
        "subscription_id": sub_id,
        "status_code": resp.status_code,
        "location_header": resp.headers.get("Location"),
        "response_body": resp.text[:1000],
        "at": now,
    })

    # 202 Accepted is the EXPECTED success path
    if resp.status_code in (200, 202):
        await db.xcel_connections.update_one(
            {"_id": conn["_id"]},
            {"$set": {
                "last_batch_request_at": now,
                "last_batch_status": resp.status_code,
                "last_batch_location": resp.headers.get("Location"),
                "last_error": None,
                "status": "active",
                "updated_at": now,
            }},
        )
        # If the server happened to return data synchronously, ingest it
        if resp.status_code == 200 and resp.text and resp.text.strip().startswith("<"):
            await _ingest_espi_xml(conn["property_id"], resp.text, source="sync_inline")
            return {
                "success": True,
                "status_code": 200,
                "mode": "synchronous",
                "message": "Datos recibidos en línea (sin notification webhook).",
            }
        return {
            "success": True,
            "status_code": 202,
            "mode": "asynchronous",
            "message": (
                "Solicitud aceptada por Xcel. Los datos llegarán a la "
                "Notification URL cuando estén listos."
            ),
            "location": resp.headers.get("Location"),
        }

    # Real error
    await db.xcel_connections.update_one(
        {"_id": conn["_id"]},
        {"$set": {
            "last_error": f"Batch request HTTP {resp.status_code}: {resp.text[:300]}",
            "updated_at": now,
        }},
    )
    raise HTTPException(
        status_code=502,
        detail=f"Xcel respondió {resp.status_code} al solicitar el Batch: {resp.text[:200]}",
    )


async def _ingest_espi_xml(property_id: str, xml_text: str, source: str = "notification"):
    """Parse an ESPI XML payload and persist daily kWh + billing summaries."""
    db = get_db()
    now = datetime.now(timezone.utc)
    parsed = parse_espi_feed(xml_text)

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
        {"property_id": property_id},
        {"$set": {"last_sync": now, "last_sync_source": source,
                  "last_error": None, "status": "active", "updated_at": now}},
    )
    return {
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

    When no real data is available yet (waiting for Xcel approval), returns a
    realistic demo dataset so the UI can be visualized end-to-end. Real data
    automatically replaces the demo once Green Button starts ingesting.
    """
    user = await auth_marketplace(request)
    db = get_db()

    contract = await _find_active_contract_for_user(user)
    if not contract or not contract.get("property_id"):
        # Return demo data even without an active contract so the user can preview
        return _build_demo_usage_payload(
            reason="no_active_contract",
            message="Aún no tienes contrato activo; mostrando datos de demostración.",
        )

    property_id = str(contract["property_id"])
    conn = await db.xcel_connections.find_one({"property_id": property_id})

    # Monthly + daily aggregations
    monthly: dict = {}
    daily_current_month: list = []
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    # Daily by exact ISO date (used for the "Día" tab — last 30 days)
    daily_30d_map: dict = {}
    now_utc = datetime.now(timezone.utc)
    cutoff_30d = (now_utc - timedelta(days=30)).strftime("%Y-%m-%d")

    async for doc in db.xcel_usage_daily.find({"property_id": property_id}).sort("date", 1):
        m = doc["date"][:7]
        monthly[m] = monthly.get(m, 0.0) + doc.get("kwh", 0.0)
        if m == current_month:
            daily_current_month.append({
                "date": doc["date"],
                "kwh": round(doc.get("kwh", 0.0), 2),
            })
        if doc["date"] >= cutoff_30d:
            daily_30d_map[doc["date"]] = round(doc.get("kwh", 0.0), 2)

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

    # ─── If no real data yet, fall back to demo dataset ───
    if not has_data:
        demo = _build_demo_usage_payload(
            reason="awaiting_xcel_data",
            message="Esperando la primera sincronización de Xcel Green Button.",
        )
        demo["connected"] = is_connected
        demo["status"] = conn.get("status") if conn else "not_connected"
        demo["property_id"] = property_id
        demo["property_address"] = prop_address
        return demo

    # ─── Real data path: build day/week/month series ───
    daily_30d = _fill_daily_30d_from_map(daily_30d_map, now_utc)
    weekly_12w = _aggregate_to_weekly(daily_30d, weeks=12, anchor=now_utc)
    monthly_12m = _aggregate_to_monthly(series, months=12)

    return {
        "connected": is_connected,
        "status": conn.get("status") if conn else "not_connected",
        "has_data": has_data,
        "is_demo": False,
        "last_sync": conn.get("last_sync").isoformat() if conn and conn.get("last_sync") else None,
        "property_id": property_id,
        "property_address": prop_address,
        "monthly": series,
        "daily_current_month": daily_current_month,
        # New unified time series (used by the chart with tabs in services.tsx)
        "timeseries": {
            "daily_30d": daily_30d,
            "weekly_12w": weekly_12w,
            "monthly_12m": monthly_12m,
        },
        "current_month": current_month,
        "current_month_kwh": round(current_kwh, 2),
        "prev_month_kwh": round(prev_kwh, 2),
        "delta_pct": delta_pct,
        "estimated_rate_per_kwh": estimated_rate,
        "estimated_current_cost": round(current_kwh * estimated_rate, 2),
        "billing_summaries": summaries,
    }


# ═══════════════════════════════════════════════════════════════
# DEMO DATA GENERATOR (used while Xcel hasn't approved the SP yet
# OR before the first data sync). Deterministic per-day for stability.
# ═══════════════════════════════════════════════════════════════

def _seeded_random(seed_key: str) -> random.Random:
    """Stable PRNG seeded by a string so the same day always returns the same kWh."""
    h = abs(hash(seed_key)) % (2**32)
    return random.Random(h)


def _demo_kwh_for_day(d: date) -> float:
    """Realistic daily consumption pattern for a residential property in TX panhandle.
    Includes weekday vs weekend variation + monthly seasonality + noise."""
    rng = _seeded_random(f"day-{d.isoformat()}")
    # Base ~25 kWh/day, weekends +20%, summer (Jun-Aug) +35%, winter (Dec-Feb) +25%, fall/spring base
    base = 25.0
    if d.weekday() >= 5:  # Sat/Sun
        base *= 1.20
    if d.month in (6, 7, 8):
        base *= 1.35
    elif d.month in (12, 1, 2):
        base *= 1.25
    # Add ±15% noise
    noise = (rng.random() - 0.5) * 0.30
    return round(base * (1 + noise), 2)


def _fill_daily_30d_from_map(existing: dict, now_utc: datetime) -> list:
    """Build a complete 30-day series, filling gaps with 0 (real data only)."""
    out = []
    for i in range(29, -1, -1):
        d = (now_utc - timedelta(days=i)).date()
        iso = d.isoformat()
        out.append({"date": iso, "kwh": existing.get(iso, 0.0), "weekday": d.weekday()})
    return out


def _aggregate_to_weekly(daily: list, weeks: int = 12, anchor: datetime = None) -> list:
    """Aggregate daily series into ISO weeks. Falls back to demo if empty."""
    if not daily:
        return []
    anchor = anchor or datetime.now(timezone.utc)
    out = []
    for w in range(weeks - 1, -1, -1):
        week_end = (anchor - timedelta(weeks=w)).date()
        week_start = week_end - timedelta(days=6)
        total = 0.0
        for d in daily:
            try:
                dd = date.fromisoformat(d["date"])
                if week_start <= dd <= week_end:
                    total += float(d.get("kwh", 0.0))
            except (ValueError, TypeError):
                continue
        out.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "label": f"S{week_start.strftime('%d')}",
            "kwh": round(total, 2),
        })
    return out


def _aggregate_to_monthly(monthly_series: list, months: int = 12) -> list:
    """Pad/truncate the monthly series to `months` items and add a short label."""
    if not monthly_series:
        return []
    last = monthly_series[-months:]
    month_labels = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    out = []
    for m in last:
        try:
            _, mm = m["month"].split("-")
            label = month_labels[int(mm) - 1]
        except Exception:
            label = m.get("month", "")
        out.append({"month": m["month"], "kwh": m["kwh"], "label": label})
    return out


def _build_demo_usage_payload(reason: str = "demo", message: str = "") -> dict:
    """Generate a complete realistic demo payload for the consumption dashboard."""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()

    # 30 days of daily data
    daily_30d = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        daily_30d.append({
            "date": d.isoformat(),
            "kwh": _demo_kwh_for_day(d),
            "weekday": d.weekday(),
        })

    # 12 months of monthly data
    monthly_12m = []
    monthly_dict = {}
    cur = today.replace(day=1)
    for _ in range(12):
        # Walk back 12 months
        cur = (cur - timedelta(days=1)).replace(day=1)
    cur = today.replace(day=1)
    months_back = []
    pointer = today.replace(day=1)
    for _ in range(12):
        months_back.append(pointer.strftime("%Y-%m"))
        # previous month
        first_of_prev = (pointer - timedelta(days=1)).replace(day=1)
        pointer = first_of_prev
    months_back.reverse()  # oldest → newest
    month_labels = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    for ym in months_back:
        y, m = ym.split("-")
        y_i, m_i = int(y), int(m)
        # Compute days in month
        if m_i == 12:
            next_first = date(y_i + 1, 1, 1)
        else:
            next_first = date(y_i, m_i + 1, 1)
        first = date(y_i, m_i, 1)
        days_in_month = (next_first - first).days
        total = 0.0
        for offset in range(days_in_month):
            dd = first + timedelta(days=offset)
            if dd <= today:
                total += _demo_kwh_for_day(dd)
        monthly_12m.append({
            "month": ym,
            "kwh": round(total, 2),
            "label": month_labels[m_i - 1],
        })
        monthly_dict[ym] = round(total, 2)

    # Weekly (last 12 weeks)
    weekly_12w = _aggregate_to_weekly(daily_30d, weeks=12, anchor=now_utc)

    current_month = today.strftime("%Y-%m")
    prev_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    prev_month = prev_first.strftime("%Y-%m")
    current_kwh = monthly_dict.get(current_month, 0.0)
    prev_kwh = monthly_dict.get(prev_month, 0.0)
    delta_pct = round((current_kwh - prev_kwh) / prev_kwh * 100, 1) if prev_kwh else None

    estimated_rate = 0.14
    return {
        "connected": False,
        "status": "demo",
        "has_data": True,  # We do have data — it's just demo
        "is_demo": True,
        "demo_reason": reason,
        "demo_message": message or "Datos de demostración mientras Xcel aprueba tu cuenta.",
        "last_sync": None,
        "property_id": None,
        "property_address": "309 W 13th St, Dumas, TX 79029 (demo)",
        "monthly": [{"month": m["month"], "kwh": m["kwh"]} for m in monthly_12m[-6:]],
        "daily_current_month": [d for d in daily_30d if d["date"].startswith(current_month)],
        "timeseries": {
            "daily_30d": daily_30d,
            "weekly_12w": weekly_12w,
            "monthly_12m": monthly_12m,
        },
        "current_month": current_month,
        "current_month_kwh": round(current_kwh, 2),
        "prev_month_kwh": round(prev_kwh, 2),
        "delta_pct": delta_pct,
        "estimated_rate_per_kwh": estimated_rate,
        "estimated_current_cost": round(current_kwh * estimated_rate, 2),
        "billing_summaries": [],
    }


# ═══════════════════════════════════════════════════════════════
# AI SAVING TIPS (Emergent LLM Key → GPT-4o)
# ═══════════════════════════════════════════════════════════════

@router.get("/tenant/xcel/saving-tips")
async def tenant_xcel_saving_tips(request: Request, refresh: bool = False):
    """Generate personalized energy-saving tips based on the tenant's
    consumption pattern. Uses Emergent LLM Key (GPT-4o). Cached 24h per user.
    """
    user = await auth_marketplace(request)
    db = get_db()
    user_id = str(user["_id"])

    # Cache hit (24h)
    if not refresh:
        cached = await db.xcel_saving_tips_cache.find_one({"user_id": user_id})
        if cached:
            age = datetime.now(timezone.utc) - cached["generated_at"]
            if age < timedelta(hours=24):
                return {
                    "tips": cached["tips"],
                    "generated_at": cached["generated_at"].isoformat(),
                    "cached": True,
                    "is_demo": cached.get("is_demo", False),
                }

    # Pull current usage payload (demo or real) to feed the LLM
    contract = await _find_active_contract_for_user(user)
    property_id = str(contract["property_id"]) if contract and contract.get("property_id") else None

    # Build a compact summary of the last 30 days + the last 6 months
    daily_kwh = []
    monthly_kwh = []
    is_demo_data = False

    if property_id:
        now_utc = datetime.now(timezone.utc)
        cutoff = (now_utc - timedelta(days=30)).strftime("%Y-%m-%d")
        async for doc in db.xcel_usage_daily.find({
            "property_id": property_id,
            "date": {"$gte": cutoff},
        }).sort("date", 1):
            daily_kwh.append({"d": doc["date"], "k": round(doc.get("kwh", 0.0), 2)})

        monthly_agg: dict = {}
        async for doc in db.xcel_usage_daily.find({"property_id": property_id}).sort("date", 1):
            m = doc["date"][:7]
            monthly_agg[m] = monthly_agg.get(m, 0.0) + doc.get("kwh", 0.0)
        monthly_kwh = sorted(
            [{"m": k, "k": round(v, 2)} for k, v in monthly_agg.items()]
        )[-6:]

    if not daily_kwh:
        # Demo fallback
        is_demo_data = True
        today = datetime.now(timezone.utc).date()
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            daily_kwh.append({"d": d.isoformat(), "k": _demo_kwh_for_day(d)})

    # Compute simple insights to help the LLM be specific
    total_30d = round(sum(x["k"] for x in daily_kwh), 1)
    avg_day = round(total_30d / max(len(daily_kwh), 1), 1)
    peak_day = max(daily_kwh, key=lambda x: x["k"]) if daily_kwh else {"d": "", "k": 0}
    weekend_kwh = sum(x["k"] for x in daily_kwh
                      if datetime.fromisoformat(x["d"]).weekday() >= 5)
    weekday_kwh = sum(x["k"] for x in daily_kwh
                      if datetime.fromisoformat(x["d"]).weekday() < 5)

    insights = {
        "total_30d_kwh": total_30d,
        "avg_daily_kwh": avg_day,
        "peak_day": peak_day,
        "weekend_total_kwh": round(weekend_kwh, 1),
        "weekday_total_kwh": round(weekday_kwh, 1),
        "current_month": datetime.now(timezone.utc).strftime("%Y-%m"),
    }

    # Call Emergent LLM (GPT-4o)
    tips = await _generate_tips_with_llm(insights, daily_kwh, monthly_kwh)

    # Cache
    await db.xcel_saving_tips_cache.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "tips": tips,
            "generated_at": datetime.now(timezone.utc),
            "is_demo": is_demo_data,
            "insights": insights,
        }},
        upsert=True,
    )

    return {
        "tips": tips,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "is_demo": is_demo_data,
    }


async def _generate_tips_with_llm(insights: dict, daily_kwh: list, monthly_kwh: list) -> list:
    """Call Emergent LLM (GPT-4o) to produce ≥3 personalized Spanish tips."""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
    except Exception as e:
        logger.warning(f"emergentintegrations not available: {e}")
        return _fallback_tips(insights)

    api_key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not api_key:
        return _fallback_tips(insights)

    system = (
        "Eres un asistente experto en eficiencia energética residencial. "
        "Analiza el patrón de consumo de un inquilino y entrega entre 3 y 5 "
        "consejos prácticos, específicos y accionables PARA EL ESPAÑOL. "
        "Cada consejo debe ser corto (máximo 2 frases) y enfocado en ahorrar "
        "kWh sin sacrificar confort. Responde ÚNICAMENTE con un JSON válido "
        "con el siguiente formato exacto: "
        '{"tips":[{"title":"...","detail":"...","saving":"~$XX/mes","icon":"bulb|snow|water|tv|home|leaf"}]}.'
    )

    user_prompt = (
        f"Analiza este patrón de consumo eléctrico de los últimos 30 días en una propiedad "
        f"residencial en Dumas, TX (clima de panhandle de Texas: veranos calurosos, inviernos fríos).\n\n"
        f"INSIGHTS:\n{json.dumps(insights, ensure_ascii=False)}\n\n"
        f"CONSUMO MENSUAL (kWh):\n{json.dumps(monthly_kwh, ensure_ascii=False)}\n\n"
        f"Genera 4 consejos personalizados en español que ayuden a reducir el consumo. "
        f"Si el consumo de fin de semana es notablemente mayor que entre semana, menciónalo. "
        f"Si hay un pico de consumo en un día específico, menciónalo. "
        f"Tarifa aproximada: $0.14/kWh."
    )

    try:
        chat = (
            LlmChat(
                api_key=api_key,
                session_id=f"xcel-tips-{insights.get('current_month')}",
                system_message=system,
            )
            .with_model("openai", "gpt-4o")
            .with_max_tokens(900)
        )
        response = await chat.send_message(UserMessage(text=user_prompt))
        raw = (response or "").strip()
        # Strip optional ```json fences
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        parsed = json.loads(raw)
        tips = parsed.get("tips", [])
        if isinstance(tips, list) and tips:
            return tips[:5]
    except Exception as e:
        logger.exception(f"LLM tips generation failed: {e}")

    return _fallback_tips(insights)


def _fallback_tips(insights: dict) -> list:
    """Static safe fallback in case the LLM call fails (no key, network, etc.)."""
    return [
        {
            "title": "Sube el termostato 1°C en verano",
            "detail": "Cada grado extra en aire acondicionado representa ~6-8% menos consumo. Pon el A/C en 25-26°C cuando no estés en casa.",
            "saving": "~$15/mes",
            "icon": "snow",
        },
        {
            "title": "Desenchufa electrónicos en modo standby",
            "detail": "TVs, microondas y cargadores consumen energía aún apagados. Usa una regleta y apágala al salir.",
            "saving": "~$8/mes",
            "icon": "tv",
        },
        {
            "title": "Cambia a bombillas LED",
            "detail": "Las LED consumen 75% menos que las incandescentes y duran 25 veces más.",
            "saving": "~$10/mes",
            "icon": "bulb",
        },
        {
            "title": "Lava ropa con agua fría",
            "detail": "El 90% del consumo de la lavadora es para calentar agua. Usa agua fría y ciclo eco.",
            "saving": "~$6/mes",
            "icon": "water",
        },
    ]


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
        # Log the failed attempt for diagnostics
        await db.xcel_audit_log.insert_one({
            "event": "exchange_state_not_found",
            "state_prefix": (state or "")[:12],
            "code_prefix": (code or "")[:12],
            "at": datetime.now(timezone.utc),
        })
        return False, "Enlace expirado", "El enlace de autorización expiró o ya fue usado. Genera uno nuevo desde el panel."
    created = st["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(minutes=30):
        await db.xcel_oauth_states.delete_one({"_id": st["_id"]})
        return False, "Enlace expirado", "Genera un nuevo enlace desde el panel de Energía."

    # ESPI / NAESB spec requires HTTP Basic Auth on the token endpoint.
    # Some Xcel/ESPI servers silently reject form-body credentials.
    auth = httpx.BasicAuth(XCEL_CLIENT_ID, XCEL_CLIENT_SECRET)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": XCEL_REDIRECT_URI,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    logger.info(f"[Xcel] Token exchange attempt: state={state[:12]}... prop={st.get('property_id')}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(XCEL_TOKEN_URL, data=data, auth=auth, headers=headers)
    except Exception as e:
        logger.exception(f"[Xcel] Token exchange network error: {e}")
        await db.xcel_audit_log.insert_one({
            "event": "exchange_network_error",
            "state_prefix": state[:12],
            "property_id": st.get("property_id"),
            "error": str(e)[:500],
            "at": datetime.now(timezone.utc),
        })
        return False, "Error de red", f"No se pudo contactar Xcel Energy: {str(e)[:200]}"

    # Persist full diagnostic record regardless of outcome
    await db.xcel_audit_log.insert_one({
        "event": "exchange_response",
        "state_prefix": state[:12],
        "property_id": st.get("property_id"),
        "status_code": resp.status_code,
        "response_body": resp.text[:2000],
        "response_headers": dict(resp.headers),
        "at": datetime.now(timezone.utc),
    })

    if resp.status_code != 200:
        logger.error(f"[Xcel] Token exchange failed: {resp.status_code} body={resp.text[:500]}")
        # Don't delete the state — let admin debug
        return False, "Error al conectar", (
            f"Xcel rechazó el intercambio (HTTP {resp.status_code}). "
            f"Detalle: {resp.text[:200]}. Verifica el audit log en /api/admin/xcel/audit-log."
        )

    try:
        tj = resp.json()
    except Exception:
        logger.error(f"[Xcel] Token response is not JSON: {resp.text[:300]}")
        return False, "Respuesta inesperada", "Xcel devolvió una respuesta no-JSON. Revisa el audit log."

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
    logger.info(f"[Xcel] ✅ Connection saved for property {st['property_id']} sub={doc['subscription_id']}")
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
    """Public Notification URL — Xcel POSTs here when a Batch is ready.

    Per the GBC Vendor Startup Guide, when Xcel finishes preparing a Batch
    XML file it calls our Notification URL with an Atom feed pointing to the
    ready resource. The body looks like:

        <?xml version="1.0" encoding="UTF-8"?>
        <ns1:BatchList xmlns:ns1="http://naesb.org/espi">
          <ns1:resources>
            https://.../Batch/Subscription/{sub_id}?file_id=XYZ
          </ns1:resources>
        </ns1:BatchList>

    We extract the resource URL(s), download with our client_credentials
    token and ingest the ESPI XML into MongoDB.
    """
    db = get_db()
    raw = await request.body()
    body = raw[:20000].decode("utf-8", errors="replace")
    now = datetime.now(timezone.utc)

    notif_doc = {
        "received_at": now,
        "body": body,
        "processed": False,
        "resources_found": [],
        "ingest_results": [],
        "errors": [],
    }

    # Extract one or more <resources>URL</resources> entries.
    resource_urls = []
    try:
        root = ET.fromstring(body)
        for r in root.iter():
            if r.tag.endswith("resources") and r.text and r.text.strip().startswith("http"):
                resource_urls.append(r.text.strip())
    except ET.ParseError:
        # Fall back to a regex sweep so we still attempt processing
        import re as _re
        resource_urls = _re.findall(r"https?://[^\s<>\"']+/Batch/[^\s<>\"']+", body)

    notif_doc["resources_found"] = resource_urls

    if not resource_urls:
        await db.xcel_notifications.insert_one(notif_doc)
        logger.warning(f"[Xcel] Notification received but no resource URLs found. Body={body[:300]}")
        return {"status": "ok", "resources_found": 0,
                "message": "No resource URLs found in notification body"}

    # Process each resource URL: download XML, ingest into Mongo.
    try:
        client_token = await _get_client_credentials_token()
    except HTTPException as e:
        notif_doc["errors"].append(f"client_credentials_failed: {e.detail}")
        await db.xcel_notifications.insert_one(notif_doc)
        logger.error(f"[Xcel] Cannot obtain client_credentials token: {e.detail}")
        return {"status": "error", "detail": str(e.detail)}

    headers = {"Authorization": f"Bearer {client_token}", "Accept": "application/atom+xml"}
    async with httpx.AsyncClient(timeout=60) as client:
        for url in resource_urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    notif_doc["errors"].append(
                        f"download_failed {resp.status_code} for {url}: {resp.text[:200]}"
                    )
                    continue
                # Resolve the property_id from the URL (Subscription/{sub_id})
                sub_match = None
                if "Subscription/" in url:
                    sub_match = url.split("Subscription/", 1)[1].split("/")[0].split("?")[0]
                property_id = None
                if sub_match:
                    conn = await db.xcel_connections.find_one({"subscription_id": sub_match})
                    if conn:
                        property_id = conn["property_id"]
                if not property_id:
                    notif_doc["errors"].append(
                        f"property_lookup_failed sub={sub_match} for {url}"
                    )
                    continue
                result = await _ingest_espi_xml(property_id, resp.text, source="notification_webhook")
                notif_doc["ingest_results"].append({"url": url, "property_id": property_id, **result})
            except Exception as e:
                notif_doc["errors"].append(f"download_exception for {url}: {str(e)[:200]}")

    notif_doc["processed"] = True
    await db.xcel_notifications.insert_one(notif_doc)

    logger.info(
        f"[Xcel] Notification processed: resources={len(resource_urls)} "
        f"ingested={len(notif_doc['ingest_results'])} errors={len(notif_doc['errors'])}"
    )
    return {
        "status": "ok",
        "resources_found": len(resource_urls),
        "ingested": len(notif_doc["ingest_results"]),
        "errors": len(notif_doc["errors"]),
    }


@router.get("/xcel/notify")
@router.get("/greenbutton/notify")
@router.head("/xcel/notify")
@router.head("/greenbutton/notify")
async def xcel_notify_probe(request: Request):
    """Connectivity probe endpoint.
    Xcel / Green Button Alliance validators may hit the Notification URL with
    GET or HEAD before approving the Service Provider. Return 200 OK so the
    endpoint is reported as reachable. Real data delivery still happens over POST."""
    return {
        "status": "ok",
        "service": "Ross House Rentals - Green Button Connect",
        "endpoint": "notification",
        "method_allowed": ["POST"],
        "version": "1.1",
    }
