"""
Enriquecimiento de contactos y señales de motivación — Deal Finder Premium
═══════════════════════════════════════════════════════════════════════════
Nivel 1 · Validación:  Twilio Lookup (tipo de línea) + ZeroBounce (emails)
Nivel 2 · Cascada:     Tracerfy → BatchData (skip tracing en cadena)
Nivel 3 · Motivación:  PropertyRadar (probate/divorcio/evicción/pre-foreclosure)
                       + Obituarios locales (gratis, extracción con IA)

Diseño modular: cada proveedor es un adaptador independiente que se activa
solo si su API key está configurada (Configuración → API Keys).
"""
import logging
import os
import re
import secrets
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from rental.shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Contact Enrichment"])

MOORE_COUNTY_FIPS = 48341
LOOKUP_CACHE_DAYS = 90

# ═══════════════════════════════════════════════════════════════
# Registro de proveedores (para la UI "Fuentes de datos")
# ═══════════════════════════════════════════════════════════════

PROVIDERS = [
    {"id": "twilio_lookup", "name": "Twilio Lookup — Tipo de línea", "level": 1,
     "keys": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
     "price": "$0.008 por número", "signup": "console.twilio.com",
     "what": "Detecta si un teléfono es móvil, fijo o VoIP antes de enviar SMS"},
    {"id": "zerobounce", "name": "ZeroBounce — Validación de emails", "level": 1,
     "keys": ["ZEROBOUNCE_API_KEY"],
     "price": "~$0.004 por email", "signup": "zerobounce.net",
     "what": "Verifica que el email exista y no rebote (protege tu SendGrid)"},
    {"id": "tracerfy", "name": "Tracerfy — Skip tracing (1ª fuente)", "level": 2,
     "keys": ["TRACERFY_API_KEY"],
     "price": "5 créditos por acierto", "signup": "tracerfy.com",
     "what": "Encuentra teléfonos y emails del dueño"},
    {"id": "batchdata", "name": "BatchData — Skip tracing (2ª fuente)", "level": 2,
     "keys": ["BATCHDATA_API_KEY"],
     "price": "$0.07–0.20 por registro", "signup": "batchdata.com",
     "what": "Fallback cuando Tracerfy no encuentra contacto; fuerte en datos TCPA"},
    {"id": "propertyradar", "name": "PropertyRadar — Señales de motivación", "level": 3,
     "keys": ["PROPERTYRADAR_API_KEY"],
     "price": "plan desde ~$59/mes (API incluida)", "signup": "propertyradar.com",
     "what": "Probate, divorcio, evicciones y pre-foreclosure en Moore County"},
    {"id": "obituaries", "name": "Obituarios locales — Gratis (IA)", "level": 3,
     "keys": ["EMERGENT_LLM_KEY"],
     "price": "Gratis (usa tu llave de IA)", "signup": "",
     "what": "Escanea obituarios de Dumas/Moore County y los cruza con tus leads (posible herencia)"},
]


@router.get("/admin/enrichment/providers")
async def enrichment_providers(request: Request):
    await auth_admin(request)
    out = []
    for p in PROVIDERS:
        out.append({**p, "configured": all(os.environ.get(k) for k in p["keys"])})
    return {"providers": out}


# ═══════════════════════════════════════════════════════════════
# NIVEL 1 · Twilio Lookup (tipo de línea) — con caché de 90 días
# ═══════════════════════════════════════════════════════════════

