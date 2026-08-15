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
from datetime import datetime, timezone, timedelta
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

    # Exemptions (e.g. "HS" homestead → owner-occupied residential)
    m = re.search(r"Exemptions:</th>\s*<td[^>]*>(.*?)</td>", h, re.I)
    if m:
        out["exemptions"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    # Neighborhood (e.g. "(CODAPT1) Apartment in Dumas" → multifamily hint)
    m = re.search(r"Neighborhood:</th>\s*<td[^>]*>(.*?)</td>", h, re.I)
    if m:
        nb = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        nm = re.match(r"\(([A-Z0-9]+)\)\s*(.*)", nb)
        if nm:
            out["neighborhood_code"] = nm.group(1)
            out["neighborhood_name"] = nm.group(2).strip()
        elif nb:
            out["neighborhood_name"] = nb

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
# Clasificación de propiedades (comercial / residencial / etc.)
# ═══════════════════════════════════════════════════════════════

ENTITY_OWNER_RX = re.compile(
    r"\b(LLC|L\.L\.C|INC|CORP(ORATION)?|LTD|COMPANY|CO\.|PARTNERS(HIP)?|"
    r"PROPERTIES|PROPERTY|INVESTMENTS?|ENTERPRISES?|HOLDINGS?|VENTURES?|GROUP|"
    r"CHURCH|IGLESIA|MINISTRIES|ASSEMBLY|CITY OF|COUNTY OF|ISD|SCHOOL|TRUST|TRUSTEE|"
    r"BANK|CREDIT UNION|ASSN|ASSOCIATION|FOUNDATION)\b", re.I)

MULTIFAM_RX = re.compile(
    r"\b(APT|APARTMENT|APARTMENTS|DUPLEX|TRIPLEX|FOURPLEX|4[- ]?PLEX|MULTI[- ]?FAMILY)\b", re.I)


def classify_property(lead: dict) -> list[str]:
    """Clasifica una oportunidad en una o más clases:
    comercial | residencial | multifamiliar | terreno | agricola
    (combinables: p.ej. terreno vacío de una LLC = terreno + comercial)."""
    classes: set = set()
    ptype = (lead.get("property_type") or "").upper()
    values = lead.get("values") or {}
    improvement = sum(v for k, v in values.items() if "improvement" in k.lower())
    land = sum(v for k, v in values.items() if "land" in k.lower())
    ag = (values.get("Ag Use Value") or 0) + (values.get("Agricultural Market Valuation") or 0)
    owner = (lead.get("owner_name") or "").upper()
    legal = (lead.get("legal_description") or "").upper()
    nbhd = f"{lead.get('neighborhood_code') or ''} {lead.get('neighborhood_name') or ''}".upper()
    exemptions = (lead.get("exemptions") or "").upper()
    homestead = bool(re.search(r"\bHS\b|HOMESTEAD", exemptions))

    if ptype == "P":
        # Propiedad personal de negocio (equipos/inventario) → actividad comercial
        classes.add("comercial")
    if ag > 0:
        classes.add("agricola")
    if ptype != "P" and land > 0 and improvement <= 0 and ag <= 0:
        classes.add("terreno")
    if MULTIFAM_RX.search(nbhd) or MULTIFAM_RX.search(legal):
        classes.add("multifamiliar")
    if homestead:
        # Residencia principal del dueño → definitivamente NO comercial
        classes.add("residencial")
        classes.discard("comercial")
    elif ptype != "P" and ENTITY_OWNER_RX.search(owner):
        classes.add("comercial")
    if ptype in ("R", "MH") and improvement > 0 and not classes.intersection(
            {"multifamiliar", "terreno", "agricola", "comercial"}):
        classes.add("residencial")
    if not classes:
        classes.add("residencial")
    return sorted(classes)



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
    lead["property_classes"] = classify_property(lead)
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
CRITICAL LENGTH LIMIT: each letter MUST be at most 150 words (4-5 short paragraphs) so it fits
on a single printed page together with the signature and QR call-to-action box.

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
        "clases": lead.get("property_classes"),
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
        "favorite": bool(doc.get("favorite", False)),
        "county": doc.get("county", ""),
        "county_name": COUNTIES.get(doc.get("county", ""), {}).get("name", doc.get("county", "")),
        "property_id": doc.get("property_id", ""),
        "geo_id": doc.get("geo_id", ""),
        "owner_name": doc.get("owner_name", ""),
        "address": doc.get("address", ""),
        "legal_description": doc.get("legal_description", ""),
        "property_type": doc.get("property_type", ""),
        "property_classes": doc.get("property_classes", []),
        "exemptions": doc.get("exemptions", ""),
        "neighborhood_code": doc.get("neighborhood_code", ""),
        "neighborhood_name": doc.get("neighborhood_name", ""),
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
        "offer": doc.get("offer"),
        "contact": doc.get("contact"),
        "mail": doc.get("mail"),
        "contract": doc.get("contract"),
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
    lead["property_classes"] = classify_property(lead)
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


@router.get("/admin/deal-finder/campaign-stats")
async def campaign_stats(request: Request):
    """Embudo de la campaña de cartas: enviadas → entregadas (est.) → QR
    escaneados → respuestas, desglosado por condado y por tipo de señal."""
    await auth_admin(request)
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    funnel = {"sent": 0, "delivered": 0, "scanned": 0, "responded": 0}
    by_county: dict = {}
    by_signal: dict = {}
    by_action: dict = {}

    async for doc in db.deal_finder_leads.find(
            {"mail.mailed_at": {"$exists": True}},
            {"county": 1, "signals": 1, "mail": 1, "offer": 1}):
        mail = doc.get("mail") or {}
        offer = doc.get("offer") or {}
        delivered = bool(mail.get("expected_delivery") and
                         mail["expected_delivery"] <= today)
        scanned = (offer.get("visits") or 0) > 0
        responded = bool(offer.get("response"))
        county = doc.get("county") or "otro"
        signals = doc.get("signals") or ["otro"]

        def bump(d, key):
            row = d.setdefault(key, {"sent": 0, "delivered": 0, "scanned": 0, "responded": 0})
            row["sent"] += 1
            row["delivered"] += 1 if delivered else 0
            row["scanned"] += 1 if scanned else 0
            row["responded"] += 1 if responded else 0

        funnel["sent"] += 1
        funnel["delivered"] += 1 if delivered else 0
        funnel["scanned"] += 1 if scanned else 0
        funnel["responded"] += 1 if responded else 0
        bump(by_county, county)
        for s in signals:
            bump(by_signal, s)
        if responded:
            action = (offer["response"] or {}).get("action", "otro")
            by_action[action] = by_action.get(action, 0) + 1

    def rates(row):
        s = row["sent"] or 1
        return {**row,
                "scan_rate": round(row["scanned"] * 100 / s, 1),
                "response_rate": round(row["responded"] * 100 / s, 1)}

    return {"success": True,
            "funnel": rates(funnel),
            "by_county": {k: rates(v) for k, v in
                          sorted(by_county.items(), key=lambda kv: -kv[1]["sent"])},
            "by_signal": {k: rates(v) for k, v in
                          sorted(by_signal.items(), key=lambda kv: -kv[1]["sent"])},
            "by_action": by_action,
            "note": "Entregadas = estimado por fecha de entrega esperada de Lob/USPS"}


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

    by_class = {}
    async for row in db.deal_finder_leads.aggregate([
            {"$unwind": "$property_classes"},
            {"$group": {"_id": "$property_classes", "n": {"$sum": 1}}}]):
        by_class[row["_id"]] = row["n"]

    # ── Direct mail este mes (presupuesto Lob) ──
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    letter_cost = float(os.environ.get("LOB_LETTER_COST", "0.99"))
    mail_month = {"live": 0, "test": 0}
    async for row in db.deal_finder_leads.aggregate([
            {"$match": {"mail.mailed_at": {"$gte": month_start}}},
            {"$group": {"_id": "$mail.mode", "n": {"$sum": 1}}}]):
        mail_month[row["_id"] or "test"] = row["n"]
    mail_total = await db.deal_finder_leads.count_documents(
        {"mail.mode": "live"})
    return {"success": True, "stats": {
        "total": total, "tax_delinquent": delinquent, "absentee_owner": absentee,
        "vacant_land": vacant, "high_score": high_score, "by_status": by_status,
        "by_class": by_class,
        "mail": {
            "month_live": mail_month["live"],
            "month_test": mail_month["test"],
            "month_cost": round(mail_month["live"] * letter_cost, 2),
            "letter_cost": letter_cost,
            "all_time_live": mail_total,
        },
    }}


@router.get("/admin/deal-finder/leads")
async def list_leads(request: Request, status: Optional[str] = None,
                     signal: Optional[str] = None, county: Optional[str] = None,
                     q: Optional[str] = None, sort: str = "score",
                     city: Optional[str] = None, min_tax: float = 0,
                     min_score: float = 0, min_value: float = 0,
                     classes: Optional[str] = None,
                     limit: int = 100, skip: int = 0, favorite: int = 0, mailed: int = 0):
    await auth_admin(request)
    db = get_db()
    filt: dict = {}
    if classes:
        cls = [c.strip() for c in classes.split(",") if c.strip()]
        if cls:
            filt["property_classes"] = {"$all": cls}
    if favorite:
        filt["favorite"] = True
    if mailed:
        filt["mail.lob_id"] = {"$exists": True, "$nin": [None, ""]}
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

    sort_spec = [("mail.mailed_at", -1)] if mailed else \
                [("ai_score", -1), ("tax_due_total", -1)] if sort == "score" else \
                [("tax_due_total", -1)] if sort == "tax_due" else \
                [("appraised_value", -1)] if sort == "value" else \
                [("last_synced_at", -1)]
    docs = await db.deal_finder_leads.find(filt).sort(sort_spec).skip(skip).to_list(min(limit, 200))
    total = await db.deal_finder_leads.count_documents(filt)
    return {"success": True, "leads": [_lead_out(d) for d in docs], "total": total}


class RadarAIPayload(BaseModel):
    question: str


RADAR_AI_SYSTEM = """Eres el AI Brain de Ross House Rentals LLC, especializado en inversión
inmobiliaria (Dumas, TX / Moore County). Analizas el pipeline del Radar de Oportunidades
(propiedades con impuestos atrasados y otras señales de venta motivada).

Tu trabajo:
- Recomendar las MEJORES oportunidades y explicar por qué (impuestos vencidos vs valor, equity,
  señales, historial de cartas enviadas).
- Predecir probabilidad de éxito (alta/media/baja + %) de cada recomendación y qué ofertar.
- Sugerir acciones concretas: a quién enviar carta hoy, a quién dar seguimiento, qué marcar en la
  lista de deseos.
- Responde en ESPAÑOL, conciso y accionable, con montos en USD. Usa listas. No inventes datos que
  no estén en el contexto."""


@router.post("/admin/deal-finder/ai-assistant")
async def deal_finder_ai_assistant(request: Request, body: RadarAIPayload):
    """AI Brain del Radar: analiza el pipeline y responde preguntas / predice / recomienda."""
    await auth_admin(request)
    if not body.question.strip():
        raise HTTPException(400, "Escribe tu pregunta")
    db = get_db()
    docs = await db.deal_finder_leads.find({}).sort(
        [("ai_score", -1), ("tax_due_total", -1)]).limit(45).to_list(45)

    lines = []
    for d in docs:
        mail = d.get("mail") or {}
        parts = [
            f"[{str(d['_id'])[-6:]}] {d.get('address', '?')}",
            f"score:{round(d.get('ai_score') or 0)}",
            f"status:{d.get('status', 'new')}",
            f"tax_due:${round(d.get('tax_due_total') or 0):,}",
            f"valor:${round(d.get('appraised_value') or 0):,}",
            f"dueño:{d.get('owner_name', '?')}",
        ]
        if d.get("years_delinquent"):
            parts.append(f"años_morosos:{d['years_delinquent']}")
        if d.get("favorite"):
            parts.append("⭐deseos")
        if mail.get("lob_id"):
            parts.append(f"carta_enviada:{(mail.get('mailed_at') or '')[:10]} entrega_est:{mail.get('expected_delivery', '?')} modo:{mail.get('mode', '?')}")
        if (d.get("offer_letter") or {}).get("letter_en"):
            parts.append("carta_generada")
        lines.append(" | ".join(parts))

    mailed_count = sum(1 for d in docs if (d.get("mail") or {}).get("lob_id"))
    context = (f"PIPELINE ACTUAL ({len(docs)} leads, {mailed_count} con carta enviada):\n"
               + "\n".join(lines))

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(503, "IA no configurada")
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import secrets as _secrets
        chat = LlmChat(api_key=api_key, session_id=f"radar-{_secrets.token_hex(6)}",
                       system_message=RADAR_AI_SYSTEM).with_model("anthropic", "claude-sonnet-4-5-20250929")
        answer = await chat.send_message(UserMessage(text=f"{context}\n\nPREGUNTA DEL ADMIN: {body.question}"))
        answer = answer if isinstance(answer, str) else str(answer)
    except Exception as e:
        logger.error(f"[deal_finder] AI assistant error: {e}")
        raise HTTPException(503, "La IA no está disponible, intenta de nuevo")

    await db.ai_brain_logs.insert_one({
        "action_type": "deal_finder_assistant",
        "details": {"question": body.question[:200], "answer_preview": answer[:200]},
        "created_at": datetime.now(timezone.utc),
    })
    return {"success": True, "answer": answer}


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
    favorite: Optional[bool] = None


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
    if body.favorite is not None:
        updates["favorite"] = body.favorite
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


# ═══════════════════════════════════════════════════════════════
# Oferta personalizada (PURL + QR): link único por lead donde el
# dueño acepta la oferta, contraoferta o pide llamada — sin login.
# ═══════════════════════════════════════════════════════════════

SITE_BASE = "https://www.rosshouserentals.com"

SUGGEST_PRICE_PROMPT = """Eres un analista de inversión inmobiliaria en el Panhandle de Texas.
Recibirás datos de una propiedad (valores del CAD, impuestos atrasados, señales).
Calcula una OFERTA DE COMPRA EN EFECTIVO agresiva pero realista para un inversionista:
- Parte del valor de mercado/tasado; descuenta reparaciones estimadas por condición
  (improvement bajo = más descuento), deuda fiscal (la absorbe el comprador) y margen
  de inversión (objetivo: pagar 55-70% del valor de mercado).
- Redondea a los $500 más cercanos. Mínimo $2,000.
Responde SOLO JSON: {"suggested_price": <número>, "reasoning_es": "<2-3 frases en español
explicando el cálculo>", "pct_of_value": <porcentaje del valor tasado>}"""


def _offer_slug(owner_name: str) -> str:
    import secrets as _sec
    base = re.sub(r"[^a-z0-9]+", "-", (owner_name or "propietario").lower()).strip("-")
    base = "-".join(base.split("-")[:2]) or "propietario"
    code = "".join(_sec.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(4))
    return f"{base}-{code}"


def _offer_qr_png(url: str) -> bytes:
    import qrcode
    from io import BytesIO
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    b = BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


@router.post("/admin/deal-finder/leads/{lead_id}/suggest-price")
async def suggest_price(request: Request, lead_id: str):
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    ai = await _ai_json(SUGGEST_PRICE_PROMPT, _lead_payload(doc))
    return {"success": True,
            "suggested_price": ai.get("suggested_price", 0),
            "reasoning": ai.get("reasoning_es", ""),
            "pct_of_value": ai.get("pct_of_value", 0)}


class OfferBody(BaseModel):
    mode: str = "amount"          # "amount" (con oferta) | "ask" (pedir su precio)
    amount: float = 0


@router.post("/admin/deal-finder/leads/{lead_id}/offer")
async def create_offer(request: Request, lead_id: str, body: OfferBody):
    """Crea (o regenera) el link único + QR de oferta para este lead."""
    await auth_admin(request)
    if body.mode not in ("amount", "ask"):
        raise HTTPException(422, "mode debe ser amount o ask")
    if body.mode == "amount" and body.amount <= 0:
        raise HTTPException(422, "Indica el monto de la oferta")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    existing = (doc.get("offer") or {})
    slug = existing.get("slug")
    if not slug:
        for _ in range(5):
            slug = _offer_slug(doc.get("owner_name", ""))
            if not await db.deal_finder_leads.find_one({"offer.slug": slug}):
                break
    offer = {
        "slug": slug,
        "mode": body.mode,
        "amount": round(body.amount, 2) if body.mode == "amount" else 0,
        "created_at": existing.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "visits": existing.get("visits", 0),
        "last_visit_at": existing.get("last_visit_at"),
        "response": existing.get("response"),
    }
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {"offer": offer}})
    return {"success": True, "offer": offer, "url": f"{SITE_BASE}/oferta/{slug}"}


