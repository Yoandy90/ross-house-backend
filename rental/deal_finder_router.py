"""
Deal Finder Router — Radar de Oportunidades Off-Market
========================================================
Scrapes the Moore County TX tax portal (BIS Consultants eSearch,
https://esearch.co.moore.tx.us) to find acquisition opportunities:

  1. GET  /search/requestSessionToken            -> searchSessionToken
  2. GET  /search/result?keywords=X&searchSessionToken=Y  -> HTML with <meta name="search-token">
  3. POST /search/SearchResults?keywords=X  (JSON body {page,pageSize,isArb,searchToken})
       -> {resultsList:[{propertyId, ownerName, address, appraisedValue, ...}], totalResults}
  4. GET  /Property/View/{id}?year=Y             -> mailing address + value breakdown
  5. GET  /Property/GetPropertyTaxDueModalResult -> delinquent taxes (reused from property_taxes_router)

Keyword syntax: "OwnerName:smith", "StreetName:maddox", "Subdivision:xyz"
(FieldName:value, quoted if it contains spaces).

Leads are stored in `deal_finder_leads` (upsert by county+property_id, preserving
pipeline status/notes/AI fields across re-scans). Scan runs are tracked in
`deal_finder_scans` and executed as background tasks.

AI scoring & offer letters use Claude via emergentintegrations (EMERGENT_LLM_KEY).
"""
import asyncio
import html as html_lib
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from rental.shared import get_db, auth_admin
from rental.property_taxes_router import fetch_account_tax_due

logger = logging.getLogger(__name__)
router = APIRouter()

UA = {"User-Agent": "Mozilla/5.0 (RossHouseRentals; investment research)"}

# ─── County source registry (extensible) ─────────────────────
COUNTIES = {
    "moore": {
        "name": "Moore County",
        "base": "https://esearch.co.moore.tx.us",
        "active": True,
        "situs_cities": ["DUMAS", "SUNRAY", "CACTUS", "MASTERSON"],
    },
    "dallam": {
        "name": "Dallam County (Dalhart)",
        "base": "https://esearch.dallamcad.org",
        "active": True,
        "situs_cities": ["DALHART", "TEXLINE", "KERRICK"],
    },
    "potter": {
        "name": "Potter-Randall (Amarillo)",
        "base": "https://esearch.prad.org",
        "active": True,
        "platform": "trueprodigy",
        "office": "PotterRandall",
        "situs_cities": ["AMARILLO", "CANYON", "BUSHLAND"],
    },
    # Estos condados usan una plataforma distinta (no BIS eSearch clásico) —
    # requieren scraper propio antes de activarlos:
    "sherman": {"name": "Sherman County", "base": "", "active": False},
    "hartley": {"name": "Hartley County", "base": "https://esearch.hartleycad.org", "active": False},
}

SEARCH_FIELDS = {
    "street": "StreetName",
    "owner": "OwnerName",
    "subdivision": "Subdivision",
    "abstract": "Abstract",
}

# Property types worth skipping by default (minerals / autos are noise)
SKIP_TYPES = {"MN", "A"}

LEAD_STATUSES = ["new", "contacted", "interested", "offer_sent", "negotiating", "acquired", "discarded"]


# ═══════════════════════════════════════════════════════════════
# Scraping helpers (BIS eSearch)
# ═══════════════════════════════════════════════════════════════

async def _open_search_session(client: httpx.AsyncClient, base: str, keywords: str) -> str:
    """Establish a search session and return the page search-token."""
    r = await client.get(f"{base}/search/requestSessionToken")
    r.raise_for_status()
    session_token = r.json().get("searchSessionToken", "")
    r2 = await client.get(f"{base}/search/result",
                          params={"keywords": keywords, "searchSessionToken": session_token})
    r2.raise_for_status()
    m = re.search(r'name="search-token" content="([^"]+)"', r2.text)
    if not m:
        raise RuntimeError("county portal did not return a search token")
    return html_lib.unescape(m.group(1))


async def _search_page(client: httpx.AsyncClient, base: str, keywords: str,
                       search_token: str, page: int, page_size: int = 25) -> dict:
    url = f"{base}/search/SearchResults?keywords={urllib.parse.quote(keywords)}"
    r = await client.post(url, json={
        "page": page, "pageSize": page_size, "isArb": False, "searchToken": search_token,
    })
    r.raise_for_status()
    return r.json()