def _e164(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return ("+" + digits) if digits else ""


async def twilio_line_lookup(phone: str) -> Optional[dict]:
    """Devuelve {line_type, carrier, valid, sms_ok} o None si no hay llaves/falla.
    Cachea 90 días en phone_lookups para no pagar dos veces por el mismo número."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    e164 = _e164(phone)
    if not (sid and token and e164):
        return None
    db = get_db()
    now = datetime.now(timezone.utc)
    cached = await db.phone_lookups.find_one({"phone": e164, "expires_at": {"$gt": now}})
    if cached:
        return {"line_type": cached.get("line_type"), "carrier": cached.get("carrier"),
                "valid": cached.get("valid"), "sms_ok": cached.get("sms_ok"), "cached": True}
    try:
        async with httpx.AsyncClient(timeout=12, auth=(sid, token)) as client:
            r = await client.get(
                f"https://lookups.twilio.com/v2/PhoneNumbers/{urllib.parse.quote(e164, safe='')}",
                params={"Fields": "line_type_intelligence"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[enrichment] twilio lookup failed for {e164}: {e}")
        return None
    lti = data.get("line_type_intelligence") or {}
    line_type = lti.get("type")
    valid = bool(data.get("valid"))
    result = {"line_type": line_type, "carrier": lti.get("carrier_name"),
              "valid": valid, "sms_ok": valid and line_type == "mobile", "cached": False}
    await db.phone_lookups.update_one({"phone": e164}, {"$set": {
        "phone": e164, **{k: result[k] for k in ("line_type", "carrier", "valid", "sms_ok")},
        "checked_at": now, "expires_at": now + timedelta(days=LOOKUP_CACHE_DAYS)}}, upsert=True)
    return result


# ═══════════════════════════════════════════════════════════════
# NIVEL 1 · ZeroBounce (validación de emails) — con caché
# ═══════════════════════════════════════════════════════════════

async def zerobounce_validate(email: str) -> Optional[dict]:
    key = os.environ.get("ZEROBOUNCE_API_KEY")
    email = (email or "").strip().lower()
    if not (key and email):
        return None
    db = get_db()
    now = datetime.now(timezone.utc)
    cached = await db.email_validations.find_one({"email": email, "expires_at": {"$gt": now}})
    if cached:
        return {"status": cached.get("status"), "sub_status": cached.get("sub_status"), "cached": True}
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            r = await client.get("https://api.zerobounce.net/v2/validate",
                                 params={"api_key": key, "email": email, "timeout": 30})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[enrichment] zerobounce failed for {email}: {e}")
        return None
    result = {"status": data.get("status"), "sub_status": data.get("sub_status"), "cached": False}
    await db.email_validations.update_one({"email": email}, {"$set": {
        "email": email, "status": result["status"], "sub_status": result["sub_status"],
        "checked_at": now, "expires_at": now + timedelta(days=LOOKUP_CACHE_DAYS)}}, upsert=True)
    return result


@router.post("/admin/deal-finder/leads/{lead_id}/validate-contacts")
async def validate_lead_contacts(request: Request, lead_id: str):
    """NIVEL 1: valida cada teléfono (móvil/fijo/VoIP vía Twilio Lookup) y cada
    email (ZeroBounce) del contacto del lead. Persiste el resultado en el lead."""
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")
    contact = doc.get("contact") or {}
    phones = contact.get("phones") or []
    emails = contact.get("emails") or []
    if not phones and not emails:
        raise HTTPException(400, "El lead no tiene contacto — corre primero el skip tracing")

    twilio_ok = bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"))
    zb_ok = bool(os.environ.get("ZEROBOUNCE_API_KEY"))

    for ph in phones[:5]:
        if twilio_ok:
            try:
                res = await twilio_line_lookup(ph.get("number", ""))
            except Exception as e:
                logger.exception("validate-contacts lookup crash")
                raise HTTPException(500, f"lookup error: {type(e).__name__}: {str(e)[:180]}")
            if res:
                ph["line_type"] = res["line_type"]
                ph["carrier"] = res.get("carrier")
                ph["sms_ok"] = res["sms_ok"]
                ph["line_checked_at"] = datetime.now(timezone.utc).isoformat()

    email_checks = []
    for em in emails[:5]:
        entry = {"email": em}
        if zb_ok:
            res = await zerobounce_validate(em)
            if res:
                entry["status"] = res["status"]
                entry["sub_status"] = res.get("sub_status")
        email_checks.append(entry)

    contact["phones"] = phones
    contact["email_checks"] = email_checks
    contact["validated_at"] = datetime.now(timezone.utc).isoformat()
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {"contact": contact}})
    return {"success": True, "contact": contact,
            "twilio_configured": twilio_ok, "zerobounce_configured": zb_ok}


# ═══════════════════════════════════════════════════════════════
# NIVEL 2 · BatchData (2ª fuente de skip tracing)
# ═══════════════════════════════════════════════════════════════

async def batchdata_trace(owner_name: str, situs: dict) -> Optional[dict]:
    """Skip trace vía BatchData v3. Devuelve {phones, emails} o None."""
    key = os.environ.get("BATCHDATA_API_KEY")
    if not key:
        return None
    address = {"street": situs["street"], "state": situs["state"]}
    if situs.get("city"):
        address["city"] = situs["city"]
    if situs.get("zip"):
        address["zip"] = situs["zip"]
    payload = [{"name": owner_name, "propertyAddress": address,
                "includeTCPABlacklistedPhones": False, "dateFormat": "iso-date-time"}]
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            r = await client.post("https://api.batchdata.com/api/v3/property/skip-trace",
                                  headers={"Authorization": f"Bearer {key}",
                                           "Content-Type": "application/json",
                                           "Accept": "application/json"},
                                  json=payload)
        if r.status_code in (401, 403):
            raise HTTPException(502, "API key de BatchData inválida")
        if r.status_code == 402:
            raise HTTPException(402, "Sin créditos en BatchData — recarga en batchdata.com")
        r.raise_for_status()
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[enrichment] batchdata failed: {e}")
        return None
    phones, emails = [], []
    rows = (data.get("result") or {}).get("data") or data.get("data") or []
    for row in rows if isinstance(rows, list) else []:
        persons = row.get("persons") or [row]
        for p in persons[:3]:
            for ph in (p.get("phones") or [])[:5]:
                num = ph.get("number") or ph.get("phone") or ""
                if num and not any(x["number"] == num for x in phones):
                    phones.append({"number": num, "type": ph.get("type", ""),
                                   "dnc": bool(ph.get("dnc") or ph.get("dncLitigator")),
                                   "tcpa": bool(ph.get("tcpa") or ph.get("tcpaBlacklisted"))})
            for em in (p.get("emails") or [])[:5]:
                e = em.get("email") if isinstance(em, dict) else str(em)
                if e and e not in emails:
                    emails.append(e)
    return {"phones": phones[:5], "emails": emails[:5]}


@router.post("/admin/deal-finder/leads/{lead_id}/skip-trace-cascade")
async def skip_trace_cascade(request: Request, lead_id: str):
    """NIVEL 2: cascada de skip tracing. Intenta Tracerfy primero; si no encuentra
    (o para completar datos faltantes) intenta BatchData. Al final valida las
    líneas automáticamente con Twilio Lookup si está configurado."""
    await auth_admin(request)
    db = get_db()
    doc = await db.deal_finder_leads.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(404, "Lead no encontrado")

    from rental.deal_finder_router import _parse_situs, _owner_first_last, ENTITY_OWNER_RX, tracerfy_trace

    owner = doc.get("owner_name") or ""
    situs = _parse_situs(doc.get("address") or "")
    if not situs:
        raise HTTPException(422, "El lead no tiene dirección física completa para rastrear")

    sources_tried, phones, emails = [], [], []
    contact = doc.get("contact") or {}

    # Paso 1 — Tracerfy (solo personas, no entidades)
    if os.environ.get("TRACERFY_API_KEY") and not ENTITY_OWNER_RX.search(owner):
        first, last = _owner_first_last(owner)
        if first and last:
            try:
                t = await tracerfy_trace(situs, first, last)
                if t:
                    phones.extend(t["phones"])
                    emails.extend(t["emails"])
                    sources_tried.append({"source": "tracerfy", "hit": bool(t["phones"] or t["emails"]),
                                          "credits_used": t.get("credits_used", 0)})
            except HTTPException as e:
                sources_tried.append({"source": "tracerfy", "hit": False, "error": str(e.detail)[:120]})

    # Paso 2 — BatchData (fallback / complemento; también funciona con LLCs)
    need_more = not phones or not emails
    if need_more and os.environ.get("BATCHDATA_API_KEY"):
        try:
            b = await batchdata_trace(owner, situs)
        except HTTPException as e:
            b = None
            sources_tried.append({"source": "batchdata", "hit": False, "error": str(e.detail)[:120]})
        if b is not None:
            added = 0
            for ph in b["phones"]:
                if not any(re.sub(r"\D", "", x["number"])[-10:] == re.sub(r"\D", "", ph["number"])[-10:] for x in phones):
                    phones.append(ph)
                    added += 1
            for em in b["emails"]:
                if em not in emails:
                    emails.append(em)
                    added += 1
            sources_tried.append({"source": "batchdata", "hit": added > 0})

    if not sources_tried:
        raise HTTPException(400, "Ninguna fuente de skip tracing está configurada — "
                                 "agrega TRACERFY_API_KEY o BATCHDATA_API_KEY en Configuración → API Keys")

    # Paso 3 — Validación automática de líneas (silenciosa)
    for ph in phones[:5]:
        res = await twilio_line_lookup(ph.get("number", ""))
        if res:
            ph["line_type"] = res["line_type"]
            ph["carrier"] = res.get("carrier")
            ph["sms_ok"] = res["sms_ok"]

    contact.update({
        "phones": phones[:5], "emails": emails[:5],
        "hit": bool(phones or emails),
        "traced_at": datetime.now(timezone.utc).isoformat(),
        "source": "cascade",
        "sources": sources_tried,
    })
    await db.deal_finder_leads.update_one({"_id": doc["_id"]}, {"$set": {"contact": contact}})
    return {"success": True, "contact": contact, "sources": sources_tried}


# ═══════════════════════════════════════════════════════════════
# NIVEL 3 · PropertyRadar — señales de motivación
# ═══════════════════════════════════════════════════════════════

RADAR_CRITERIA = {
    "probate": "inProbateProperty",
    "divorce": "inDivorce",
    "eviction": "hasRecentEviction",
    "preforeclosure": "isPreforeclosure",
}
SIGNAL_LABELS = {
    "probate": "⚖️ Probate (herencia)",
    "divorce": "💔 Divorcio",
    "eviction": "📤 Evicción reciente",
    "preforeclosure": "🏚️ Pre-foreclosure",
    "possible_deceased": "🕊️ Posible fallecido",
}


def _norm_street(addr: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", "", (addr or "").upper())
    s = re.sub(r"\b(STREET|AVENUE|DRIVE|LANE|ROAD|BOULEVARD|COURT|PLACE)\b",
               lambda m: {"STREET": "ST", "AVENUE": "AVE", "DRIVE": "DR", "LANE": "LN",
                          "ROAD": "RD", "BOULEVARD": "BLVD", "COURT": "CT", "PLACE": "PL"}[m.group(0)], s)
    return re.sub(r"\s+", " ", s).strip()


class MotivationScanBody(BaseModel):
    signals: list[str] = ["probate", "preforeclosure", "divorce", "eviction"]
    limit: int = 100
    county_fips: int = MOORE_COUNTY_FIPS


@router.post("/admin/deal-finder/motivation-scan")
async def motivation_scan(request: Request, body: MotivationScanBody):
    """NIVEL 3: consulta PropertyRadar (Purchase=0, modo preview — sin cargos por
    registro completo) y cruza los resultados con tus leads por dirección.
    A los que coinciden les marca las señales de motivación."""
    await auth_admin(request)
    key = os.environ.get("PROPERTYRADAR_API_KEY")
    if not key:
        raise HTTPException(400, "Configura PROPERTYRADAR_API_KEY en Configuración → API Keys "
                                 "(crea cuenta en propertyradar.com — la API viene incluida en todos los planes)")
    signals = [s for s in body.signals if s in RADAR_CRITERIA]
    if not signals:
        raise HTTPException(422, "Elige al menos una señal válida")

    criteria = [{"name": "County", "value": [body.county_fips]}]
    criteria += [{"name": RADAR_CRITERIA[s], "value": True} for s in signals]
    fields = ["RadarID", "Address", "City", "ZipFive", "Owner",
              "isPreforeclosure", "inProbateProperty", "inDivorce", "hasRecentEviction"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.propertyradar.com/v1/properties",
                                  params={"Purchase": 0, "Start": 0, "Limit": min(body.limit, 200)},
                                  headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                                  json={"Criteria": criteria, "Fields": fields})
        if r.status_code in (401, 403):
            raise HTTPException(502, "API key de PropertyRadar inválida")
        if r.status_code == 402:
            raise HTTPException(402, "Sin balance en PropertyRadar")
        r.raise_for_status()
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"PropertyRadar no disponible: {e}")

    results = data.get("results") or []
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    matched, unmatched = [], []
    for prop in results:
        street = _norm_street(prop.get("Address") or "")
        if not street:
            continue
        prop_signals = [s for s, crit in RADAR_CRITERIA.items() if prop.get(crit)]
        if not prop_signals:
            prop_signals = signals
        lead = await db.deal_finder_leads.find_one(
            {"address": {"$regex": f"^{re.escape(street)}", "$options": "i"}})
        if not lead:
            # segundo intento: normalizando la dirección del lead en memoria es caro;
            # usamos búsqueda parcial por número + primera palabra de la calle
            parts = street.split(" ")
            if len(parts) >= 2:
                lead = await db.deal_finder_leads.find_one(
                    {"address": {"$regex": f"^{re.escape(parts[0])}\\s+{re.escape(parts[1])}", "$options": "i"}})
        if lead:
            motivation = lead.get("motivation") or {"signals": [], "details": []}
            new_signals = sorted(set(motivation.get("signals", []) + prop_signals))
            motivation["signals"] = new_signals
            motivation["details"] = (motivation.get("details") or []) + [{
                "source": "propertyradar", "radar_id": prop.get("RadarID"),
                "signals": prop_signals, "at": now}]
            motivation["updated_at"] = now
            await db.deal_finder_leads.update_one({"_id": lead["_id"]}, {"$set": {"motivation": motivation}})
            matched.append({"lead_id": str(lead["_id"]), "address": lead.get("address"),
                            "signals": prop_signals})
        else:
            unmatched.append({"address": prop.get("Address"), "city": prop.get("City"),
                              "signals": prop_signals})
    return {"success": True, "scanned": len(results),
            "matched": matched, "unmatched": unmatched[:25],
            "result_count": data.get("resultCount"),
            "note": "Modo preview (Purchase=0): no se compraron registros de contacto."}


# ═══════════════════════════════════════════════════════════════
# REGISTROS PÚBLICOS DEL CONDADO — importador universal con IA
# (probate, evicciones, divorcios, subastas, code violations, vacantes)
# ═══════════════════════════════════════════════════════════════

PUBLIC_SOURCES = [
    {"id": "probate", "signal": "probate_confirmed", "name": "⚖️ Corte de Probate — County Clerk",
     "url": "https://public.lgsonlinesolutions.com/ors.html",
     "steps": "Guest Login → Court Records → tipo 'Probate' → busca por fecha reciente → copia el índice (nombres/casos) y pégalo aquí"},
    {"id": "eviction", "signal": "eviction_filed", "name": "📤 Evicciones — JP Court",
     "url": "https://public.lgsonlinesolutions.com/ors.html",
     "steps": "Guest Login → Court Records → 'Civil/JP' → casos de eviction → copia el índice y pégalo aquí"},
    {"id": "divorce", "signal": "divorce_filed", "name": "💔 Divorcios — District Clerk",
     "url": "https://public.lgsonlinesolutions.com/ors.html",
     "steps": "Guest Login → Court Records → 'Family/Divorce' → copia el índice y pégalo aquí"},
    {"id": "tax_sale", "signal": "tax_sale", "name": "🔨 Subastas de impuestos (Tax Sales)",
     "url": "https://www.co.moore.tx.us/page/moore.Tax.AssessorCollector",
     "steps": "Pide la lista de próximas tax sales al Tax Office (o al bufete que ejecuta) y pega aquí la lista de propiedades"},
    {"id": "code_violation", "signal": "code_violation", "name": "🏚️ Code Violations — Ciudad de Dumas",
     "url": "https://www.ci.dumas.tx.us",
     "steps": "Usa el botón 'Enviar solicitud TPIA' de abajo; cuando la ciudad responda, pega aquí la lista de multas"},
    {"id": "vacancy", "signal": "vacant", "name": "📮 Vacantes / Registro de votantes",
     "url": "https://www.votetexas.gov",
     "steps": "Pega aquí cualquier lista de direcciones vacantes o padrón que consigas (CSV o texto)"},
]

PUBLIC_SIGNAL_BY_TYPE = {s["id"]: s["signal"] for s in PUBLIC_SOURCES}


@router.get("/admin/deal-finder/public-records/guide")
async def public_records_guide(request: Request):
    await auth_admin(request)
    return {"sources": PUBLIC_SOURCES}


class PublicRecordsImport(BaseModel):
    source_type: str
    text: str


@router.post("/admin/deal-finder/public-records/import")
async def public_records_import(request: Request, body: PublicRecordsImport):
    """Pega el índice crudo del portal del condado (o CSV) → la IA lo estructura →
    se cruza con tus leads por nombre de dueño Y por dirección → badges de motivación."""
    await auth_admin(request)
    if body.source_type not in PUBLIC_SIGNAL_BY_TYPE:
        raise HTTPException(422, f"Tipo inválido. Usa: {', '.join(PUBLIC_SIGNAL_BY_TYPE)}")
    text = (body.text or "").strip()
    if len(text) < 20:
        raise HTTPException(422, "Pega el texto del índice (mínimo unas líneas)")
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(400, "Falta EMERGENT_LLM_KEY para la extracción con IA")

    from emergentintegrations.llm.chat import LlmChat, UserMessage
    import json as json_lib
    chat = LlmChat(api_key=api_key, session_id=f"pubrec-{secrets.token_hex(6)}",
                   system_message="Extraes datos estructurados de índices de registros públicos de condados de Texas. "
                                  "Respondes SOLO con JSON válido.")
    chat = chat.with_model("anthropic", "claude-sonnet-4-5-20250929")
    prompt = (f"Este texto es un índice de registros de tipo '{body.source_type}' del condado de Moore, TX.\n"
              f"Extrae TODAS las entradas. Formato JSON EXACTO:\n"
              f'[{{"name":"APELLIDO NOMBRE o nombre de la parte","address":"direccion si aparece o null",'
              f'"case_number":"numero de caso o null","date":"YYYY-MM-DD o null"}}]\n'
              f"Si es una lista de propiedades (tax sale/vacantes) usa address y deja name null.\n"
              f"Si no hay nada devuelve [].\n\nTEXTO:\n{text[:14000]}")
    raw = await chat.send_message(UserMessage(text=prompt))
    raw = raw if isinstance(raw, str) else str(raw)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    records = []
    if m:
        try:
            records = [r for r in json_lib.loads(m.group(0)) if isinstance(r, dict)][:200]
        except Exception:
            records = []
    if not records:
        return {"success": True, "records_found": 0, "matches": [],
                "note": "La IA no encontró entradas en el texto pegado"}

    db = get_db()
    signal = PUBLIC_SIGNAL_BY_TYPE[body.source_type]
    now = datetime.now(timezone.utc).isoformat()
    matches, new_matches = [], []
    for rec in records:
        lead = None
        # 1) por dirección
        addr = _norm_street(rec.get("address") or "")
        if addr:
            parts = addr.split(" ")
            if len(parts) >= 2:
                lead = await db.deal_finder_leads.find_one(
                    {"address": {"$regex": f"^{re.escape(parts[0])}\\s+{re.escape(parts[1])}", "$options": "i"}})
        # 2) por nombre del dueño
        if lead is None and rec.get("name"):
            nparts = [p for p in re.split(r"[\s,]+", rec["name"].strip()) if len(p) > 1]
            if len(nparts) >= 2:
                a, b = nparts[0], nparts[-1]
                lead = await db.deal_finder_leads.find_one(
                    {"owner_name": {"$regex": f"{re.escape(a)}.*{re.escape(b)}|{re.escape(b)}.*{re.escape(a)}",
                                    "$options": "i"}})
        if lead is None:
            continue
        motivation = lead.get("motivation") or {"signals": [], "details": []}
        match_info = {"lead_id": str(lead["_id"]), "address": lead.get("address"),
                      "owner_name": lead.get("owner_name"), "record": rec}
        dedupe_key = rec.get("case_number") or rec.get("name") or rec.get("address")
        already = any(d.get("source") == body.source_type and
                      (d.get("case_number") or d.get("record_name")) == dedupe_key
                      for d in (motivation.get("details") or []))
        if already:
            matches.append(match_info)
            continue
        if signal not in motivation.get("signals", []):
            motivation["signals"] = sorted(set(motivation.get("signals", []) + [signal]))
        motivation["details"] = (motivation.get("details") or []) + [{
            "source": body.source_type, "record_name": rec.get("name"),
            "case_number": rec.get("case_number"), "date": rec.get("date"), "at": now}]
        motivation["updated_at"] = now
        await db.deal_finder_leads.update_one({"_id": lead["_id"]}, {"$set": {"motivation": motivation}})
        matches.append(match_info)
        new_matches.append(match_info)

    return {"success": True, "records_found": len(records),
            "matches": matches, "new_matches": new_matches, "signal": signal}


# ═══════════════════════════════════════════════════════════════
# CRON mensual: solicitud de lista STRUCK-OFF al Tax Office
# ═══════════════════════════════════════════════════════════════

TAX_OFFICE_EMAIL = "taxoffice@moore-tx.com"

STRUCKOFF_BODY = """Moore County Tax Assessor-Collector:

I hope this message finds you well. Pursuant to the Texas Public Information Act, I would like to respectfully request the current list of STRUCK-OFF properties held in trust by Moore County (properties that did not sell at tax sale and are available for private resale), including for each: property address or legal description, account/geo ID, and minimum bid or taxes owed.

I would also appreciate information on the date of the next scheduled tax sale, if available.

Electronic format (Excel/CSV or PDF) sent to this email is preferred. Please let me know if there are any fees.

Thank you for your time,
Yoandy Ross
Ross House Rentals LLC
(806) 934-2018 · info@rosshouserentals.com"""


async def _send_struckoff_request(db) -> dict:
    from rental.ai_brain_router import _send_email_branded
    cfg = await db.app_settings.find_one({"_id": "struckoff_request_cron"}) or {}
    to = cfg.get("to_email") or TAX_OFFICE_EMAIL
    html = "<p>" + STRUCKOFF_BODY.replace("\n", "<br>") + "</p>"
    ok = await _send_email_branded(to, "Request: Struck-Off Property List — Moore County (Public Information Act)",
                                   html, STRUCKOFF_BODY)
    # copia para el admin con checklist de pendientes
    pending = []
    if not os.environ.get("PROPERTYRADAR_API_KEY"):
        pending.append("🛰️ Falta la llave de PropertyRadar (propertyradar.com → Settings → API) para probate/divorcio/evicción automáticos")
    pending.append("📱 Verifica el estado de tu campaña A2P 10DLC en Twilio (Console → Messaging → Regulatory Compliance) para desbloquear los SMS")
    phtml = "".join(f"<li>{p}</li>" for p in pending)
    admin_html = (f"<h3 style='color:#B91C1C'>🔨 Solicitud struck-off enviada al Tax Office</h3>"
                  f"<p>Se envió la solicitud mensual de la lista struck-off a <b>{to}</b>. "
                  f"Cuando respondan, pega la lista en Oportunidades → 🛰️ Datos → Registros públicos (tipo 🔨 Tax Sale).</p>"
                  f"<p><b>Pendientes del Radar:</b></p><ul>{phtml}</ul>")
    await _send_email_branded(OBIT_ALERT_EMAIL, "🔨 Enviada solicitud struck-off al Tax Office + pendientes del Radar",
                              admin_html, f"Solicitud struck-off enviada a {to}. Pendientes: " + " | ".join(pending))
    return {"sent": ok, "to": to}


@router.post("/admin/deal-finder/struckoff-request")
async def struckoff_request_now(request: Request):
    """Envía AHORA la solicitud de lista struck-off al Tax Office (además del cron mensual)."""
    await auth_admin(request)
    db = get_db()
    r = await _send_struckoff_request(db)
    if not r["sent"]:
        raise HTTPException(502, "No se pudo enviar — revisa SendGrid")
    await db.app_settings.update_one({"_id": "struckoff_request_cron"},
                                     {"$set": {"last_run_at": datetime.now(timezone.utc)}}, upsert=True)
    return {"success": True, **r}


async def _struckoff_should_run(db) -> bool:
    from zoneinfo import ZoneInfo
    cfg = await db.app_settings.find_one({"_id": "struckoff_request_cron"}) or {}
    if not cfg.get("enabled", True):
        return False
    now_ct = datetime.now(ZoneInfo("America/Chicago"))
    if now_ct.day != int(cfg.get("day_of_month", 1)) or now_ct.hour != int(cfg.get("hour_ct", 9)):
        return False
    last = cfg.get("last_run_at")
    if last and isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last) < timedelta(days=20):
            return False
    return True


async def struckoff_request_loop():
    """Background: solicita la lista struck-off al Tax Office el día 1 de cada mes, 9AM CT."""
    import asyncio
    logger.info("🔨 Struck-off request cron started (día 1 de cada mes, 9AM CT)")
    while True:
        try:
            db = get_db()
            if db is not None and await _struckoff_should_run(db):
                r = await _send_struckoff_request(db)
                await db.app_settings.update_one({"_id": "struckoff_request_cron"}, {"$set": {
                    "last_run_at": datetime.now(timezone.utc), "last_result": r}}, upsert=True)
                logger.info(f"🔨 Struck-off request sent: {r}")
        except Exception:
            logger.exception("struckoff request loop error")
        await asyncio.sleep(1800)


TPIA_BODY = """Code Enforcement Department, City of Dumas:

Pursuant to the Texas Public Information Act (Tex. Gov't Code Chapter 552), I respectfully request the following public records:

A list of all code enforcement violations, notices, and citations issued within the City of Dumas during the last 12 months, including for each: property address, type of violation, date issued, and current status.

I request this information in electronic format (Excel/CSV preferred) sent to this email address. If any fees exceed $25, please contact me before processing.

Thank you,
Yoandy Ross
Ross House Rentals LLC
(806) 934-2018 · info@rosshouserentals.com"""


class TpiaBody(BaseModel):
    to_email: Optional[str] = None


@router.post("/admin/deal-finder/tpia-request")
async def send_tpia_request(request: Request, body: TpiaBody):
    """Envía (o te reenvía como borrador) la solicitud TPIA de code violations a la Ciudad de Dumas."""
    await auth_admin(request)
    from rental.ai_brain_router import _send_email_branded
    to = (body.to_email or "").strip() or "yoandyross@gmail.com"
    html = "<p>" + TPIA_BODY.replace("\n", "<br>") + "</p>"
    ok = await _send_email_branded(to, "Public Information Act Request — Code Enforcement Records (City of Dumas)",
                                   html, TPIA_BODY)
    if not ok:
        raise HTTPException(502, "No se pudo enviar el email — revisa SendGrid")
    return {"success": True, "sent_to": to,
            "note": "Si lo enviaste a tu propio correo, reenvíalo al email de Code Enforcement de Dumas"}




DEFAULT_OBITUARY_SOURCES = [
    "https://www.echovita.com/us/obituaries/tx/dumas",
    "https://www.echovita.com/us/obituaries/tx/cactus",
    "https://www.echovita.com/us/obituaries/tx/sunray",
    "https://www.morrisonfuneraldirectors.com/obituaries",
]

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

ECHOVITA_LINK_RX = re.compile(r'href="/us/obituaries/([a-z]{2})/([a-z-]+)/([a-z0-9-]+)-\d+"')


def _parse_echovita_links(html: str) -> list:
    """Los links de echovita traen el nombre del fallecido en la URL — extracción sin IA."""
    out, seen = [], set()
    for m in ECHOVITA_LINK_RX.finditer(html):
        city_slug, name_slug = m.group(2), m.group(3)
        if name_slug in seen:
            continue
        seen.add(name_slug)
        name = " ".join(p.capitalize() for p in name_slug.split("-") if p)
        city = " ".join(p.capitalize() for p in city_slug.split("-"))
        out.append({"name": name, "age": None, "city": city, "date": None})
    return out


async def _llm_extract_obituaries(page_text: str, source_url: str) -> list:
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return []
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import json as json_lib
        chat = LlmChat(api_key=api_key, session_id=f"obit-{secrets.token_hex(6)}",
                       system_message="Extraes datos estructurados de páginas de obituarios. "
                                      "Respondes SOLO con JSON válido, sin texto extra.")
        chat = chat.with_model("anthropic", "claude-sonnet-4-5-20250929")
        prompt = (f"De este texto de una página de obituarios extrae TODAS las personas fallecidas.\n"
                  f"Formato JSON EXACTO: [{{\"name\":\"Nombre Apellido\",\"age\":74,\"city\":\"Dumas\",\"date\":\"2026-08-01\"}}]\n"
                  f"Si no hay fecha exacta usa null. Si no encuentras obituarios devuelve [].\n\n"
                  f"TEXTO:\n{page_text[:14000]}")
        raw = await chat.send_message(UserMessage(text=prompt))
        raw = raw if isinstance(raw, str) else str(raw)
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return []
        items = json_lib.loads(m.group(0))
        return [i for i in items if isinstance(i, dict) and i.get("name")][:50]
    except Exception as e:
        logger.warning(f"[enrichment] LLM obituary extract failed for {source_url}: {e}")
        return []


@router.get("/admin/deal-finder/obituary-results")
async def obituary_results(request: Request):
    """Devuelve la lista del último escaneo de obituarios (para verla en la UI)."""
    await auth_admin(request)
    db = get_db()
    cfg = await db.admin_config.find_one({"type": "enrichment_config"}) or {}
    return {
        "last_scan": cfg.get("last_obituary_scan"),
        "count": cfg.get("last_obituary_count", 0),
        "obituaries": cfg.get("last_obituaries", []),
        "matches": cfg.get("last_matches", []),
    }


@router.post("/admin/deal-finder/obituary-scan")
async def obituary_scan(request: Request):
    """NIVEL 3 (GRATIS): descarga los obituarios locales de Dumas / Moore County,
    extrae los nombres con IA y los cruza con el nombre del dueño de tus leads.
    Los que coinciden se marcan como '🕊️ Posible fallecido' (candidato a probate)."""
    await auth_admin(request)
    return {"success": True, **(await run_obituary_scan())}


async def run_obituary_scan() -> dict:
    """Núcleo del escaneo de obituarios (usado por el endpoint y por el cron semanal)."""
    db = get_db()
    cfg = await db.admin_config.find_one({"type": "enrichment_config"}) or {}
    sources = cfg.get("obituary_sources") or DEFAULT_OBITUARY_SOURCES

    all_obits = []
    fetch_errors = []
    async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                 headers={"User-Agent": BROWSER_UA}) as client:
        for url in sources[:5]:
            try:
                r = await client.get(url)
                r.raise_for_status()
                if "echovita.com" in url:
                    obits = _parse_echovita_links(r.text)
                else:
                    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text)
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    obits = await _llm_extract_obituaries(text, url)
                for o in obits:
                    o["source_url"] = url
                all_obits.extend(obits)
            except Exception as e:
                fetch_errors.append({"url": url, "error": str(e)[:120]})

    # dedupe por nombre
    seen, obits = set(), []
    for o in all_obits:
        k = re.sub(r"\W", "", o["name"].lower())
        if k not in seen:
            seen.add(k)
            obits.append(o)

    # Cruce contra leads: owner_name en CAD viene como "APELLIDO NOMBRE"
    now = datetime.now(timezone.utc).isoformat()
    matches, new_matches = [], []
    for o in obits:
        parts = [p for p in re.split(r"[\s,]+", o["name"].strip()) if len(p) > 1]
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        cursor = db.deal_finder_leads.find(
            {"owner_name": {"$regex": f"{re.escape(last)}.*{re.escape(first)}|{re.escape(first)}.*{re.escape(last)}",
                            "$options": "i"}}).limit(3)
        async for lead in cursor:
            motivation = lead.get("motivation") or {"signals": [], "details": []}
            match_info = {"lead_id": str(lead["_id"]), "address": lead.get("address"),
                          "owner_name": lead.get("owner_name"), "obituary": o}
            already = any(d.get("source") == "obituary" and d.get("obit_name") == o["name"]
                          for d in (motivation.get("details") or []))
            if already:
                matches.append(match_info)
                continue
            if "possible_deceased" not in motivation.get("signals", []):
                motivation["signals"] = sorted(set(motivation.get("signals", []) + ["possible_deceased"]))
            motivation["details"] = (motivation.get("details") or []) + [{
                "source": "obituary", "obit_name": o["name"], "obit_date": o.get("date"),
                "obit_city": o.get("city"), "source_url": o.get("source_url"), "at": now}]
            motivation["updated_at"] = now
            await db.deal_finder_leads.update_one({"_id": lead["_id"]}, {"$set": {"motivation": motivation}})
            matches.append(match_info)
            new_matches.append(match_info)

    # guardar historial del escaneo (incluye la lista para verla en la UI)
    await db.admin_config.update_one({"type": "enrichment_config"}, {"$set": {
        "type": "enrichment_config", "last_obituary_scan": now,
        "last_obituary_count": len(obits), "last_obituary_matches": len(matches),
        "last_obituaries": [{k: o.get(k) for k in ("name", "age", "city", "date", "source_url")} for o in obits[:100]],
        "last_matches": [{"lead_id": m["lead_id"], "address": m.get("address"),
                          "owner_name": m.get("owner_name"),
                          "obit_name": (m.get("obituary") or {}).get("name"),
                          "obit_date": (m.get("obituary") or {}).get("date")} for m in matches[:100]],
    }}, upsert=True)

    return {"obituaries_found": len(obits), "matches": matches, "new_matches": new_matches,
            "fetch_errors": fetch_errors,
            "sources": sources,
            "sample": obits[:10]}


# ═══════════════════════════════════════════════════════════════
# CRON semanal de obituarios — lunes 9AM CT + email si hay nuevos
# ═══════════════════════════════════════════════════════════════

OBIT_ALERT_EMAIL = "yoandyross@gmail.com"
_ADMIN_URL = "https://www.rosshouserentals.com/admin/oportunidades"


async def _obit_should_run(db) -> bool:
    from zoneinfo import ZoneInfo
    cfg = await db.app_settings.find_one({"_id": "obituary_scan_cron"}) or {}
    if not cfg.get("enabled", True):
        return False
    now_ct = datetime.now(ZoneInfo("America/Chicago"))
    if now_ct.weekday() != int(cfg.get("weekday", 0)) or now_ct.hour != int(cfg.get("hour_ct", 9)):
        return False
    last = cfg.get("last_run_at")
    if last and isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last) < timedelta(days=6):
            return False
    return True


_REMINDER_HTML = (
    "<div style='margin-top:18px;padding:14px;background:#FEF3C7;border-radius:10px'>"
    "<b>📋 Rutina de 2 minutos — Probate oficial:</b><br>"
    "1. Abre el portal del condado: <a href='https://public.lgsonlinesolutions.com/ors.html'>"
    "public.lgsonlinesolutions.com/ors.html</a> (Guest Login)<br>"
    "2. Court Records → tipo <b>Probate</b> → filtra por la última semana → copia el índice<br>"
    "3. En tu panel: Oportunidades → 🛰️ Datos → <b>📥 Registros públicos</b> → pega y clic en Importar<br>"
    "El sistema cruza los casos con tus leads y marca ⚖️ <b>Probate CONFIRMADO</b> automáticamente.</div>")


async def _send_obit_alert(db, new_matches: list) -> bool:
    from rental.ai_brain_router import _send_email_branded
    cfg = await db.app_settings.find_one({"_id": "obituary_scan_cron"}) or {}
    to = cfg.get("alert_email") or OBIT_ALERT_EMAIL
    if not new_matches:
        html = ("<h2 style='color:#B91C1C'>🕊️ Escaneo semanal: sin herencias nuevas</h2>"
                "<p>El radar de obituarios corrió hoy y no encontró coincidencias nuevas con tus leads.</p>"
                f"<p><a href='{_ADMIN_URL}'>Ver el Radar</a></p>" + _REMINDER_HTML)
        plain = ("Escaneo semanal de obituarios: sin herencias nuevas.\n"
                 "Rutina de 2 min: copia el índice de Probate en public.lgsonlinesolutions.com/ors.html "
                 "y pégalo en Oportunidades → Datos → Registros públicos.")
        return await _send_email_branded(to, "🕊️ Radar semanal: sin herencias nuevas — recordatorio de probate",
                                         html, plain)
    rows = ""
    lines = []
    for m in new_matches[:20]:
        o = m.get("obituary") or {}
        rows += (f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'><b>{m.get('address','')}</b><br>"
                 f"<span style='color:#666'>Dueño: {m.get('owner_name','')}</span></td>"
                 f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>🕊️ {o.get('name','')}"
                 f"{(' · ' + o['date']) if o.get('date') else ''}{(' · ' + o['city']) if o.get('city') else ''}</td></tr>")
        lines.append(f"• {m.get('address','')} — dueño {m.get('owner_name','')} — obituario: {o.get('name','')}")
    html = (f"<h2 style='color:#B91C1C'>🕊️ {len(new_matches)} posible(s) herencia(s) detectada(s)</h2>"
            f"<p>El escaneo semanal de obituarios encontró coincidencias NUEVAS con dueños de tu radar. "
            f"Son candidatos a <b>probate</b>: los herederos suelen vender rápido.</p>"
            f"<table style='border-collapse:collapse;font-size:14px'>{rows}</table>"
            f"<p><a href='{_ADMIN_URL}' style='background:#B91C1C;color:#fff;padding:10px 18px;"
            f"border-radius:8px;text-decoration:none;font-weight:bold'>Ver en el Radar</a></p>" + _REMINDER_HTML)
    plain = f"{len(new_matches)} posibles herencias detectadas:\n" + "\n".join(lines) + f"\n{_ADMIN_URL}"
    return await _send_email_branded(to, f"🕊️ Radar: {len(new_matches)} posible(s) herencia(s) nueva(s) en Moore County",
                                     html, plain)


async def obituary_scan_loop():
    """Background: corre el escaneo de obituarios cada lunes 9AM CT (revisa cada 30 min)."""
    import asyncio
    logger.info("🕊️ Obituary scan cron started (lunes 9AM CT, checks cada 30 min)")
    while True:
        try:
            db = get_db()
            if db is not None and await _obit_should_run(db):
                logger.info("🕊️ Obituary scan cron: firing")
                result = await run_obituary_scan()
                new = result.get("new_matches") or []
                emailed = False
                try:
                    emailed = await _send_obit_alert(db, new)  # siempre envía (con o sin novedades + recordatorio probate)
                except Exception:
                    logger.exception("obit alert email failed")
                await db.app_settings.update_one({"_id": "obituary_scan_cron"}, {"$set": {
                    "last_run_at": datetime.now(timezone.utc),
                    "last_result": {"obituaries": result.get("obituaries_found", 0),
                                    "matches": len(result.get("matches") or []),
                                    "new_matches": len(new), "emailed": emailed}}}, upsert=True)
                logger.info(f"🕊️ Obituary scan done: {result.get('obituaries_found')} obits, "
                            f"{len(new)} nuevos, email={emailed}")
        except Exception:
            logger.exception("obituary scan loop error")
        await asyncio.sleep(1800)