# ═══════════════════════════════════════════════════════════════
# Skip Tracing (Tracerfy) — teléfono/email del dueño
# ═══════════════════════════════════════════════════════════════

def _parse_situs(address: str) -> dict | None:
    """'1115 S BIRGE, DUMAS TX 79029' → street/city/state/zip."""
    m = re.match(r"^(.*?),\s*(.+?)\s+([A-Z]{2})\s+(\d{5})", (address or "").strip(), re.I)
    if not m:
        return None
    return {"street": m.group(1).strip(), "city": m.group(2).strip(),
            "state": m.group(3).upper(), "zip": m.group(4)}


def _owner_first_last(owner_name: str) -> tuple[str, str]:
    """CAD guarda 'APELLIDO NOMBRE SEGUNDO' → (first, last)."""
    toks = [t for t in re.split(r"[\s,]+", (owner_name or "").strip()) if t]
    if len(toks) >= 2:
        return toks[1].title(), toks[0].title()
    return (toks[0].title(), "") if toks else ("", "")


@router.post("/admin/deal-finder/leads/{lead_id}/skip-trace")
async def skip_trace_lead(request: Request, lead_id: str):
    """Busca teléfono y email del dueño vía Tracerfy (5 créditos por acierto, 0 si no hay match)."""
    await auth_admin(request)
    api_key = os.environ.get("TRACERFY_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "Configura TRACERFY_API_KEY en Configuración → API Keys (crea cuenta en tracerfy.com)")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")

    owner = doc.get("owner_name") or ""
    if ENTITY_OWNER_RX.search(owner):
        raise HTTPException(422,
            "El dueño es una empresa/entidad — el skip tracing es para personas. "
            "Busca el agente registrado gratis en el Comptroller de Texas "
            "(comptroller.texas.gov/taxes/franchise/account-status/search) o en SOSDirect.")

    situs = _parse_situs(doc.get("address") or "")
    if not situs:
        raise HTTPException(422, "El lead no tiene dirección física completa para rastrear")
    first, last = _owner_first_last(owner)
    if not first or not last:
        raise HTTPException(422, "No se pudo separar nombre/apellido del dueño")

    payload = {"address": situs["street"], "city": situs["city"], "state": situs["state"],
               "zip": situs["zip"], "find_owner": False, "first_name": first, "last_name": last}
    base = os.environ.get("TRACERFY_BASE_URL", "https://tracerfy.com/v1/api").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(f"{base}/trace/lookup/", json=payload,
                                  headers={"Authorization": f"Bearer {api_key}",
                                           "Content-Type": "application/json"})
    except httpx.HTTPError:
        raise HTTPException(502, "Tracerfy no disponible — intenta de nuevo")
    if r.status_code == 401:
        raise HTTPException(502, "API key de Tracerfy inválida")
    if r.status_code == 402:
        raise HTTPException(402, "Sin créditos en Tracerfy — recarga en tracerfy.com")
    if r.status_code >= 400:
        raise HTTPException(502, f"Tracerfy respondió {r.status_code}")

    data = r.json()
    persons = data.get("persons") or []
    phones, emails = [], []
    for p in persons[:3]:
        for ph in (p.get("phones") or [])[:5]:
            num = ph.get("number") or ph.get("phone") or ""
            if num and not any(x["number"] == num for x in phones):
                phones.append({"number": num, "type": ph.get("type", ""),
                               "dnc": bool(ph.get("dnc")), "tcpa": bool(ph.get("tcpa"))})
        for em in (p.get("emails") or [])[:5]:
            e = em.get("email") if isinstance(em, dict) else str(em)
            if e and e not in emails:
                emails.append(e)
    contact = {
        "phones": phones[:5], "emails": emails[:5],
        "hit": bool(data.get("hit") or phones or emails),
        "credits_used": data.get("credits_deducted", 0),
        "traced_at": datetime.now(timezone.utc).isoformat(),
        "source": "tracerfy",
    }
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {"contact": contact}})
    return {"success": True, "contact": contact}


