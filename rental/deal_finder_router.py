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
    "potter": {"name": "Potter County (Amarillo)", "base": "", "active": False},
    "sherman": {"name": "Sherman County", "base": "", "active": False},
    "hartley": {"name": "Hartley County", "base": "", "active": False},
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
                prop_id = str(item.get("propertyId") or "").strip()
                if not prop_id:
                    continue
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
                    tax = await fetch_account_tax_due(prop_id)
                    lead["tax_due_total"] = tax["total_due"]
                    lead["tax_years_due"] = [y["year"] for y in tax["years_due"]]
                except Exception as e:
                    logger.warning(f"[deal_finder] tax due failed {prop_id}: {e}")
                    lead["tax_due_total"] = 0
                    lead["tax_years_due"] = []
                await asyncio.sleep(0.5)

                lead["signals"] = compute_signals(lead)
                lead["last_synced_at"] = datetime.now(timezone.utc)
                processed += 1
                await _update(processed=processed)

                if only_delinquent and "tax_delinquent" not in lead["signals"]:
                    continue

                res = await db.deal_finder_leads.update_one(
                    {"county": county, "property_id": prop_id},
                    {"$set": lead,
                     "$setOnInsert": {"status": "new", "notes": "",
                                      "created_at": datetime.now(timezone.utc)}},
                    upsert=True,
                )
                if res.upserted_id:
                    new_leads += 1
                else:
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
