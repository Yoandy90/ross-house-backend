"""🛰️ Radar de Clientes — Monitoreo de señales sobre clientes de otras empresas.

Señales automáticas (cron semanal, martes):
  💰 tax_debt   — nombre/dirección coincide con leads delincuentes del Deal Finder
  🔨 struckoff  — coincide con leads tipo tax sale/struck-off
  ⛓️ tdcj       — aparece en el buscador de reclusos estatal de Texas (TDCJ)
  🚔 jail       — aparece en rosters de cárceles de condado (URLs configurables)
  🕊️ deceased   — coincide con los obituarios ya escaneados (cron de obituarios)
  📵 phone_dead — Twilio Lookup indica línea desconectada/inactiva
  🧊 ice        — señal MANUAL (botón que abre locator.ice.gov con el A-number copiado)
"""
import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from .shared import get_db, auth_admin

logger = logging.getLogger("client_radar")
router = APIRouter(tags=["client-radar"])

SIGNAL_WEIGHTS = {"ice": 45, "tdcj": 40, "jail": 40, "struckoff": 35,
                  "tax_debt": 30, "deceased": 25, "phone_dead": 15}

FIELDS = ("full_name", "dob", "address", "city", "email", "phone", "a_number",
          "nationality", "passport", "source_business", "alt_contact_name",
          "alt_contact_phone", "notes")