# ═══════════════════════════════════════════════════════════════
# Enviar link de oferta por Email / SMS
# ═══════════════════════════════════════════════════════════════

class OfferSendBody(BaseModel):
    channel: str          # "email" | "sms"
    to: str
    pin: str = ""         # PIN para desbloquear envío a números DNC


async def _get_dnc_pin(db) -> str:
    cfg = await db.rental_config.find_one({"type": "company"}) or {}
    return str(cfg.get("dnc_pin") or "2018")


@router.post("/admin/deal-finder/leads/{lead_id}/offer/send")
async def send_offer_link(request: Request, lead_id: str, body: OfferSendBody):
    await auth_admin(request)
    if body.channel not in ("email", "sms"):
        raise HTTPException(422, "channel debe ser email o sms")
    to = body.to.strip()
    if not to:
        raise HTTPException(422, "Indica el destinatario")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    offer = doc.get("offer") or {}
    if not offer.get("slug"):
        raise HTTPException(400, "Primero genera el link de la oferta")

    # 🛡️ Bloqueo automático DNC (TCPA): SMS a números en No-Llamar requiere PIN
    if body.channel == "sms":
        to_digits = re.sub(r"\D", "", to)[-10:]
        for ph in ((doc.get("contact") or {}).get("phones") or []):
            if re.sub(r"\D", "", ph.get("number", ""))[-10:] == to_digits and (ph.get("dnc") or ph.get("tcpa")):
                pin_ok = body.pin and body.pin == await _get_dnc_pin(db)
                if not pin_ok:
                    raise HTTPException(423,
                        "DNC_PIN_REQUIRED: Este número está en la lista No Llamar (DNC). "
                        "Enviar SMS puede generar multas TCPA de $500-$1,500. "
                        "Ingresa tu PIN para desbloquear bajo tu responsabilidad.")
                break

    url = f"{SITE_BASE}/oferta/{offer['slug']}"
    first, _ = _owner_first_last(doc.get("owner_name") or "")
    addr = doc.get("address") or "su propiedad"
    amount = offer.get("amount") or 0
    amount_txt_es = f"${amount:,.0f} en efectivo" if offer.get("mode") == "amount" and amount else "una oferta en efectivo"
    amount_txt_en = f"${amount:,.0f} cash" if offer.get("mode") == "amount" and amount else "a cash offer"

    sent_ok = False
    if body.channel == "sms":
        sms_body = (f"Hola {first}, soy Yoandy de Ross House Rentals (Dumas, TX). "
                    f"Le ofrecemos {amount_txt_es} por {addr}. Vea los detalles y responda aqui: {url} "
                    f"/ Hi {first}, we're offering {amount_txt_en} for your property. Details: {url}")
        from rental.service_providers_router import _send_sms
        sent_ok = await _send_sms(to, sms_body)
        if not sent_ok:
            raise HTTPException(502, "No se pudo enviar el SMS — verifica las llaves de Twilio en Configuración → API Keys")
    else:
        cfg = await db.rental_config.find_one({"type": "company"}) or {}
        phone = cfg.get("phone") or "(806) 934-2018"
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;background:#f8f9fa;padding:24px;border-radius:12px">
          <h2 style="color:#8B1E3F;margin:0 0 12px">Ross House Rentals LLC</h2>
          <p>Hola <b>{first or 'propietario'}</b>,</p>
          <p>Le hacemos <b>{amount_txt_es}</b> por su propiedad en <b>{addr}</b>.
             Sin comisiones, sin reparaciones, cierre rápido y nosotros pagamos los costos de cierre.</p>
          <p style="text-align:center;margin:24px 0">
            <a href="{url}" style="background:#8B1E3F;color:#fff;padding:14px 28px;border-radius:8px;
               text-decoration:none;font-weight:bold">Ver mi oferta / View my offer</a>
          </p>
          <hr style="border:none;border-top:1px solid #ddd;margin:20px 0">
          <p style="color:#555">Hi <b>{first or 'owner'}</b>, we're making <b>{amount_txt_en}</b> for your property at
             <b>{addr}</b>. No fees, no repairs, fast closing. Tap the button above to view and respond.</p>
          <p style="font-size:12px;color:#888">Ross House Rentals LLC · {phone} · {url}</p>
        </div>"""
        plain = f"Ross House Rentals: le ofrecemos {amount_txt_es} por {addr}. Vea y responda: {url}"
        from rental.ai_brain_router import _send_email_branded
        sent_ok = await _send_email_branded(to, f"💰 Oferta en efectivo por {addr} — Ross House Rentals", html, plain)
        if not sent_ok:
            raise HTTPException(502, "No se pudo enviar el email — verifica SendGrid en Configuración → API Keys")

    entry = {"channel": body.channel, "to": to, "at": datetime.now(timezone.utc).isoformat()}
    if body.channel == "sms" and isinstance(sent_ok, str):
        entry["sid"] = sent_ok
        entry["status"] = "accepted"
    upd = {"$push": {"offer.sent_history": entry}}
    if doc.get("status") in (None, "", "new", "contacted"):
        upd["$set"] = {"status": "offer_sent"}
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, upd)
    return {"success": True, "sent": entry}


_SMS_ERROR_HINTS = {
    30007: "Bloqueado por el filtro anti-spam del carrier. Los SMS de marketing (oferta + link) "
           "requieren una campaña A2P 10DLC de tipo Marketing registrada en Twilio "
           "(Console → Messaging → Regulatory Compliance).",
    30034: "Número no registrado en A2P 10DLC — los carriers de EE.UU. bloquean SMS de números sin campaña registrada.",
    30003: "El teléfono del destinatario está apagado o fuera de servicio.",
    30005: "Número desconocido o inactivo.",
    30006: "El número destino es fijo (landline) y no puede recibir SMS.",
}


@router.post("/admin/deal-finder/leads/{lead_id}/sms-status")
async def refresh_sms_delivery_status(request: Request, lead_id: str):
    """Consulta en Twilio el estado REAL de entrega de los SMS del historial de la oferta
    y lo persiste (delivered / undelivered / failed + código y explicación)."""
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    hist = ((doc.get("offer") or {}).get("sent_history") or [])
    sms_entries = [h for h in hist if h.get("channel") == "sms"]
    if not sms_entries:
        return {"success": True, "sent_history": hist}
    acct_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (acct_sid and token):
        raise HTTPException(400, "Twilio no configurado — revisa Configuración → API Keys")

    def _lookup():
        from twilio.rest import Client
        client = Client(acct_sid, token)
        found = {}
        for h in sms_entries:
            if h.get("status") in ("delivered", "undelivered", "failed"):
                continue  # estado final ya conocido
            try:
                if h.get("sid"):
                    m = client.messages(h["sid"]).fetch()
                else:
                    # Entradas antiguas sin SID: buscar por número el mensaje más cercano en tiempo
                    digits = re.sub(r"\D", "", h.get("to", ""))[-10:]
                    if not digits:
                        continue
                    cands = client.messages.list(to=f"+1{digits}", limit=5)
                    sent_at = datetime.fromisoformat(h["at"])
                    m = min(cands, key=lambda c: abs((c.date_created - sent_at).total_seconds())
                            if c.date_created else 1e9, default=None)
                    if m is None or not m.date_created or abs((m.date_created - sent_at).total_seconds()) > 1200:
                        continue
                found[id(h)] = {"sid": m.sid, "status": m.status,
                                "error_code": m.error_code, "error_message": m.error_message}
            except Exception:
                logger.warning(f"[deal-finder] sms-status lookup failed for {h.get('to')}", exc_info=True)
        return found

    import anyio
    found = await anyio.to_thread.run_sync(_lookup)
    changed = False
    for h in sms_entries:
        st = found.get(id(h))
        if st:
            h.update({k: v for k, v in st.items() if v is not None or k in ("error_code", "error_message")})
            if st.get("error_code") in _SMS_ERROR_HINTS:
                h["error_hint"] = _SMS_ERROR_HINTS[st["error_code"]]
            changed = True
    if changed:
        await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {"offer.sent_history": hist}})
    return {"success": True, "sent_history": hist}


@router.get("/admin/deal-finder/sms-log")
async def sms_delivery_log(request: Request, limit: int = 20):
    """Diagnóstico: estado REAL de entrega de los últimos SMS en Twilio
    (queued/sent/delivered/undelivered/failed + código de error del carrier)."""
    await auth_admin(request)
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and token):
        raise HTTPException(400, "Twilio no configurado — revisa Configuración → API Keys")

    def _fetch():
        from twilio.rest import Client
        client = Client(sid, token)
        acct = client.api.accounts(sid).fetch()
        try:
            bal = client.api.accounts(sid).balance.fetch()
            balance = f"{bal.balance} {bal.currency}"
        except Exception:
            balance = None
        msgs = []
        for m in client.messages.list(limit=min(limit, 50)):
            msgs.append({
                "to": m.to, "from": m.from_, "status": m.status,
                "error_code": m.error_code, "error_message": m.error_message,
                "date": m.date_created.isoformat() if m.date_created else None,
                "body": (m.body or "")[:90],
            })
        return {"account_type": acct.type, "account_status": acct.status,
                "balance": balance, "messages": msgs}

    import anyio
    try:
        data = await anyio.to_thread.run_sync(_fetch)
    except Exception as e:
        raise HTTPException(502, f"Twilio error: {e}")
    return data


# ═══════════════════════════════════════════════════════════════
# Búsqueda manual de contacto (independiente de leads)
# ═══════════════════════════════════════════════════════════════

def _normalize_tracerfy_persons(data: dict) -> list[dict]:
    out = []
    for p in (data.get("persons") or [])[:5]:
        phones, emails = [], []
        for ph in (p.get("phones") or [])[:8]:
            num = ph.get("number") or ph.get("phone") or ""
            if num and not any(x["number"] == num for x in phones):
                phones.append({"number": num, "type": ph.get("type", ""), "carrier": ph.get("carrier", ""),
                               "dnc": bool(ph.get("dnc")), "tcpa": bool(ph.get("tcpa"))})
        for em in (p.get("emails") or [])[:5]:
            e = em.get("email") if isinstance(em, dict) else str(em)
            if e and e not in emails:
                emails.append(e)
        ma = p.get("mailing_address") or {}
        out.append({
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip().title(),
            "age": p.get("age"),
            "deceased": bool(p.get("deceased")),
            "property_owner": bool(p.get("property_owner")),
            "phones": phones, "emails": emails,
            "mailing_address": ", ".join(filter(None, [ma.get("street"), ma.get("city"),
                                                        f"{ma.get('state', '')} {ma.get('zip', '')}".strip()])),
        })
    return out


class ContactSearchBody(BaseModel):
    mode: str             # "name_address" | "address_owner" | "phone_dnc"
    first_name: str = ""
    last_name: str = ""
    street: str = ""
    city: str = ""
    state: str = "TX"
    zip: str = ""
    phone: str = ""


@router.post("/admin/deal-finder/contact-search")
async def contact_search(request: Request, body: ContactSearchBody):
    """Búsqueda manual de contacto vía Tracerfy:
    - name_address: nombre + apellido + dirección → teléfonos/emails (5 créditos/hit)
    - address_owner: solo dirección → descubre al dueño + contacto (5 créditos/hit)
    - phone_dnc: teléfono → verifica DNC federal/estatal/litigante (5 créditos)"""
    await auth_admin(request)
    api_key = os.environ.get("TRACERFY_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "Configura TRACERFY_API_KEY en Configuración → API Keys")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    if body.mode == "phone_dnc":
        phone = re.sub(r"\D", "", body.phone)[-10:]
        if len(phone) != 10:
            raise HTTPException(422, "Indica un teléfono válido de 10 dígitos")
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.post("https://tracerfy.com/v2/api/dnc/lookup/",
                                      json={"phone": phone}, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(502, "Tracerfy no disponible")
        if r.status_code == 402:
            raise HTTPException(402, "Sin créditos en Tracerfy")
        if r.status_code >= 400:
            raise HTTPException(502, f"Tracerfy respondió {r.status_code}")
        d = r.json()
        return {"success": True, "mode": "phone_dnc", "phone": phone,
                "is_clean": bool(d.get("is_clean")),
                "national_dnc": bool(d.get("national_dnc")),
                "state_dnc": bool(d.get("state_dnc")),
                "state_dnc_list": d.get("state_dnc_list") or [],
                "litigator": bool(d.get("litigator")),
                "credits_used": d.get("credits_deducted", 0)}

    if body.mode not in ("name_address", "address_owner"):
        raise HTTPException(422, "mode inválido")
    street, city, state, zipc = body.street.strip(), body.city.strip(), body.state.strip().upper(), body.zip.strip()
    if not street and body.mode:
        raise HTTPException(422, "Indica la dirección (calle)")
    if not city or not zipc:
        # intentar parsear todo desde street si vino completo
        situs = _parse_situs(street)
        if situs:
            street, city, state, zipc = situs["street"], situs["city"], situs["state"], situs["zip"]
    if not (street and city and state and zipc):
        raise HTTPException(422, "Dirección incompleta — necesito calle, ciudad, estado y ZIP")

    payload: dict = {"address": street, "city": city, "state": state, "zip": zipc}
    if body.mode == "name_address":
        if not body.first_name.strip() or not body.last_name.strip():
            raise HTTPException(422, "Indica nombre y apellido")
        payload.update({"find_owner": False, "first_name": body.first_name.strip(),
                        "last_name": body.last_name.strip()})
    else:
        payload["find_owner"] = True

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://tracerfy.com/v1/api/trace/lookup/",
                                  json=payload, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(502, "Tracerfy no disponible")
    if r.status_code == 402:
        raise HTTPException(402, "Sin créditos en Tracerfy — recarga en tracerfy.com")
    if r.status_code >= 400:
        raise HTTPException(502, f"Tracerfy respondió {r.status_code}")
    data = r.json()
    return {"success": True, "mode": body.mode, "hit": bool(data.get("hit")),
            "persons": _normalize_tracerfy_persons(data),
            "credits_used": data.get("credits_deducted", 0)}


@router.get("/public/oferta/{slug}")
async def public_offer(slug: str, request: Request):
    """Página pública del dueño — registra la visita (¡sabemos que recibió la carta!)."""
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"offer.slug": slug})
    if not doc:
        raise HTTPException(404, "Oferta no encontrada")
    offer = doc.get("offer") or {}
    upd = {"$inc": {"offer.visits": 1},
           "$set": {"offer.last_visit_at": datetime.now(timezone.utc).isoformat()}}
    if not offer.get("first_visit_at"):
        upd["$set"]["offer.first_visit_at"] = datetime.now(timezone.utc).isoformat()
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, upd)
    expires = offer.get("expires_at", "")
    expired = False
    try:
        expired = datetime.fromisoformat(expires) < datetime.now(timezone.utc)
    except Exception:
        pass
    first_name = (doc.get("owner_name") or "").split()[0].title() if doc.get("owner_name") else ""
    return {
        "success": True,
        "owner_first": first_name,
        "owner_name": (doc.get("owner_name") or "").title(),
        "address": doc.get("address", ""),
        "county": COUNTIES.get(doc.get("county", ""), {}).get("name", ""),
        "mode": offer.get("mode", "ask"),
        "amount": offer.get("amount", 0),
        "expires_at": expires,
        "expired": expired,
        "responded": bool(offer.get("response")),
        "response_action": (offer.get("response") or {}).get("action", ""),
    }


class OfferResponseBody(BaseModel):
    action: str                    # accept | counter | call | reject
    price: float = 0
    phone: str = ""
    best_time: str = ""
    message: str = ""


@router.post("/public/oferta/{slug}/responder")
async def public_offer_respond(slug: str, body: OfferResponseBody, request: Request):
    if body.action not in ("accept", "counter", "call", "reject"):
        raise HTTPException(422, "Acción inválida")
    if body.action == "counter" and body.price <= 0:
        raise HTTPException(422, "Indica tu precio")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"offer.slug": slug})
    if not doc:
        raise HTTPException(404, "Oferta no encontrada")
    response = {
        "action": body.action,
        "price": round(body.price, 2) if body.price else 0,
        "phone": body.phone.strip()[:25],
        "best_time": body.best_time.strip()[:60],
        "message": body.message.strip()[:500],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    new_status = {"accept": "negotiating", "counter": "interested",
                  "call": "contacted", "reject": "discarded"}[body.action]
    await db.deal_finder_leads.update_one(
        {"_id": doc["_id"]},
        {"$set": {"offer.response": response, "status": new_status}})

    # ✅ ACEPTÓ: generar contrato automáticamente (queda guardado en el lead)
    auto_contract_pdf = None
    contract_price = 0.0
    if body.action == "accept":
        offer = doc.get("offer") or {}
        contract_price = float(offer.get("amount") or 0) or float(body.price or 0)
        if contract_price > 0:
            try:
                auto_contract_pdf, _ = await _generate_contract_for_lead(
                    db, doc, price=contract_price)
                logger.info(f"[deal_finder] contrato auto-generado para {doc.get('address','')} (${contract_price:,.0f})")
            except Exception as e:
                logger.warning(f"[deal_finder] auto-contrato falló: {e}")

    # Notificar al admin por email en segundo plano (incluye análisis IA si es contraoferta)
    async def _notify_admin():
        try:
            from rental.newsletter_router import _sendgrid, _send_one
            sg_key, from_email = await _sendgrid()
            labels = {"accept": "✅ ACEPTÓ LA OFERTA", "counter": "💬 CONTRAOFERTA",
                      "call": "📞 PIDE LLAMADA", "reject": "❌ No le interesa"}
            offer = doc.get("offer") or {}
            contract_note = ""
            if auto_contract_pdf:
                contract_note = ("<p style='background:#ecfdf5;border-left:4px solid #10b981;"
                                 "padding:10px 14px;border-radius:6px;color:#065f46;'>"
                                 f"📄 <b>Contrato de compra generado automáticamente</b> por "
                                 f"${contract_price:,.0f} — va adjunto y quedó guardado en el lead. "
                                 f"Puedes descargarlo o enviárselo al vendedor desde el panel.</p>")
            # 🤖 Contraoferta → análisis de negociación con Claude
            analysis_html = ""
            if body.action == "counter" and (body.price or 0) > 0:
                analysis = await _analyze_counteroffer(doc, float(body.price))
                if analysis:
                    await db.deal_finder_leads.update_one(
                        {"_id": doc["_id"]}, {"$set": {"offer.response.ai_analysis": analysis}})
                    analysis_html = _analysis_email_html(analysis)
            html = (f"<h2>{labels[body.action]} — {doc.get('address','')}</h2>"
                    f"<p><b>Propiedad:</b> {doc.get('address','')}</p>"
                    f"<p><b>Dueño:</b> {doc.get('owner_name','')}</p>"
                    + (f"<p><b>Nuestra oferta:</b> ${offer.get('amount',0):,.0f}</p>" if offer.get('mode') == 'amount' else "")
                    + (f"<p><b>Su precio:</b> ${body.price:,.0f}</p>" if body.price else "")
                    + (f"<p><b>Teléfono:</b> {body.phone} · {body.best_time}</p>" if body.phone else "")
                    + (f"<p><b>Mensaje:</b> {body.message}</p>" if body.message else "")
                    + analysis_html
                    + contract_note
                    + f"<p><a href='{SITE_BASE}/admin/oportunidades'>Ver en el panel →</a></p>")
            subject = f"{labels[body.action]} — {doc.get('address','')[:60]}"
            if auto_contract_pdf:
                ok = await _email_with_pdf("yoandyross@gmail.com", subject, html,
                                           auto_contract_pdf, _contract_filename(doc))
                if not ok:
                    await _send_one(sg_key, from_email, "yoandyross@gmail.com", subject, html)
            else:
                await _send_one(sg_key, from_email, "yoandyross@gmail.com", subject, html)
        except Exception as e:
            logger.warning(f"[deal_finder] notificación de respuesta falló: {e}")

    asyncio.create_task(_notify_admin())
    return {"success": True}


# ═══════════════════════════════════════════════════════════════
# Contrato de compraventa (cash) — PDF pre-llenado
# ═══════════════════════════════════════════════════════════════

class ContractBody(BaseModel):
    price: float
    seller_name: str = ""
    earnest_money: float = 500
    closing_days: int = 30
    title_company_id: str = ""
    title_policy_paid_by: str = "Buyer"   # Buyer | Seller
    special_terms: str = ""


async def _generate_contract_for_lead(db, doc: dict, *, price: float,
                                      seller_name: str = "", earnest_money: float = 500,
                                      closing_days: int = 30, title_company_id: str = "",
                                      title_policy_paid_by: str = "Buyer",
                                      special_terms: str = "") -> tuple[bytes, dict]:
    """Genera el contrato PDF, lo guarda en el lead (meta + PDF base64) y lo devuelve."""
    import base64
    from rental.title_companies_router import _ensure_seed
    await _ensure_seed(db)
    title_co = None
    if title_company_id:
        title_co = await db.title_companies.find_one({"_id": title_company_id})
    if not title_co:
        title_co = (await db.title_companies.find_one({"is_default": True})
                    or await db.title_companies.find_one({}))
    if not title_co:
        raise HTTPException(400, "Agrega al menos una casa de título primero")

    buyer = await _sender_info()
    seller = (seller_name or doc.get("owner_name") or "").title()
    mailing = [ln for ln in (doc.get("mailing_lines") or []) if ln]

    from rental.purchase_contract import build_contract_pdf
    pdf = build_contract_pdf(
        buyer=buyer,
        seller_name=seller,
        seller_address=", ".join(mailing[:2]),
        property_address=doc.get("address", ""),
        legal_description=doc.get("legal_description", ""),
        county_name=COUNTIES.get(doc.get("county", ""), {}).get("name", "Moore County"),
        price=price,
        earnest_money=earnest_money,
        closing_days=closing_days,
        title_co=title_co,
        title_policy_paid_by=title_policy_paid_by if title_policy_paid_by in ("Buyer", "Seller") else "Buyer",
        special_terms=special_terms[:2000],
    )

    meta = {
        "price": round(price, 2),
        "seller_name": seller,
        "earnest_money": round(earnest_money, 2),
        "closing_days": closing_days,
        "title_company_id": title_co["_id"],
        "title_company_name": title_co.get("name", ""),
        "title_policy_paid_by": title_policy_paid_by,
        "special_terms": special_terms[:2000],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {
        "contract": meta,
        "contract_pdf_b64": base64.b64encode(pdf).decode(),
    }})
    return pdf, meta


def _contract_filename(doc: dict) -> str:
    return f"contrato_{(doc.get('address') or str(doc.get('property_id', ''))).split(',')[0].replace(' ', '_')}.pdf"


async def _email_with_pdf(to_email: str, subject: str, html: str,
                          pdf: bytes, filename: str) -> bool:
    """Envía un email con el contrato PDF adjunto vía SendGrid."""
    from rental.newsletter_router import _sendgrid
    sg_key, from_email = await _sendgrid()
    if not sg_key:
        return False

    def _sync() -> bool:
        import base64 as b64
        import sendgrid
        from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
                                           FileContent, FileName, FileType, Disposition)
        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        mail = Mail(from_email=Email(from_email, "Ross House Rentals"),
                    to_emails=To(to_email), subject=subject,
                    html_content=Content("text/html", html))
        att = Attachment(FileContent(b64.b64encode(pdf).decode()),
                         FileName(filename), FileType("application/pdf"),
                         Disposition("attachment"))
        mail.add_attachment(att)
        resp = sg.client.mail.send.post(request_body=mail.get())
        return resp.status_code in (200, 201, 202)

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logger.warning(f"[deal_finder] email con contrato a {to_email} falló: {e}")
        return False


async def _analyze_counteroffer(doc: dict, counter_price: float) -> dict | None:
    """Analiza una contraoferta con Claude y devuelve recomendación de negociación."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    offer = doc.get("offer") or {}
    facts = {
        "direccion": doc.get("address", ""),
        "condado": COUNTIES.get(doc.get("county", ""), {}).get("name", doc.get("county", "")),
        "valor_tasado_condado": doc.get("appraised_value", 0),
        "desglose_valores": doc.get("values", {}),
        "impuestos_atrasados": doc.get("tax_due_total", 0),
        "anios_impuestos_deuda": doc.get("tax_years_due", []),
        "seniales_distress": doc.get("signals", []),
        "nuestra_oferta": offer.get("amount", 0),
        "contraoferta_del_dueno": counter_price,
        "notas_lead": (doc.get("notes") or "")[:500],
    }
    try:
        from uuid import uuid4
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        system_prompt = (
            "Eres el asesor de adquisiciones de Ross House Rentals LLC, un inversionista que compra "
            "casas en efectivo (AS-IS) en el Texas Panhandle (Dumas/Moore County) para rentarlas. "
            "Estrategia: comprar con descuento fuerte (ideal ≤60-70% del valor tasado del condado, "
            "que suele estar por DEBAJO del valor de mercado), asumiendo reparaciones desconocidas. "
            "Los impuestos atrasados se pagan del dinero del VENDEDOR al cierre (no suben nuestro costo, "
            "pero reducen lo que el dueño recibe neto — úsalo como palanca de negociación). "
            "Analiza la contraoferta del dueño y responde SOLO JSON válido:\n"
            "{\n"
            '  "recommendation": "accept"|"counter"|"reject",\n'
            '  "suggested_counter": número o null (si recommendation=counter, el precio que debemos ofrecer),\n'
            '  "max_price": número (precio máximo walk-away para que el deal siga siendo bueno),\n'
            '  "deal_score": 1-10 (qué tan bueno es el deal AL PRECIO DE LA CONTRAOFERTA),\n'
            '  "reasoning": "3-4 frases en español explicando el análisis con números",\n'
            '  "leverage_points": ["palanca de negociación 1", …2-3 en español],\n'
            '  "email_script": "borrador corto y cordial EN INGLÉS (3-4 frases) para responder al dueño con la estrategia recomendada"\n'
            "}"
        )
        user_prompt = ("Analiza esta contraoferta y devuelve el JSON:\n```json\n"
                       + json.dumps(facts, ensure_ascii=False, default=str, indent=2) + "\n```")
        chat = LlmChat(api_key=api_key,
                       session_id=f"counter_analysis_{uuid4()}",
                       system_message=system_prompt).with_model("anthropic", "claude-sonnet-4-5-20250929")
        raw = await chat.send_message(UserMessage(text=user_prompt))
        text = str(raw or "").strip()
        if text.startswith("```"):
            parts = text.split("```", 2)
            text = parts[1] if len(parts) > 1 else parts[0]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip("` \n")
        try:
            parsed = json.loads(text)
        except Exception:
            first, last = text.find("{"), text.rfind("}")
            parsed = json.loads(text[first:last + 1]) if first >= 0 and last > first else None
        if not isinstance(parsed, dict) or "recommendation" not in parsed:
            return None
        parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
        parsed["analyzed_counter_price"] = counter_price
        return parsed
    except Exception as e:
        logger.warning(f"[deal_finder] análisis de contraoferta falló: {e}")
        return None