def _parse_money(txt: str) -> float:
    try:
        return float(txt.replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def parse_property_detail(raw_html: str) -> dict:
    """Extract mailing address + value breakdown from /Property/View/{id}."""
    h = re.sub(r"\s+", " ", raw_html)
    out = {"mailing_lines": [], "mailing_city": "", "mailing_state": "", "mailing_zip": "", "values": {}}

    m = re.search(r"Mailing Address:</th>\s*<td[^>]*>(.*?)</td>", h, re.I)
    if m:
        lines = [re.sub(r"<[^>]+>", "", part).strip()
                 for part in re.split(r"<br\s*/?>", m.group(1))]
        lines = [ln for ln in lines if ln]
        out["mailing_lines"] = lines
        if lines:
            last = lines[-1]
            cm = re.search(r"^(.*?)\s*,\s*([A-Z]{2})\s*([\d-]*)\s*$", last)
            if cm:
                out["mailing_city"] = cm.group(1).strip().upper()
                out["mailing_state"] = cm.group(2).strip().upper()
                out["mailing_zip"] = cm.group(3).strip()

    # Value rows: <th>Label:</th><td class="table-number">$123,456</td>
    for label, val in re.findall(r"<th>([^<]{2,60}?):</th>\s*<td class=\"table-number\">\s*\$?([\d,.\-]+)", h):
        out["values"][label.strip()] = _parse_money(val)

    return out


def _situs_city(address: str) -> str:
    """'620 S MADDOX, DUMAS TX 79029' -> 'DUMAS'"""
    m = re.search(r",\s*([A-Z .\-']+?)\s+TX\b", (address or "").upper())
    return m.group(1).strip() if m else ""


def compute_signals(lead: dict) -> list[str]:
    signals = []
    if (lead.get("tax_due_total") or 0) > 0:
        signals.append("tax_delinquent")

    m_state = lead.get("mailing_state") or ""
    m_city = lead.get("mailing_city") or ""
    s_city = _situs_city(lead.get("address") or "")
    if m_state and m_state != "TX":
        signals.append("out_of_state_owner")
        signals.append("absentee_owner")
    elif m_city and s_city and m_city != s_city:
        signals.append("absentee_owner")

    values = lead.get("values") or {}
    improvement = sum(v for k, v in values.items() if "improvement" in k.lower())
    land = sum(v for k, v in values.items() if "land" in k.lower())
    market = values.get("Market Value") or lead.get("appraised_value") or 0
    ptype = (lead.get("property_type") or "").upper()

    if ptype.startswith("R"):
        if land > 0 and improvement <= 0:
            signals.append("vacant_land")
        elif market > 0 and improvement > 0 and improvement / market < 0.25:
            signals.append("low_improvement")
        if 0 < market < 60000:
            signals.append("low_value")
    return signals


# ═══════════════════════════════════════════════════════════════
# Enrichment (shared by manual scans and the auto-scan cron)
# ═══════════════════════════════════════════════════════════════

async def enrich_and_upsert(db, client: httpx.AsyncClient, base: str, county: str,
                            item: dict, only_delinquent: bool = False) -> tuple[str, dict, bool]:
    """Enrich one search-result item (detail page + delinquent taxes), compute
    signals and upsert into deal_finder_leads.
    Returns (outcome 'new'|'updated'|'skipped', lead, became_delinquent)."""
    prop_id = str(item.get("propertyId") or "").strip()
    if not prop_id:
        return "skipped", {}, False
    year = item.get("year") or datetime.now().year
    lead = {
        "county": county,
        "property_id": prop_id,
        "year": year,
        "geo_id": (item.get("geoId") or "").strip(),
        "owner_name": (item.get("ownerName") or "").strip(),
        "owner_id": str(item.get("ownerId") or ""),
        "address": (item.get("address") or "").strip(),
        "legal_description": (item.get("legalDescription") or "").strip(),
        "property_type": (item.get("propertyTypeCode") or "").strip(),
        "appraised_value": item.get("appraisedValue") or 0,
        "portal_url": f"{base}/Property/View/{prop_id}",
    }

    # Enrich: detail page (mailing + values)
    try:
        rd = await client.get(f"{base}/Property/View/{prop_id}", params={"year": year})
        if rd.status_code == 200:
            lead.update(parse_property_detail(rd.text))
    except Exception as e:
        logger.warning(f"[deal_finder] detail failed {prop_id}: {e}")
    await asyncio.sleep(0.5)

    # Enrich: delinquent taxes
    try:
        tax = await fetch_account_tax_due(prop_id, base)
        lead["tax_due_total"] = tax["total_due"]
        lead["tax_years_due"] = [y["year"] for y in tax["years_due"]]
    except Exception as e:
        logger.warning(f"[deal_finder] tax due failed {prop_id}: {e}")
        lead["tax_due_total"] = 0
        lead["tax_years_due"] = []
    await asyncio.sleep(0.5)

    lead["signals"] = compute_signals(lead)
    lead["last_synced_at"] = datetime.now(timezone.utc)

    if only_delinquent and "tax_delinquent" not in lead["signals"]:
        return "skipped", lead, False

    prev = await db.deal_finder_leads.find_one(
        {"county": county, "property_id": prop_id}, {"signals": 1})
    became_delinquent = bool(prev) and "tax_delinquent" not in (prev.get("signals") or []) \
        and "tax_delinquent" in lead["signals"]

    res = await db.deal_finder_leads.update_one(
        {"county": county, "property_id": prop_id},
        {"$set": lead,
         "$setOnInsert": {"status": "new", "notes": "",
                          "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return ("new" if res.upserted_id else "updated"), lead, became_delinquent


# ═══════════════════════════════════════════════════════════════
# Scan engine (background task)
# ═══════════════════════════════════════════════════════════════

async def _run_scan(scan_id: str, county: str, keywords: str,
                    max_results: int, only_delinquent: bool):
    db = get_db()
    base = COUNTIES[county]["base"]
    oid = ObjectId(scan_id)

    async def _update(**fields):
        await db.deal_finder_scans.update_one({"_id": oid}, {"$set": fields})

    try:
        async with httpx.AsyncClient(timeout=40, headers=UA, follow_redirects=True) as client:
            token = await _open_search_session(client, base, keywords)

            # Collect search results across pages
            results, page = [], 1
            while len(results) < max_results:
                data = await _search_page(client, base, keywords, token, page)
                items = data.get("resultsList") or []
                if not items:
                    break
                results.extend(items)
                total_pages = data.get("totalPages") or 1
                await _update(total_found=data.get("totalResults") or len(results))
                if page >= total_pages:
                    break
                page += 1
                await asyncio.sleep(0.8)

            results = [r for r in results
                       if (r.get("propertyTypeCode") or "").upper() not in SKIP_TYPES][:max_results]
            await _update(total=len(results), status="enriching")

            new_leads = updated = processed = 0
            for item in results:
                outcome, _lead, _became = await enrich_and_upsert(
                    db, client, base, county, item, only_delinquent)
                processed += 1
                await _update(processed=processed)
                if outcome == "new":
                    new_leads += 1
                elif outcome == "updated":
                    updated += 1

            await _update(status="done", new_leads=new_leads, updated=updated,
                          finished_at=datetime.now(timezone.utc))
            logger.info(f"[deal_finder] scan {scan_id} done: {new_leads} new, {updated} updated")
    except Exception as e:
        logger.error(f"[deal_finder] scan {scan_id} failed: {e}")
        await _update(status="error", error=str(e), finished_at=datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════════
# AI (Claude via emergentintegrations)
# ═══════════════════════════════════════════════════════════════

ANALYZE_PROMPT = """Eres un analista de inversiones inmobiliarias experto en el Panhandle de Texas \
(Dumas, Sunray, Cactus, Amarillo). Recibirás datos públicos de una propiedad del condado \
(valores de tasación, impuestos atrasados, dirección postal del dueño vs. ubicación de la propiedad, señales detectadas).

Evalúa qué tan buena oportunidad off-market es para contactar al dueño y comprar con descuento.
Considera: impuestos atrasados (motivación de venta), dueño ausente/fuera del estado, terreno baldío,
valor de mejora bajo (posible abandono), y valor total.

Responde SOLO con JSON válido:
{
 "score": <0-100, 100 = oportunidad excelente>,
 "veredicto": "<1 frase en español>",
 "razones": ["<razón 1>", "<razón 2>", ...],
 "estrategia": "<estrategia de adquisición sugerida en español, 1-2 frases>",
 "oferta_sugerida_pct": <número, % del valor de mercado a ofrecer, ej. 60>
}"""

LETTER_PROMPT = """You write short, warm, professional direct-mail letters for a Texas real estate \
investor (Ross House Rentals LLC, Dumas TX) offering to buy a property directly from the owner.
Rules: friendly, no pressure, mention we buy AS-IS, cash, we pay closing costs, quick close.
NEVER mention delinquent taxes, financial distress or anything that could embarrass the owner.
Include placeholders [TELÉFONO] and [EMAIL] for contact info.

Respond ONLY with valid JSON:
{
 "letter_en": "<full letter in English, addressed to the owner by name, referencing the property address>",
 "letter_es": "<misma carta en español>"
}"""


async def _ai_json(system: str, payload: dict) -> dict:
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(500, "EMERGENT_LLM_KEY not configured")
    from uuid import uuid4
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    from rental.ai_brain_router import MODEL_PROVIDER, MODEL_NAME
    chat = LlmChat(api_key=api_key, session_id=f"dealfinder_{uuid4()}",
                   system_message=system).with_model(MODEL_PROVIDER, MODEL_NAME)
    raw = str(await chat.send_message(
        UserMessage(text=json.dumps(payload, ensure_ascii=False, default=str))))
    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception:
        logger.error(f"[deal_finder] AI respuesta no parseable: {raw[:300]}")
        raise HTTPException(502, "La AI no devolvió una respuesta válida — reintenta")


def _lead_payload(lead: dict) -> dict:
    return {
        "direccion": lead.get("address"),
        "dueno": lead.get("owner_name"),
        "tipo": lead.get("property_type"),
        "descripcion_legal": lead.get("legal_description"),
        "valor_tasado": lead.get("appraised_value"),
        "valores": lead.get("values"),
        "impuestos_atrasados": lead.get("tax_due_total"),
        "anos_atrasados": lead.get("tax_years_due"),
        "direccion_postal_dueno": lead.get("mailing_lines"),
        "ciudad_postal": lead.get("mailing_city"),
        "estado_postal": lead.get("mailing_state"),
        "senales": lead.get("signals"),
    }


# ═══════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════

def _lead_out(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "county": doc.get("county", ""),
        "county_name": COUNTIES.get(doc.get("county", ""), {}).get("name", doc.get("county", "")),
        "property_id": doc.get("property_id", ""),
        "geo_id": doc.get("geo_id", ""),
        "owner_name": doc.get("owner_name", ""),
        "address": doc.get("address", ""),
        "legal_description": doc.get("legal_description", ""),
        "property_type": doc.get("property_type", ""),
        "appraised_value": doc.get("appraised_value", 0),
        "values": doc.get("values", {}),
        "mailing_lines": doc.get("mailing_lines", []),
        "mailing_city": doc.get("mailing_city", ""),
        "mailing_state": doc.get("mailing_state", ""),
        "mailing_zip": doc.get("mailing_zip", ""),
        "tax_due_total": doc.get("tax_due_total", 0),
        "tax_years_due": doc.get("tax_years_due", []),
        "signals": doc.get("signals", []),
        "status": doc.get("status", "new"),
        "notes": doc.get("notes", ""),
        "ai_score": doc.get("ai_score"),
        "ai_analysis": doc.get("ai_analysis"),
        "offer_letter": doc.get("offer_letter"),
        "mail": doc.get("mail"),
        "portal_url": doc.get("portal_url", ""),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else "",
        "last_synced_at": doc["last_synced_at"].isoformat() if doc.get("last_synced_at") else "",
    }


def _scan_out(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "county": doc.get("county", ""),
        "keywords": doc.get("keywords", ""),
        "status": doc.get("status", ""),
        "total_found": doc.get("total_found", 0),
        "total": doc.get("total", 0),
        "processed": doc.get("processed", 0),
        "new_leads": doc.get("new_leads", 0),
        "updated": doc.get("updated", 0),
        "error": doc.get("error", ""),
        "started_at": doc["started_at"].isoformat() if doc.get("started_at") else "",
        "finished_at": doc["finished_at"].isoformat() if doc.get("finished_at") else "",
    }


# ═══════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════

class ScanRequest(BaseModel):
    county: str = "moore"
    search_type: str = "street"  # street | owner | subdivision | abstract
    query: str
    max_results: int = 40
    only_delinquent: bool = False


@router.get("/admin/deal-finder/counties")
async def list_counties(request: Request):
    await auth_admin(request)
    return {"success": True, "counties": [
        {"key": k, "name": v["name"], "active": v["active"]} for k, v in COUNTIES.items()
    ]}


# ═══════════════════════════════════════════════════════════════
# Scraper TrueProdigy (Potter-Randall / Amarillo — esearch.prad.org)
# La búsqueda pública usa prod-container.trueprodigyapi.com con token
# de oficina (sin Bearer) y devuelve el registro completo (situs, dueño,
# mailing y valores) — no requiere página de detalle. La deuda fiscal
# no está disponible en el CAD (la lleva la oficina de impuestos).
# ═══════════════════════════════════════════════════════════════

TP_API = "https://prod-container.trueprodigyapi.com"


async def _tp_token(client: httpx.AsyncClient, office: str) -> str:
    r = await client.post(f"{TP_API}/trueprodigy/cadpublic/auth/token",
                          json={"office": office})
    r.raise_for_status()
    return r.json()["user"]["token"]


async def _tp_search(client: httpx.AsyncClient, token: str, query: str,
                     year: int, page: int, page_size: int = 50) -> dict:
    payload = {
        "pYear": {"operator": "=", "value": str(year)},
        "fullTextSearch": {"operator": "match", "value": query},
    }
    r = await client.post(
        f"{TP_API}/public/property/searchfulltext?page={page}&pageSize={page_size}",
        headers={"Authorization": token}, json=payload)
    r.raise_for_status()
    return r.json()


def _tp_to_lead(county: str, rec: dict) -> dict:
    # "904 S BONHAM ST, AMARILLO, TX, 79102" → "904 S BONHAM ST, AMARILLO TX 79102"
    situs = re.sub(r",\s*TX,?\s*", " TX ", (rec.get("fullSitus") or "")).strip().rstrip(",")
    m_line = (rec.get("addrDeliveryLine") or "").strip()
    m_city = (rec.get("addrCity") or "").strip()
    m_state = (rec.get("addrState") or "").strip().upper()
    m_zip = (rec.get("addrZip") or "").strip()
    lead = {
        "county": county,
        "property_id": str(rec.get("pid") or ""),
        "year": int(rec.get("pYear") or datetime.now().year),
        "geo_id": rec.get("geoID") or "",
        "owner_name": (rec.get("displayName") or rec.get("name") or "").strip(),
        "owner_id": str(rec.get("ownerID") or ""),
        "address": situs,
        "legal_description": rec.get("legalDescription") or "",
        "property_type": (rec.get("propType") or "").strip().upper(),
        "appraised_value": rec.get("appraisedValue") or 0,
        "portal_url": f"https://esearch.prad.org/property-detail/{rec.get('pid')}/{rec.get('pYear')}",
        "mailing_lines": [ln for ln in [m_line, f"{m_city}, {m_state} {m_zip}".strip(", ")] if ln],
        "mailing_city": m_city.upper(),
        "mailing_state": m_state,
        "mailing_zip": m_zip,
        "values": {
            "Land Value": rec.get("landValue") or 0,
            "Improvement Value": rec.get("improvementValue") or 0,
            "Market Value": rec.get("marketValue") or 0,
        },
        "tax_due_total": 0,
        "tax_years_due": [],
        "latitude": rec.get("latitude") or "",
        "longitude": rec.get("longitude") or "",
    }
    lead["signals"] = compute_signals(lead)
    lead["last_synced_at"] = datetime.now(timezone.utc)
    return lead


async def _run_scan_trueprodigy(scan_id: str, county: str, query: str,
                                max_results: int):
    db = get_db()
    office = COUNTIES[county]["office"]
    oid = ObjectId(scan_id)
    year = datetime.now().year

    async def _update(**fields):
        await db.deal_finder_scans.update_one({"_id": oid}, {"$set": fields})

    try:
        async with httpx.AsyncClient(timeout=40, headers=UA) as client:
            token = await _tp_token(client, office)

            results, page = [], 1
            while len(results) < max_results:
                data = await _tp_search(client, token, query, year, page)
                items = data.get("results") or []
                if not items:
                    break
                results.extend(items)
                total = (data.get("totalProperty") or {}).get("propertyCount") or len(results)
                await _update(total_found=total)
                if len(items) < 50 or len(results) >= total:
                    break
                page += 1
                await asyncio.sleep(0.6)

            results = [r for r in results
                       if (r.get("propType") or "").upper() not in SKIP_TYPES
                       and (r.get("active") or "Yes") == "Yes"][:max_results]
            await _update(total=len(results), status="enriching")

            new_leads = updated = processed = 0
            for rec in results:
                lead = _tp_to_lead(county, rec)
                if not lead["property_id"]:
                    continue
                res = await db.deal_finder_leads.update_one(
                    {"county": county, "property_id": lead["property_id"]},
                    {"$set": lead,
                     "$setOnInsert": {"status": "new", "notes": "",
                                      "created_at": datetime.now(timezone.utc)}},
                    upsert=True,
                )
                processed += 1
                await _update(processed=processed)
                if res.upserted_id:
                    new_leads += 1
                else:
                    updated += 1

            await _update(status="done", new_leads=new_leads, updated=updated,
                          finished_at=datetime.now(timezone.utc))
            logger.info(f"[deal_finder] TP scan {scan_id} done: {new_leads} new, {updated} updated")
    except Exception as e:
        logger.error(f"[deal_finder] TP scan {scan_id} failed: {e}")
        await _update(status="error", error=str(e), finished_at=datetime.now(timezone.utc))


@router.post("/admin/deal-finder/scan")
async def start_scan(request: Request, body: ScanRequest):
    await auth_admin(request)
    db = get_db()

    county = COUNTIES.get(body.county)
    if not county or not county["active"]:
        raise HTTPException(400, "Ese condado aún no está disponible — por ahora solo Moore County")
    field = SEARCH_FIELDS.get(body.search_type)
    if not field:
        raise HTTPException(400, f"search_type inválido. Usa: {', '.join(SEARCH_FIELDS)}")
    q = body.query.strip()
    if not q:
        raise HTTPException(400, "Escribe qué buscar (ej. nombre de calle)")

    running = await db.deal_finder_scans.find_one({"status": {"$in": ["searching", "enriching"]}})
    if running:
        raise HTTPException(409, "Ya hay un escaneo en curso — espera a que termine")

    platform = county.get("platform", "esearch")
    if platform == "trueprodigy":
        # búsqueda full-text (dirección, dueño o cuenta en un solo campo)
        keywords = q
        max_results = max(1, min(body.max_results, 200))
        doc = {
            "county": body.county, "keywords": keywords, "status": "searching",
            "total_found": 0, "total": 0, "processed": 0, "new_leads": 0, "updated": 0,
            "only_delinquent": False,
            "started_at": datetime.now(timezone.utc), "finished_at": None, "error": "",
        }
        res = await db.deal_finder_scans.insert_one(doc)
        scan_id = str(res.inserted_id)
        asyncio.create_task(_run_scan_trueprodigy(scan_id, body.county, keywords, max_results))
        return {"success": True, "scan_id": scan_id, "keywords": keywords}

    keywords = f'{field}:"{q}"' if " " in q else f"{field}:{q}"
    max_results = max(1, min(body.max_results, 100))

    doc = {
        "county": body.county, "keywords": keywords, "status": "searching",
        "total_found": 0, "total": 0, "processed": 0, "new_leads": 0, "updated": 0,
        "only_delinquent": body.only_delinquent,
        "started_at": datetime.now(timezone.utc), "finished_at": None, "error": "",
    }
    res = await db.deal_finder_scans.insert_one(doc)
    scan_id = str(res.inserted_id)
    asyncio.create_task(_run_scan(scan_id, body.county, keywords, max_results, body.only_delinquent))
    return {"success": True, "scan_id": scan_id, "keywords": keywords}


@router.get("/admin/deal-finder/scan/{scan_id}")
async def get_scan(request: Request, scan_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_scans.find_one({"_id": ObjectId(scan_id)})
    if not doc:
        raise HTTPException(404, "Scan no encontrado")
    return {"success": True, "scan": _scan_out(doc)}


@router.get("/admin/deal-finder/scans")
async def list_scans(request: Request):
    await auth_admin(request)
    db = get_db()
    docs = await db.deal_finder_scans.find({}).sort("started_at", -1).to_list(20)
    return {"success": True, "scans": [_scan_out(d) for d in docs]}


@router.get("/admin/deal-finder/stats")
async def get_stats(request: Request):
    await auth_admin(request)
    db = get_db()
    total = await db.deal_finder_leads.count_documents({})
    delinquent = await db.deal_finder_leads.count_documents({"signals": "tax_delinquent"})
    absentee = await db.deal_finder_leads.count_documents({"signals": "absentee_owner"})
    vacant = await db.deal_finder_leads.count_documents({"signals": "vacant_land"})
    high_score = await db.deal_finder_leads.count_documents({"ai_score": {"$gte": 70}})
    by_status = {}
    async for row in db.deal_finder_leads.aggregate([
            {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        by_status[row["_id"] or "new"] = row["n"]
    return {"success": True, "stats": {
        "total": total, "tax_delinquent": delinquent, "absentee_owner": absentee,
        "vacant_land": vacant, "high_score": high_score, "by_status": by_status,
    }}


@router.get("/admin/deal-finder/leads")
async def list_leads(request: Request, status: Optional[str] = None,
                     signal: Optional[str] = None, county: Optional[str] = None,
                     q: Optional[str] = None, sort: str = "score",
                     city: Optional[str] = None, min_tax: float = 0,
                     min_score: float = 0, min_value: float = 0,
                     limit: int = 100, skip: int = 0):
    await auth_admin(request)
    db = get_db()
    filt: dict = {}
    if status:
        filt["status"] = status
    if signal:
        filt["signals"] = signal
    if county:
        filt["county"] = county
    if city:
        filt["address"] = {"$regex": re.escape(city.strip()), "$options": "i"}
    if min_tax > 0:
        filt["tax_due_total"] = {"$gte": min_tax}
    if min_score > 0:
        filt["ai_score"] = {"$gte": min_score}
    if min_value > 0:
        filt["appraised_value"] = {"$gte": min_value}
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        filt["$or"] = [{"address": rx}, {"owner_name": rx}, {"legal_description": rx}]

    sort_spec = [("ai_score", -1), ("tax_due_total", -1)] if sort == "score" else \
                [("tax_due_total", -1)] if sort == "tax_due" else \
                [("appraised_value", -1)] if sort == "value" else \
                [("last_synced_at", -1)]
    docs = await db.deal_finder_leads.find(filt).sort(sort_spec).skip(skip).to_list(min(limit, 200))
    total = await db.deal_finder_leads.count_documents(filt)
    return {"success": True, "leads": [_lead_out(d) for d in docs], "total": total}


@router.get("/admin/deal-finder/leads/{lead_id}")
async def get_lead(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    return {"success": True, "lead": _lead_out(doc)}


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@router.patch("/admin/deal-finder/leads/{lead_id}")
async def update_lead(request: Request, lead_id: str, body: LeadUpdate):
    await auth_admin(request)
    db = get_db()
    updates: dict = {}
    if body.status is not None:
        if body.status not in LEAD_STATUSES:
            raise HTTPException(400, f"status inválido. Usa: {', '.join(LEAD_STATUSES)}")
        updates["status"] = body.status
    if body.notes is not None:
        updates["notes"] = body.notes
    if not updates:
        raise HTTPException(400, "Nada que actualizar")
    updates["updated_at"] = datetime.now(timezone.utc)
    res = await db.deal_finder_leads.update_one({"_id": ObjectId(lead_id)}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(404, "Lead no encontrado")
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    return {"success": True, "lead": _lead_out(doc)}


@router.delete("/admin/deal-finder/leads/{lead_id}")
async def delete_lead(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    res = await db.deal_finder_leads.delete_one({"_id": ObjectId(lead_id)})
    if res.deleted_count == 0:
        raise HTTPException(404, "Lead no encontrado")
    return {"success": True}


@router.get("/admin/deal-finder/cron-config")
async def get_cron_config(request: Request):
    await auth_admin(request)
    db = get_db()
    cfg = await db.app_settings.find_one({"_id": "deal_finder_cron"}) or {}
    state = await db.app_settings.find_one({"_id": "deal_finder_cron_state"}) or {}
    from rental.deal_finder_cron import LETTERS, DEFAULT_MAX_PER_RUN, DEFAULT_ALERT_EMAIL
    letter_idx = int(state.get("letter_idx") or 0) % len(LETTERS)
    return {"success": True, "config": {
        "enabled": cfg.get("enabled", True),
        "max_per_run": int(cfg.get("max_per_run") or DEFAULT_MAX_PER_RUN),
        "alert_email": cfg.get("alert_email") or DEFAULT_ALERT_EMAIL,
    }, "state": {
        "next_letter": LETTERS[letter_idx].upper(),
        "coverage_pct": round(letter_idx / len(LETTERS) * 100),
        "cycles": int(state.get("cycles") or 0),
        "last_run": state["last_run"].isoformat() if state.get("last_run") else "",
        "last_result": state.get("last_result") or {},
        "running": bool(state.get("manual_running")),
    }}


class CronConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_per_run: Optional[int] = None
    alert_email: Optional[str] = None


@router.patch("/admin/deal-finder/cron-config")
async def update_cron_config(request: Request, body: CronConfigUpdate):
    await auth_admin(request)
    db = get_db()
    updates: dict = {}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.max_per_run is not None:
        updates["max_per_run"] = max(20, min(body.max_per_run, 500))
    if body.alert_email is not None:
        updates["alert_email"] = body.alert_email.strip()
    if not updates:
        raise HTTPException(400, "Nada que actualizar")
    await db.app_settings.update_one({"_id": "deal_finder_cron"}, {"$set": updates}, upsert=True)
    return {"success": True, "updated": updates}


@router.post("/admin/deal-finder/cron-run-now")
async def run_cron_now(request: Request):
    """Ejecuta un lote del recorrido automático ahora mismo (background)."""
    await auth_admin(request)
    db = get_db()
    state = await db.app_settings.find_one({"_id": "deal_finder_cron_state"}) or {}
    if state.get("manual_running"):
        raise HTTPException(409, "El radar automático ya está corriendo — espera a que termine")
    running_scan = await db.deal_finder_scans.find_one({"status": {"$in": ["searching", "enriching"]}})
    if running_scan:
        raise HTTPException(409, "Hay un escaneo manual en curso — espera a que termine")

    await db.app_settings.update_one(
        {"_id": "deal_finder_cron_state"}, {"$set": {"manual_running": True}}, upsert=True)

    async def _run():
        try:
            from rental.deal_finder_cron import run_auto_scan_batch
            await run_auto_scan_batch(db)
        except Exception as e:
            logger.error(f"[deal_finder] corrida manual del cron falló: {e}")
        finally:
            await db.app_settings.update_one(
                {"_id": "deal_finder_cron_state"}, {"$set": {"manual_running": False}})

    asyncio.create_task(_run())
    return {"success": True, "message": "Lote del radar automático iniciado"}


@router.post("/admin/deal-finder/leads/{lead_id}/analyze")
async def analyze_lead(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    ai = await _ai_json(ANALYZE_PROMPT, _lead_payload(doc))
    score = max(0, min(100, int(ai.get("score") or 0)))
    analysis = {
        "veredicto": ai.get("veredicto", ""),
        "razones": ai.get("razones", []),
        "estrategia": ai.get("estrategia", ""),
        "oferta_sugerida_pct": ai.get("oferta_sugerida_pct"),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.deal_finder_leads.update_one(
        {"_id": doc["_id"]}, {"$set": {"ai_score": score, "ai_analysis": analysis}})
    return {"success": True, "ai_score": score, "ai_analysis": analysis}


@router.post("/admin/deal-finder/leads/{lead_id}/letter")
async def generate_letter(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    ai = await _ai_json(LETTER_PROMPT, _lead_payload(doc))
    letter = {
        "letter_en": ai.get("letter_en", ""),
        "letter_es": ai.get("letter_es", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {"offer_letter": letter}})
    return {"success": True, "offer_letter": letter}


# ═══════════════════════════════════════════════════════════════
# Carta lista para imprimir (PDF) + envío físico vía Lob
# ═══════════════════════════════════════════════════════════════

async def _sender_info() -> dict:
    """Remitente = payer del 1099 (Ross House Rentals LLC)."""
    from rental.tax_1099_router import _get_payer
    return await _get_payer()


def _build_letter_pdf(lead: dict, sender: dict, lang: str) -> bytes:
    """Genera una carta de negocios en PDF (US Letter) lista para imprimir y
    doblar en un sobre #10 de ventana (la dirección del destinatario queda en la
    zona de la ventana ~2\" desde arriba)."""
    import io
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Frame,
                                    PageTemplate, FrameBreak)
    from reportlab.pdfgen import canvas as _canvas

    body_text = (lead.get("offer_letter") or {}).get(
        "letter_en" if lang == "en" else "letter_es", "")
    body_text = body_text.replace("[TELÉFONO]", sender.get("phone", "")) \
                         .replace("[PHONE]", sender.get("phone", "")) \
                         .replace("[EMAIL]", sender.get("email", "yoandyross@gmail.com"))

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("body", parent=styles["Normal"],
                                fontName="Helvetica", fontSize=11, leading=16)
    small = ParagraphStyle("small", parent=styles["Normal"],
                           fontName="Helvetica", fontSize=9, leading=12,
                           textColor="#555555")

    buf = io.BytesIO()

    def _draw_static(c: _canvas.Canvas, doc):
        c.saveState()
        # Remitente arriba-izquierda (return address)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(0.75 * inch, 10.35 * inch, sender.get("name", ""))
        c.setFont("Helvetica", 9)
        c.drawString(0.75 * inch, 10.20 * inch, sender.get("address", ""))
        c.drawString(0.75 * inch, 10.05 * inch,
                     f"{sender.get('city','')}, {sender.get('state','')} {sender.get('zip','')}")
        if sender.get("phone"):
            c.drawString(0.75 * inch, 9.90 * inch, sender["phone"])
        # Ventana destinatario (~2\" desde arriba)
        addr_top = 8.55 * inch
        c.setFont("Helvetica", 11)
        lines = [lead.get("owner_name", "").strip()]
        lines += [ln for ln in (lead.get("mailing_lines") or []) if ln]
        for i, ln in enumerate(lines[:5]):
            c.drawString(0.9 * inch, addr_top - i * 0.18 * inch, ln)
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=LETTER,
                            topMargin=3.4 * inch, bottomMargin=0.9 * inch,
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch)
    story = [Paragraph(datetime.now().strftime("%B %d, %Y"), small), Spacer(1, 0.25 * inch)]
    for para in [p for p in body_text.split("\n") if p.strip()]:
        story.append(Paragraph(para.replace("&", "&amp;").replace("<", "&lt;"), body_style))
        story.append(Spacer(1, 0.12 * inch))

    doc.build(story, onFirstPage=_draw_static, onLaterPages=_draw_static)
    return buf.getvalue()


@router.get("/admin/deal-finder/leads/{lead_id}/letter.pdf")
async def download_letter_pdf(request: Request, lead_id: str, lang: str = "en"):
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    if not (doc.get("offer_letter") or {}).get("letter_en"):
        raise HTTPException(400, "Genera primero la carta de oferta con AI")
    lang = "es" if lang == "es" else "en"
    sender = await _sender_info()
    pdf = _build_letter_pdf(doc, sender, lang)
    fname = f"carta_{(doc.get('address') or doc['property_id']).split(',')[0].replace(' ', '_')}_{lang}.pdf"
    return StreamingResponse(
        iter([pdf]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ─── Lob: envío físico (imprime, ensobra y despacha por USPS) ──

def _lob_key() -> Optional[str]:
    return os.environ.get("LOB_API_KEY")


def _lob_addr(prefix: str, name: str, lines: list[str], city: str, state: str, zipc: str) -> dict:
    out = {f"{prefix}[name]": (name or "Current Resident")[:40]}
    line1 = lines[0] if lines else ""
    line2 = lines[1] if len(lines) > 1 else ""
    out[f"{prefix}[address_line1]"] = line1[:64]
    if line2:
        out[f"{prefix}[address_line2]"] = line2[:64]
    out[f"{prefix}[address_city]"] = city
    out[f"{prefix}[address_state]"] = state
    out[f"{prefix}[address_zip]"] = zipc
    out[f"{prefix}[address_country]"] = "US"
    return out


def _lead_mail_parts(lead: dict) -> tuple[list[str], str, str, str]:
    """Devuelve (líneas de calle, city, state, zip) del destinatario."""
    lines = [ln for ln in (lead.get("mailing_lines") or []) if ln]
    city = lead.get("mailing_city", "")
    state = lead.get("mailing_state", "")
    zipc = lead.get("mailing_zip", "")
    # quitar la última línea (city/state/zip) de las líneas de calle si aparece
    street = [ln for ln in lines if not re.search(r",\s*[A-Z]{2}\s*[\d-]*$", ln)]
    return street or lines, city, state, zipc


@router.get("/admin/deal-finder/lob-status")
async def lob_status(request: Request):
    await auth_admin(request)
    key = _lob_key()
    out = {"success": True, "configured": bool(key),
           "mode": "live" if (key or "").startswith("live_") else "test" if key else None,
           "api_ok": False, "recent_letters": []}
    if key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get("https://api.lob.com/v1/letters?limit=5", auth=(key, ""))
                out["api_ok"] = r.status_code == 200
                if r.status_code == 200:
                    out["recent_letters"] = [{
                        "id": l.get("id"),
                        "to": (l.get("to") or {}).get("name", ""),
                        "date_created": l.get("date_created", ""),
                        "expected_delivery_date": l.get("expected_delivery_date", ""),
                        "carrier": l.get("carrier", ""),
                    } for l in (r.json().get("data") or [])]
                else:
                    out["api_error"] = r.json().get("error", {}).get("message", f"HTTP {r.status_code}")
        except Exception as e:
            out["api_error"] = str(e)[:150]
    return out


@router.post("/admin/deal-finder/leads/{lead_id}/mail")
async def mail_letter(request: Request, lead_id: str):
    """Envía la carta físicamente vía Lob (verifica dirección, imprime, ensobra y despacha)."""
    await auth_admin(request)
    key = _lob_key()
    if not key:
        raise HTTPException(400, "Lob no está configurado — agrega LOB_API_KEY en el backend")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    if not (doc.get("offer_letter") or {}).get("letter_en"):
        raise HTTPException(400, "Genera primero la carta de oferta con AI")

    street, city, state, zipc = _lead_mail_parts(doc)
    if not (street and city and state and zipc):
        raise HTTPException(422, "La dirección postal del dueño está incompleta — no se puede enviar")

    sender = await _sender_info()

    async with httpx.AsyncClient(timeout=40) as client:
        # 1) Verificar dirección (CASS) — evita cartas devueltas
        ver = await client.post("https://api.lob.com/v1/us_verifications",
                                auth=(key, ""), data={
                                    "primary_line": street[0],
                                    "city": city, "state": state, "zip_code": zipc})
        if ver.status_code == 200:
            dpv = ver.json().get("deliverability", "")
            if dpv not in ("deliverable", "deliverable_unnecessary_unit",
                           "deliverable_incorrect_unit", "deliverable_missing_unit"):
                raise HTTPException(422, f"USPS marca la dirección como no entregable ({dpv})")

        # 2) HTML de la carta (US Letter, address_placement=top_first_page)
        pdf = _build_letter_pdf(doc, sender, "en")
        import base64
        # Lob acepta HTML o un PDF; enviamos HTML embebiendo el texto para control total
        body_text = (doc.get("offer_letter") or {}).get("letter_en", "") \
            .replace("[TELÉFONO]", sender.get("phone", "")) \
            .replace("[EMAIL]", sender.get("email", "yoandyross@gmail.com"))
        body_html = "".join(
            f"<p>{p.strip().replace('&','&amp;').replace('<','&lt;')}</p>"
            for p in body_text.split("\n") if p.strip())
        html = (f'<html><head><meta charset="utf-8"><style>'
                f'@page{{size:letter;margin:0}}body{{width:8.5in;min-height:11in;margin:0;'
                f'padding:3.4in .9in .9in;font-family:Helvetica,Arial,sans-serif;font-size:11pt;line-height:1.45}}'
                f'</style></head><body>{body_html}</body></html>')

        data = {
            "description": f"Carta oferta — {doc.get('address', doc['property_id'])}",
            "color": "false", "double_sided": "false",
            "address_placement": "top_first_page",
            "mail_type": "usps_first_class", "use_type": "marketing",
            "file": html,
        }
        data.update(_lob_addr("to", doc.get("owner_name", ""), street, city, state, zipc))
        data.update(_lob_addr("from", sender.get("name", ""),
                              [sender.get("address", "")], sender.get("city", ""),
                              sender.get("state", ""), sender.get("zip", "")))

        idem = f"lead-{lead_id}-{int(datetime.now().timestamp())}"
        r = await client.post("https://api.lob.com/v1/letters", auth=(key, ""),
                              data=data, headers={"Idempotency-Key": idem})
    if r.status_code not in (200, 201):
        logger.error(f"[deal_finder] Lob falló {r.status_code}: {r.text[:300]}")
        raise HTTPException(502, f"Lob rechazó el envío: {r.text[:200]}")

    letter = r.json()
    mail = {
        "lob_id": letter.get("id"),
        "status": "mailed" if (key.startswith("live_")) else "test",
        "expected_delivery": letter.get("expected_delivery_date", ""),
        "tracking_url": letter.get("url", ""),
        "mode": "live" if key.startswith("live_") else "test",
        "mailed_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.deal_finder_leads.update_one(
        {"_id": doc["_id"]},
        {"$set": {"mail": mail, "status": "offer_sent"}})
    return {"success": True, "mail": mail}