HEADER_MAP = {
    "nombre": "full_name", "nombre completo": "full_name", "name": "full_name",
    "full name": "full_name", "fecha de nacimiento": "dob", "dob": "dob",
    "direccion": "address", "dirección": "address", "address": "address",
    "ciudad": "city", "city": "city", "email": "email", "correo": "email",
    "telefono": "phone", "teléfono": "phone", "phone": "phone",
    "a number": "a_number", "a-number": "a_number", "anumber": "a_number",
    "nacionalidad": "nationality", "nationality": "nationality",
    "pasaporte": "passport", "passport": "passport", "numero de pasaporte": "passport",
    "empresa": "source_business", "source": "source_business", "negocio": "source_business",
    "contacto alterno": "alt_contact_name", "telefono alterno": "alt_contact_phone",
    "notas": "notes", "notes": "notes",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _score(signals: dict) -> int:
    return min(100, sum(SIGNAL_WEIGHTS.get(k, 10) for k, v in signals.items()
                        if v and v.get("active")))


def _serialize(c: dict) -> dict:
    c["_id"] = str(c["_id"])
    # enmascarar datos sensibles en listados
    for k in ("a_number", "passport"):
        v = c.get(k) or ""
        c[f"{k}_masked"] = ("••••" + v[-4:]) if len(v) > 4 else ("SET" if v else "")
    return c


# ─────────────────────────── CRUD ───────────────────────────

@router.get("/admin/client-radar/clients")
async def list_clients(request: Request, signal: str = "", q: str = ""):
    await auth_admin(request)
    query: dict = {}
    if signal:
        query[f"signals.{signal}.active"] = True
    if q:
        query["$or"] = [{"full_name": {"$regex": q, "$options": "i"}},
                        {"address": {"$regex": q, "$options": "i"}}]
    items = []
    async for c in get_db().radar_clients.find(query).sort("score", -1).limit(500):
        items.append(_serialize(c))
    total = await get_db().radar_clients.count_documents({})
    with_signals = await get_db().radar_clients.count_documents({"score": {"$gt": 0}})
    return {"success": True, "items": items, "total": total, "with_signals": with_signals}


@router.post("/admin/client-radar/clients")
async def create_client(request: Request):
    await auth_admin(request)
    data = await request.json()
    if not data.get("full_name"):
        raise HTTPException(400, "full_name es requerido")
    doc = {k: (data.get(k) or "").strip() for k in FIELDS}
    doc.update({"signals": {}, "score": 0, "created_at": datetime.now(timezone.utc),
                "last_scan": None})
    r = await get_db().radar_clients.insert_one(doc)
    return {"success": True, "id": str(r.inserted_id)}


@router.patch("/admin/client-radar/clients/{cid}")
async def update_client(cid: str, request: Request):
    await auth_admin(request)
    data = await request.json()
    update = {k: (data[k] or "").strip() for k in FIELDS if k in data}
    # señal manual ICE
    if "ice_active" in data:
        update["signals.ice"] = {"active": bool(data["ice_active"]),
                                 "detail": data.get("ice_detail", "Marcado manualmente"),
                                 "at": datetime.now(timezone.utc).isoformat()}
    if not update:
        raise HTTPException(400, "Nada que actualizar")
    await get_db().radar_clients.update_one({"_id": ObjectId(cid)}, {"$set": update})
    c = await get_db().radar_clients.find_one({"_id": ObjectId(cid)})
    await get_db().radar_clients.update_one({"_id": ObjectId(cid)},
                                            {"$set": {"score": _score(c.get("signals", {}))}})
    return {"success": True}


@router.delete("/admin/client-radar/clients/{cid}")
async def delete_client(cid: str, request: Request):
    await auth_admin(request)
    await get_db().radar_clients.delete_one({"_id": ObjectId(cid)})
    return {"success": True}


@router.post("/admin/client-radar/import")
async def import_csv(request: Request):
    """Importa CSV crudo (texto) con auto-mapeo de encabezados ES/EN."""
    await auth_admin(request)
    data = await request.json()
    raw = data.get("csv_text", "")
    if not raw.strip():
        raise HTTPException(400, "CSV vacío")
    reader = csv.DictReader(io.StringIO(raw))
    db = get_db()
    inserted = skipped = 0
    for row in reader:
        doc = {}
        for h, v in row.items():
            key = HEADER_MAP.get(_norm(h or ""))
            if key and v:
                doc[key] = str(v).strip()
        if not doc.get("full_name"):
            skipped += 1
            continue
        dup = await db.radar_clients.find_one({"full_name": {"$regex": f"^{re.escape(doc['full_name'])}$", "$options": "i"}})
        if dup:
            skipped += 1
            continue
        doc.update({k: doc.get(k, "") for k in FIELDS})
        doc.update({"signals": {}, "score": 0, "created_at": datetime.now(timezone.utc),
                    "last_scan": None})
        await db.radar_clients.insert_one(doc)
        inserted += 1
    return {"success": True, "inserted": inserted, "skipped": skipped}


# ─────────────────────── MOTOR DE SEÑALES ───────────────────────

async def _sig_deal_finder(db, client) -> dict:
    """Cruza contra leads del Deal Finder (delincuencia fiscal / struck-off)."""
    out = {}
    name, street = _norm(client.get("full_name")), _norm(client.get("address"))[:25]
    ors = []
    if name:
        ors.append({"owner_name": {"$regex": re.escape(client["full_name"]), "$options": "i"}})
    if street and len(street) > 8:
        ors.append({"address": {"$regex": re.escape(client["address"][:25]), "$options": "i"}})
    if not ors:
        return out
    async for lead in db.leads.find({"$or": ors}).limit(5):
        classes = " ".join(str(x) for x in (lead.get("property_classes") or [])).lower()
        sig = str(lead.get("signal") or "").lower()
        detail = f"Lead: {lead.get('address', '')} ({lead.get('signal', '')})"
        if "struck" in classes + sig or "tax sale" in classes + sig:
            out["struckoff"] = {"active": True, "detail": detail}
        else:
            out["tax_debt"] = {"active": True, "detail": detail}
    return out


async def _sig_tdcj(client) -> dict:
    """Busca en el buscador público de reclusos estatales de Texas."""
    parts = (client.get("full_name") or "").split()
    if len(parts) < 2:
        return {}
    first, last = parts[0], parts[-1]
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as x:
            r = await x.get("https://inmate.tdcj.texas.gov/InmateSearch/search.action",
                            params={"firstName": first, "lastName": last, "gender": "ALL",
                                    "race": "ALL", "btnSearch": "Search"})
        if r.status_code == 200 and last.upper() in r.text.upper() \
                and "tdcj number" in r.text.lower():
            rows = r.text.upper().count(last.upper())
            return {"tdcj": {"active": True,
                             "detail": f"Posible match en TDCJ ({rows} coincidencias) — verificar con DOB"}}
    except Exception as e:  # noqa: BLE001
        logger.debug("TDCJ scan falló: %s", e)
    return {}


async def _sig_jail(db, client) -> dict:
    """Rosters de cárceles de condado (URLs configurables en app_settings)."""
    cfg = await db.app_settings.find_one({"_id": "client_radar_config"}) or {}
    urls = cfg.get("jail_roster_urls") or []
    name = client.get("full_name", "")
    if not urls or len(name.split()) < 2:
        return {}
    last = name.split()[-1].upper()
    first = name.split()[0].upper()
    for url in urls[:5]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as x:
                r = await x.get(url)
            text = r.text.upper()
            if last in text and first in text:
                return {"jail": {"active": True, "detail": f"Posible match en roster: {url}"}}
        except Exception:  # noqa: BLE001
            continue
    return {}


async def _sig_obituaries(db, client) -> dict:
    cfg = await db.app_settings.find_one({"_id": "obituary_scan_cron"}) or {}
    name = _norm(client.get("full_name"))
    if not name:
        return {}
    parts = name.split()
    for o in cfg.get("last_obituaries", []):
        oname = _norm(o.get("name", ""))
        if len(parts) >= 2 and parts[0] in oname and parts[-1] in oname:
            return {"deceased": {"active": True,
                                 "detail": f"Obituario: {o.get('name')} ({o.get('city', '')}, {o.get('date', '')})"}}
    return {}


async def _sig_phone(client) -> dict:
    phone = client.get("phone")
    if not phone:
        return {}
    try:
        from .contact_enrichment_router import twilio_line_lookup
        info = await twilio_line_lookup(phone)
        if info and str(info.get("status", "")).lower() in ("inactive", "deactivated", "disconnected"):
            return {"phone_dead": {"active": True,
                                   "detail": f"Línea {info.get('status')} ({info.get('line_type', '')})"}}
    except Exception as e:  # noqa: BLE001
        logger.debug("phone scan falló: %s", e)
    return {}


async def scan_client(db, client: dict) -> dict:
    """Corre todas las señales sobre un cliente y actualiza su doc."""
    signals = dict(client.get("signals") or {})
    manual_ice = signals.get("ice")  # la señal ICE manual se preserva
    new = {}
    for coro in (_sig_deal_finder(db, client), _sig_tdcj(client),
                 _sig_jail(db, client), _sig_obituaries(db, client), _sig_phone(client)):
        try:
            new.update(await coro)
        except Exception as e:  # noqa: BLE001
            logger.warning("señal falló para %s: %s", client.get("full_name"), e)
    ts = datetime.now(timezone.utc).isoformat()
    for k, v in new.items():
        v["at"] = ts
    fresh = {k: v for k, v in new.items() if not (signals.get(k) or {}).get("active")}
    signals.update(new)
    if manual_ice:
        signals["ice"] = manual_ice
    score = _score(signals)
    await db.radar_clients.update_one({"_id": client["_id"]}, {"$set": {
        "signals": signals, "score": score,
        "last_scan": datetime.now(timezone.utc)}})
    return {"client": client.get("full_name"), "new_signals": list(fresh.keys()), "score": score}


@router.post("/admin/client-radar/scan")
async def scan_now(request: Request):
    """Escanea todos los clientes (o uno con {client_id})."""
    await auth_admin(request)
    data = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    db = get_db()
    q = {"_id": ObjectId(data["client_id"])} if data.get("client_id") else {}
    results = []
    async for c in db.radar_clients.find(q).limit(300):
        results.append(await scan_client(db, c))
    with_new = [r for r in results if r["new_signals"]]
    return {"success": True, "scanned": len(results), "with_new_signals": with_new}


# ─────────────────────── CRON SEMANAL (martes) ───────────────────────

async def _radar_should_run(db) -> bool:
    now = datetime.now(timezone.utc)
    if now.weekday() != 1 or now.hour < 14:  # martes ≥ 14:00 UTC (9 AM CT)
        return False
    cfg = await db.app_settings.find_one({"_id": "client_radar_cron"}) or {}
    last = cfg.get("last_run")
    return not (last and str(last)[:10] == now.strftime("%Y-%m-%d"))


async def client_radar_scan_loop():
    from .shared import get_db as _gd
    await asyncio.sleep(120)
    while True:
        try:
            db = _gd()
            if db is not None and await _radar_should_run(db):
                results = []
                async for c in db.radar_clients.find({}).limit(300):
                    results.append(await scan_client(db, c))
                news = [r for r in results if r["new_signals"]]
                await db.app_settings.update_one(
                    {"_id": "client_radar_cron"},
                    {"$set": {"last_run": datetime.now(timezone.utc).isoformat(),
                              "scanned": len(results), "new": len(news)}}, upsert=True)
                if news:
                    try:
                        from .ai_brain_router import _send_email_branded
                        rows = "".join(
                            f"<li><b>{n['client']}</b> — señales nuevas: "
                            f"{', '.join(n['new_signals'])} (score {n['score']})</li>" for n in news)
                        await _send_email_branded(
                            "yoandyross@gmail.com",
                            f"🛰️ Radar de Clientes: {len(news)} con señales nuevas",
                            f"<h3>🛰️ Radar de Clientes — escaneo semanal</h3><ul>{rows}</ul>"
                            f"<p>Revisa el panel → Radar de Clientes para ver detalles y crear oportunidades.</p>",
                            f"{len(news)} clientes con señales nuevas")
                    except Exception as e:  # noqa: BLE001
                        logger.warning("email radar falló: %s", e)
                logger.info("🛰️ Radar de clientes: %s escaneados, %s con señales nuevas",
                            len(results), len(news))
        except Exception as e:  # noqa: BLE001
            logger.warning("client_radar_scan_loop error: %s", e)
        await asyncio.sleep(3600)


@router.post("/admin/client-radar/clients/{cid}/create-lead")
async def create_lead_from_client(cid: str, request: Request):
    """Crea un lead en Oportunidades a partir de un cliente con señales."""
    await auth_admin(request)
    db = get_db()
    c = await db.radar_clients.find_one({"_id": ObjectId(cid)})
    if not c:
        raise HTTPException(404, "Cliente no encontrado")
    active = [k for k, v in (c.get("signals") or {}).items() if v.get("active")]
    lead = {
        "owner_name": c.get("full_name", ""),
        "address": c.get("address", ""),
        "city": c.get("city", ""),
        "signal": "client_radar",
        "property_classes": [f"radar:{s}" for s in active],
        "contact": {"phones": [c["phone"]] if c.get("phone") else [],
                    "emails": [c["email"]] if c.get("email") else []},
        "source": "client_radar",
        "radar_client_id": str(c["_id"]),
        "status": "new",
        "created_at": datetime.now(timezone.utc),
    }
    r = await db.leads.insert_one(lead)
    return {"success": True, "lead_id": str(r.inserted_id)}