def _analysis_email_html(analysis: dict) -> str:
    """Bloque HTML del análisis de Claude para el email de notificación."""
    rec_map = {"accept": ("✅ ACEPTAR", "#10b981"), "counter": ("🔁 CONTRAOFERTAR", "#f59e0b"),
               "reject": ("❌ RECHAZAR / PASAR", "#ef4444")}
    label, color = rec_map.get(analysis.get("recommendation", ""), ("🤖 ANÁLISIS", "#6366f1"))
    sc = analysis.get("suggested_counter")
    rows = ""
    if sc:
        rows += f"<p style='margin:4px 0'><b>Contraoferta sugerida:</b> ${sc:,.0f}</p>"
    if analysis.get("max_price"):
        rows += f"<p style='margin:4px 0'><b>Precio máximo (walk-away):</b> ${analysis['max_price']:,.0f}</p>"
    if analysis.get("deal_score") is not None:
        rows += f"<p style='margin:4px 0'><b>Score del deal a su precio:</b> {analysis['deal_score']}/10</p>"
    points = "".join(f"<li>{p}</li>" for p in (analysis.get("leverage_points") or [])[:4])
    script = analysis.get("email_script") or ""
    return (f"<div style='border:2px solid {color};border-radius:10px;padding:14px 16px;margin:14px 0;background:#fafafa'>"
            f"<p style='margin:0 0 6px;font-size:16px;font-weight:bold;color:{color}'>🤖 Recomendación IA: {label}</p>"
            f"{rows}"
            f"<p style='margin:8px 0;color:#334155'>{analysis.get('reasoning', '')}</p>"
            + (f"<p style='margin:8px 0 2px'><b>Palancas de negociación:</b></p><ul style='margin:2px 0 8px;color:#334155'>{points}</ul>" if points else "")
            + (f"<p style='margin:8px 0 2px'><b>Borrador para responder (inglés):</b></p>"
               f"<p style='margin:2px 0;padding:8px 10px;background:#eef2ff;border-radius:6px;color:#1e293b;font-style:italic'>{script}</p>" if script else "")
            + "</div>")


@router.post("/admin/deal-finder/leads/{lead_id}/analyze-counter")
async def analyze_counter_endpoint(request: Request, lead_id: str):
    """Analiza (o re-analiza) la contraoferta de un lead bajo demanda."""
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    response = (doc.get("offer") or {}).get("response") or {}
    counter_price = float(response.get("price") or 0)
    if counter_price <= 0:
        raise HTTPException(422, "Este lead no tiene una contraoferta con precio")
    analysis = await _analyze_counteroffer(doc, counter_price)
    if not analysis:
        raise HTTPException(502, "El análisis IA no está disponible ahora — intenta de nuevo")
    await db.deal_finder_leads.update_one(
        {"_id": doc["_id"]}, {"$set": {"offer.response.ai_analysis": analysis}})
    return {"success": True, "analysis": analysis}


@router.post("/admin/deal-finder/leads/{lead_id}/contract.pdf")
async def generate_contract_pdf(request: Request, lead_id: str, body: ContractBody):
    """Genera el contrato de compra cash pre-llenado (PDF) y lo guarda en el lead."""
    await auth_admin(request)
    if body.price <= 0:
        raise HTTPException(422, "Indica el precio de compra")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    pdf, _ = await _generate_contract_for_lead(
        db, doc, price=body.price, seller_name=body.seller_name,
        earnest_money=body.earnest_money, closing_days=body.closing_days,
        title_company_id=body.title_company_id,
        title_policy_paid_by=body.title_policy_paid_by,
        special_terms=body.special_terms)
    return StreamingResponse(
        iter([pdf]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_contract_filename(doc)}"'})


@router.get("/admin/deal-finder/leads/{lead_id}/contract-download.pdf")
async def download_stored_contract(request: Request, lead_id: str):
    """Descarga el último contrato guardado en la base de datos."""
    import base64
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    b64 = doc.get("contract_pdf_b64")
    if not b64:
        raise HTTPException(404, "Este lead no tiene contrato guardado — genera uno primero")
    pdf = base64.b64decode(b64)
    return StreamingResponse(
        iter([pdf]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_contract_filename(doc)}"'})


class ContractEmailBody(BaseModel):
    to_email: str
    message: str = ""


@router.post("/admin/deal-finder/leads/{lead_id}/contract-email")
async def email_contract_to_seller(request: Request, lead_id: str, body: ContractEmailBody):
    """Envía el contrato guardado por email al vendedor (u otra dirección)."""
    import base64
    await auth_admin(request)
    if not re.match(r"[^@]+@[^@]+\.[^@]+", body.to_email or ""):
        raise HTTPException(422, "Email inválido")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    b64 = doc.get("contract_pdf_b64")
    if not b64:
        raise HTTPException(404, "Este lead no tiene contrato guardado — genera uno primero")
    contract = doc.get("contract") or {}
    seller = contract.get("seller_name") or (doc.get("owner_name") or "").title()
    extra = f"<p style='color:#475569;font-size:14px;line-height:1.6'>{body.message.strip()[:1000]}</p>" if body.message.strip() else ""
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:600px;margin:auto;">
      <h2 style="color:#0f172a;">Purchase Agreement — {doc.get('address', '')}</h2>
      <p style="color:#475569;font-size:14px;line-height:1.6;">
        Dear {seller},<br/><br/>
        Please find attached the cash purchase agreement for the property at
        <b>{doc.get('address', '')}</b> in the amount of <b>${contract.get('price', 0):,.2f}</b>.
        Closing will be handled by <b>{contract.get('title_company_name', '')}</b>.
      </p>
      {extra}
      <p style="color:#475569;font-size:14px;line-height:1.6;">
        If you have any questions, call us at (806) 934-2018 or reply to this email.<br/><br/>
        — Yoandy Ross · Ross House Rentals LLC
      </p>
    </div>"""
    ok = await _email_with_pdf(body.to_email.strip(), f"Purchase Agreement — {doc.get('address', '')}",
                               html, base64.b64decode(b64), _contract_filename(doc))
    if not ok:
        raise HTTPException(502, "No se pudo enviar el email (revisa SendGrid)")
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {
        "contract.emailed_to": body.to_email.strip(),
        "contract.emailed_at": datetime.now(timezone.utc).isoformat(),
    }})
    return {"success": True, "sent_to": body.to_email.strip()}


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

# ─── Foto aérea de la propiedad (Census geocoder + Esri World Imagery, gratis) ──

async def _geocode_address(address: str) -> Optional[tuple]:
    """Geocodifica una dirección con el US Census Geocoder (gratuito)."""
    if not address:
        return None
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": address, "benchmark": "Public_AR_Current",
                        "format": "json"})
            matches = ((r.json().get("result") or {}).get("addressMatches") or [])
            if matches:
                c = matches[0]["coordinates"]
                return float(c["y"]), float(c["x"])
    except Exception as e:
        logger.warning(f"[deal_finder] geocode falló '{address}': {e}")
    return None


async def _aerial_photo(lat: float, lon: float, out_w: int = 540, out_h: int = 330,
                        zoom: int = 19) -> Optional[bytes]:
    """Foto aérea centrada en la propiedad (mosaico de tiles Esri World Imagery)
    con un marcador rojo en el punto exacto. Devuelve JPEG o None."""
    import math
    from io import BytesIO
    from PIL import Image, ImageDraw
    try:
        n = 2 ** zoom
        px = (lon + 180) / 360 * n * 256
        py = (1 - math.log(math.tan(math.radians(lat)) +
                           1 / math.cos(math.radians(lat))) / math.pi) / 2 * n * 256
        x0, y0 = int(px - out_w / 2), int(py - out_h / 2)
        tx0, ty0 = x0 // 256, y0 // 256
        tx1, ty1 = (x0 + out_w) // 256, (y0 + out_h) // 256
        tiles = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
        async with httpx.AsyncClient(timeout=20) as client:
            results = await asyncio.gather(*[
                client.get("https://server.arcgisonline.com/ArcGIS/rest/services/"
                           f"World_Imagery/MapServer/tile/{zoom}/{ty}/{tx}")
                for tx, ty in tiles], return_exceptions=True)
        mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
        for (tx, ty), r in zip(tiles, results):
            if isinstance(r, Exception) or r.status_code != 200:
                return None
            mosaic.paste(Image.open(BytesIO(r.content)), ((tx - tx0) * 256, (ty - ty0) * 256))
        crop = mosaic.crop((x0 - tx0 * 256, y0 - ty0 * 256,
                            x0 - tx0 * 256 + out_w, y0 - ty0 * 256 + out_h))
        # Marcador: anillo + punto rojo en el centro
        d = ImageDraw.Draw(crop)
        cx, cy = out_w // 2, out_h // 2
        d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], outline="#C41428", width=5)
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill="#C41428")
        buf = BytesIO()
        crop.save(buf, "JPEG", quality=82)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"[deal_finder] foto aérea falló ({lat},{lon}): {e}")
        return None


async def _lead_photo(db, doc: dict) -> Optional[bytes]:
    """Foto aérea del lead (cacheada en el documento). None si no es posible."""
    import base64
    cached = doc.get("photo") or {}
    if cached.get("b64"):
        return base64.b64decode(cached["b64"])
    lat = lon = None
    try:
        if doc.get("latitude") and doc.get("longitude"):
            lat, lon = float(doc["latitude"]), float(doc["longitude"])
    except (TypeError, ValueError):
        pass
    if lat is None:
        coords = await _geocode_address(doc.get("address", ""))
        if not coords:
            return None
        lat, lon = coords
        await db.deal_finder_leads.update_one(
            {"_id": doc["_id"]}, {"$set": {"latitude": str(lat), "longitude": str(lon)}})
    img = await _aerial_photo(lat, lon)
    if img:
        await db.deal_finder_leads.update_one(
            {"_id": doc["_id"]},
            {"$set": {"photo": {"b64": base64.b64encode(img).decode(),
                                "source": "aerial",
                                "at": datetime.now(timezone.utc).isoformat()}}})
    return img


async def _sender_info() -> dict:
    """Remitente: Yoandy Ross (personal — mejor respuesta en direct mail);
    la LLC va como segunda línea/firma."""
    from rental.tax_1099_router import _get_payer
    payer = await _get_payer()
    payer["company"] = payer.get("name", "Ross House Rentals LLC")
    payer["name"] = "Yoandy Ross"
    return payer


def _letter_date(lang: str) -> str:
    now = datetime.now()
    if lang == "es":
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        return f"{now.day} de {meses[now.month - 1]} de {now.year}"
    return now.strftime("%B %d, %Y")


def _register_embedded_fonts():
    """Registra TTFs bajo los nombres estándar para que reportlab EMBEBA las
    fuentes (Lob exige fuentes embebidas en los PDFs)."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        fdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
        mapping = {
            "Helvetica": "LiberationSans-Regular.ttf",
            "Helvetica-Bold": "LiberationSans-Bold.ttf",
            "Helvetica-Oblique": "LiberationSans-Italic.ttf",
            "Times-Roman": "LiberationSerif-Regular.ttf",
            "Times-Italic": "LiberationSerif-Italic.ttf",
        }
        for name, fn in mapping.items():
            path = os.path.join(fdir, fn)
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(name, path))
    except Exception as e:
        logger.warning(f"[deal_finder] no se pudieron embeber fuentes: {e}")


def _build_letter_pdf(lead: dict, sender: dict, lang: str,
                      photo: Optional[bytes] = None,
                      for_lob: bool = False) -> bytes:
    """Carta premium en PDF (US Letter) lista para imprimir y doblar en sobre #10
    de ventana (destinatario en la zona de la ventana ~2\" desde arriba).
    Diseño: membrete con acento de marca, tipografía serif, caja CTA con QR,
    tarjeta de firma y pie de página elegante.
    for_lob=True: deja limpia la zona superior-izquierda de la página 1 para que
    Lob imprima ahí el remitente y destinatario (address_placement=top_first_page)."""
    _register_embedded_fonts()
    import io
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, Image as RLImage)
    from reportlab.pdfgen import canvas as _canvas

    RED = rl_colors.HexColor("#C41428")
    DARK = rl_colors.HexColor("#20242E")
    GRAY = rl_colors.HexColor("#6B7280")
    LIGHT = rl_colors.HexColor("#D9DCE1")
    TINT = rl_colors.HexColor("#FCF5F5")
    W, H = LETTER

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("body", parent=styles["Normal"],
                                fontName="Times-Roman", fontSize=11, leading=16,
                                alignment=TA_JUSTIFY, textColor=DARK)
    date_style = ParagraphStyle("date", parent=styles["Normal"],
                                fontName="Times-Italic", fontSize=10.5, leading=13,
                                textColor=GRAY)

    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "assets", "logo.jpg")
    csz = f"{sender.get('city','')}, {sender.get('state','')} {sender.get('zip','')}"

    buf = io.BytesIO()

    def _draw_static(c: _canvas.Canvas, doc, first: bool = True):
        c.saveState()
        lob_first = for_lob and first
        # ── Membrete ────────────────────────────────────────────
        # Remitente arriba-izquierda (Lob imprime su propio bloque en pág. 1)
        if not lob_first:
            c.setFillColor(DARK)
            c.setFont("Helvetica-Bold", 12.5)
            c.drawString(0.8 * inch, 10.52 * inch, (sender.get("name", "") or "").upper(),
                         charSpace=1.1)
            c.setFont("Helvetica", 7.8)
            c.setFillColor(RED)
            if sender.get("company"):
                c.drawString(0.8 * inch, 10.36 * inch, sender["company"].upper(), charSpace=0.8)
            c.setFillColor(GRAY)
            c.setFont("Helvetica", 8.8)
            c.drawString(0.8 * inch, 10.20 * inch, sender.get("address", ""))
            c.drawString(0.8 * inch, 10.06 * inch, csz)
            if sender.get("phone"):
                c.drawString(0.8 * inch, 9.92 * inch, sender["phone"])
        # Logo arriba-derecha con web debajo (fuera de la zona de dirección de Lob)
        try:
            c.drawImage(logo_path, W - 0.8 * inch - 0.72 * inch, 10.06 * inch,
                        width=0.72 * inch, height=0.72 * inch, mask="auto")
            c.setFont("Helvetica", 7)
            c.drawRightString(W - 0.8 * inch, 9.92 * inch, "rosshouserentals.com")
        except Exception:
            pass
        # Divisor de marca: trazo rojo grueso + línea fina
        if not lob_first:
            c.setStrokeColor(RED)
            c.setLineWidth(2.6)
            c.line(0.8 * inch, 9.72 * inch, 2.15 * inch, 9.72 * inch)
            c.setStrokeColor(LIGHT)
            c.setLineWidth(0.7)
            c.line(2.15 * inch, 9.72 * inch, W - 0.8 * inch, 9.72 * inch)
        # ── Destinatario (zona de ventana ~2" desde arriba) ─────
        if first:
            if not for_lob:
                c.setFillColor(DARK)
                c.setFont("Helvetica", 10.5)
                addr_top = 8.55 * inch
                lines = [lead.get("owner_name", "").strip().title() or lead.get("owner_name", "").strip()]
                lines += [ln for ln in (lead.get("mailing_lines") or []) if ln]
                for i, ln in enumerate(lines[:5]):
                    c.drawString(0.9 * inch, addr_top - i * 0.18 * inch, ln)
            # ── Foto aérea de la propiedad (derecha, zona libre) ──
            if photo:
                try:
                    from reportlab.lib.utils import ImageReader
                    pw, ph = 2.55 * inch, 1.55 * inch
                    px_ = W - 0.9 * inch - pw
                    py_ = 8.06 * inch
                    c.drawImage(ImageReader(io.BytesIO(photo)), px_, py_,
                                width=pw, height=ph)
                    c.setStrokeColor(LIGHT)
                    c.setLineWidth(1)
                    c.rect(px_, py_, pw, ph)
                    c.setFont("Helvetica-Oblique", 7.3)
                    c.setFillColor(GRAY)
                    caption = ("Vista aérea de su propiedad" if lang == "es"
                               else "Aerial view of your property")
                    c.drawRightString(px_ + pw, py_ - 0.13 * inch, caption)
                except Exception:
                    pass
        # ── Pie de página ────────────────────────────────────────
        c.setStrokeColor(RED)
        c.setLineWidth(1.4)
        c.line(0.8 * inch, 0.85 * inch, W - 0.8 * inch, 0.85 * inch)
        c.setFont("Helvetica", 7.4)
        c.setFillColor(GRAY)
        footer = " · ".join(filter(None, [
            sender.get("name", ""), sender.get("company", ""),
            f"{sender.get('address','')}, {csz}", sender.get("phone", ""),
            "rosshouserentals.com"]))
        c.drawCentredString(W / 2, 0.64 * inch, footer)
        c.restoreState()

    # ── Tarjeta de firma (logo + contacto, con barra de acento) ──
    sig_name = ParagraphStyle("sn", parent=styles["Normal"], fontName="Helvetica-Bold",
                              fontSize=11, leading=14, textColor=DARK)
    sig_meta = ParagraphStyle("sm", parent=styles["Normal"], fontName="Helvetica",
                              fontSize=8.8, leading=12.5, textColor=GRAY)
    flip_style = ParagraphStyle("flip", parent=styles["Normal"], fontName="Helvetica-Bold",
                                fontSize=8.5, leading=11, textColor=RED, alignment=2)

    def _sig_wrap():
        sig_cell = [Paragraph(sender.get("name", "Yoandy Ross"), sig_name),
                    Paragraph(f"{sender.get('company','Ross House Rentals LLC')} — Dumas, TX", sig_meta),
                    Paragraph(f"{sender.get('phone','')} · {sender.get('email','yoandyross@gmail.com')}", sig_meta)]
        try:
            sig_logo = RLImage(logo_path, width=0.52 * inch, height=0.52 * inch)
        except Exception:
            sig_logo = ""
        sig_tbl = Table([["", sig_logo, sig_cell]],
                        colWidths=[0.055 * inch, 0.75 * inch, 4.6 * inch])
        sig_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (0, -1), RED),
            ("LEFTPADDING", (1, 0), (1, -1), 10),
            ("LEFTPADDING", (2, 0), (2, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        wrap = Table([[sig_tbl]], colWidths=[5.5 * inch])
        wrap.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        wrap.hAlign = "LEFT"
        return wrap

    def _cta_tbl(lg: str):
        offer = lead.get("offer") or {}
        if not offer.get("slug"):
            return None
        url = f"{SITE_BASE}/oferta/{offer['slug']}"
        qr_buf = io.BytesIO(_offer_qr_png(url))
        qr_img = RLImage(qr_buf, width=1.12 * inch, height=1.12 * inch)
        if lg == "es":
            kicker = "RESPONDA EN LÍNEA — 1 MINUTO"
            cta_title = (f"Nuestra oferta en efectivo: <font color='#C41428'>${offer['amount']:,.0f}</font>"
                         if offer.get("mode") == "amount" and offer.get("amount")
                         else "Díganos cuánto aceptaría por su propiedad")
            cta_body = ("Escanee el código QR con la cámara de su teléfono o visite "
                        f"<b>{url.replace('https://www.','')}</b> para ver su oferta "
                        "personalizada y responder al instante.<br/>"
                        "<i>Sin compromiso · Compramos AS-IS · Oferta válida por 30 días</i>")
        else:
            kicker = "RESPOND ONLINE — TAKES 1 MINUTE"
            cta_title = (f"Our cash offer: <font color='#C41428'>${offer['amount']:,.0f}</font>"
                         if offer.get("mode") == "amount" and offer.get("amount")
                         else "Tell us your price for the property")
            cta_body = ("Scan the QR code with your phone camera or visit "
                        f"<b>{url.replace('https://www.','')}</b> to view your "
                        "personalized offer and respond instantly.<br/>"
                        "<i>No obligation · We buy AS-IS · Offer valid for 30 days</i>")
        kicker_style = ParagraphStyle("kick", parent=styles["Normal"], fontName="Helvetica-Bold",
                                      fontSize=7.6, leading=10, textColor=RED)
        cta_bold = ParagraphStyle("ctab", parent=styles["Normal"], fontName="Helvetica-Bold",
                                  fontSize=13, leading=16, textColor=DARK)
        cta_style = ParagraphStyle("cta", parent=styles["Normal"], fontName="Helvetica",
                                   fontSize=9.4, leading=13.5, textColor=DARK)
        cell = [Paragraph(kicker, kicker_style), Spacer(1, 3),
                Paragraph(cta_title, cta_bold), Spacer(1, 5), Paragraph(cta_body, cta_style)]
        tbl = Table([[qr_img, cell]], colWidths=[1.45 * inch, 5.05 * inch])
        style_cmds = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, -1), TINT),
            ("BOX", (0, 0), (-1, -1), 1.3, RED),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]
        try:
            tbl.setStyle(TableStyle(style_cmds + [("ROUNDEDCORNERS", [8, 8, 8, 8])]))
        except Exception:
            tbl.setStyle(TableStyle(style_cmds))
        return tbl

    def _lang_story(lg: str, flip_note: str, fs: float = 11, ld: float = 16,
                    gap: float = 0.09):
        b_style = ParagraphStyle(f"body{fs}", parent=body_style,
                                 fontSize=fs, leading=ld)
        txt = (lead.get("offer_letter") or {}).get(
            "letter_en" if lg == "en" else "letter_es", "") \
            .replace("[TELÉFONO]", sender.get("phone", "")) \
            .replace("[PHONE]", sender.get("phone", "")) \
            .replace("[EMAIL]", sender.get("email", "yoandyross@gmail.com"))
        s = [Paragraph(flip_note, flip_style), Spacer(1, 0.06 * inch),
             Paragraph(_letter_date(lg), date_style), Spacer(1, 0.12 * inch)]
        for para in [p for p in txt.split("\n") if p.strip()]:
            s.append(Paragraph(para.replace("&", "&amp;").replace("<", "&lt;"), b_style))
            s.append(Spacer(1, gap * inch))
        s.append(Spacer(1, 0.08 * inch))
        s.append(_sig_wrap())
        cta = _cta_tbl(lg)
        if cta is not None:
            s.append(Spacer(1, 0.16 * inch))
            s.append(cta)
        return s

    # ── Documento bilingüe: página 1 = idioma primario (con destinatario y
    #    foto), página 2 = el otro idioma (para imprimir por ambos lados).
    #    Auto-ajuste: reduce tipografía hasta que cada idioma quepa en UNA cara ──
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
                                    NextPageTemplate, PageBreak)
    primary = "es" if lang == "es" else "en"
    secondary = "en" if primary == "es" else "es"
    note_front = ("English version on the other side →" if primary == "es"
                  else "¿Prefiere español? → Vea el reverso")
    note_back = ("¿Prefiere español? → Vea el frente" if primary == "es"
                 else "English version on the front side →")

    def _make_doc(target_buf, fs, ld, gap):
        frame_first = Frame(0.9 * inch, 1.0 * inch, W - 1.8 * inch,
                            H - 3.15 * inch - 1.0 * inch, id="f1")
        frame_later = Frame(0.9 * inch, 1.0 * inch, W - 1.8 * inch,
                            H - 1.55 * inch - 1.0 * inch, id="f2")
        d = BaseDocTemplate(target_buf, pagesize=LETTER)
        d.addPageTemplates([
            PageTemplate(id="first", frames=[frame_first],
                         onPage=lambda c, dd: _draw_static(c, dd, True)),
            PageTemplate(id="later", frames=[frame_later],
                         onPage=lambda c, dd: _draw_static(c, dd, False)),
        ])
        story = ([NextPageTemplate("later")]
                 + _lang_story(primary, note_front, fs, ld, gap)
                 + [PageBreak()]
                 + _lang_story(secondary, note_back, fs, ld, gap))
        d.build(story)
        return d.page

    # Prueba niveles de compactación hasta lograr exactamente 2 páginas (1 hoja dúplex)
    levels = [(11, 16, 0.09), (10.3, 14.5, 0.07), (9.6, 13.2, 0.055), (8.9, 12.2, 0.045)]
    for fs, ld, gap in levels:
        buf = io.BytesIO()
        pages = _make_doc(buf, fs, ld, gap)
        if pages <= 2:
            break
    return buf.getvalue()


@router.get("/admin/deal-finder/leads/{lead_id}/letter.pdf")
async def download_letter_pdf(request: Request, lead_id: str, lang: str = "en",
                              token: Optional[str] = None):
    # Auth por header (web) o por query token (app móvil abre el PDF en el navegador)
    if token and not request.headers.get("Authorization"):
        import jwt as _jwt
        from rental.shared import TENANT_JWT_SECRET
        try:
            payload = _jwt.decode(token, TENANT_JWT_SECRET, algorithms=["HS256"])
            if not (payload.get("type") == "marketplace" and payload.get("role") == "admin"):
                raise ValueError("no admin")
        except Exception:
            raise HTTPException(401, "No autorizado")
    else:
        await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    if not (doc.get("offer_letter") or {}).get("letter_en"):
        raise HTTPException(400, "Genera primero la carta de oferta con AI")
    lang = "es" if lang == "es" else "en"
    sender = await _sender_info()
    photo = await _lead_photo(db, doc)
    pdf = _build_letter_pdf(doc, sender, lang, photo)
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


TRACK_LABELS = {
    "Mailed": "📮 Despachada por USPS",
    "In Transit": "🚚 En tránsito",
    "In Local Area": "📍 En el área local del destinatario",
    "Processed for Delivery": "📬 Procesada para entrega (llega hoy/mañana)",
    "Re-Routed": "↩️ Re-enrutada por USPS",
    "Returned to Sender": "⛔ Devuelta al remitente",
}


@router.get("/admin/deal-finder/leads/{lead_id}/mail-status")
async def mail_tracking(request: Request, lead_id: str):
    """Rastreo REAL de la carta física: consulta los tracking events de USPS vía Lob
    y actualiza el estado en la base de datos."""
    await auth_admin(request)
    key = _lob_key()
    if not key:
        raise HTTPException(400, "Lob no está configurado")
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc or not (doc.get("mail") or {}).get("lob_id"):
        raise HTTPException(404, "Este lead no tiene carta enviada por Lob")
    lob_id = doc["mail"]["lob_id"]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"https://api.lob.com/v1/letters/{lob_id}", auth=(key, ""))
    if r.status_code != 200:
        raise HTTPException(400, "Carta no encontrada en Lob (¿se envió con otra API key test/live?)")
    letter = r.json()
    events = [{
        "name": e.get("name"),
        "label": TRACK_LABELS.get(e.get("name"), e.get("name")),
        "time": e.get("time"),
        "location": e.get("location"),
    } for e in (letter.get("tracking_events") or [])]
    last = events[-1]["name"] if events else None
    friendly = ("delivered_soon" if last == "Processed for Delivery"
                else "returned" if last == "Returned to Sender"
                else "in_transit" if last in ("In Transit", "In Local Area", "Re-Routed", "Mailed")
                else doc["mail"].get("status", "mailed"))
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {
        "mail.tracking_events": events,
        "mail.tracking_status": friendly,
        "mail.expected_delivery": letter.get("expected_delivery_date"),
        "mail.send_date": letter.get("send_date"),
        "mail.last_tracked_at": datetime.utcnow(),
    }})
    return {
        "lob_id": lob_id,
        "status": friendly,
        "expected_delivery": letter.get("expected_delivery_date"),
        "send_date": (letter.get("send_date") or "")[:10],
        "carrier": "USPS First Class",
        "events": events,
        "note": ("USPS aún no reporta eventos — normal en las primeras 24-72h tras el envío"
                 if not events else None),
    }


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

        # 2) PDF bilingüe (idéntico a la vista previa) — Lob imprime doble cara.
        #    for_lob=True deja limpia la zona superior de la pág. 1 para que Lob
        #    imprima ahí el bloque de remitente/destinatario.
        photo = await _lead_photo(db, doc)
        pdf_bytes = _build_letter_pdf(doc, sender, "en", photo=photo, for_lob=True)

        data = {
            "description": f"Carta oferta — {doc.get('address', doc['property_id'])}",
            "color": "true", "double_sided": "true",
            "address_placement": "top_first_page",
            "mail_type": "usps_first_class", "use_type": "marketing",
        }
        data.update(_lob_addr("to", doc.get("owner_name", ""), street, city, state, zipc))
        data.update(_lob_addr("from", sender.get("name", ""),
                              [sender.get("address", "")], sender.get("city", ""),
                              sender.get("state", ""), sender.get("zip", "")))

        idem = f"lead-{lead_id}-{int(datetime.now().timestamp())}"
        r = await client.post("https://api.lob.com/v1/letters", auth=(key, ""),
                              data=data,
                              files={"file": ("carta.pdf", pdf_bytes, "application/pdf")},
                              headers={"Idempotency-Key": idem})
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
