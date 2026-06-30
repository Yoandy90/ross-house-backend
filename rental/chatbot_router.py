"""
Public Chatbot Router — Phase 2 AI Brain (Public Chat Widget)
==============================================================
Public-facing chatbot for rosshouserentals.com visitors. Captures leads
automatically using Claude Sonnet 4.5.

Endpoints (no auth):
  POST /api/public/chatbot/chat   { session_id?, message, lang? } -> SSE stream
  POST /api/public/chatbot/session             -> { session_id }
  GET  /api/public/chatbot/sessions/{id}       -> conversation history

Admin endpoints (auth_admin):
  GET  /api/admin/chatbot/sessions             -> list all sessions
  GET  /api/admin/chatbot/sessions/{id}        -> full transcript

Collection: public_chatbot_sessions
  _id (session id), created_at, updated_at, ip, user_agent,
  lang, lead_id (when converted), captured_contact (dict),
  messages: [{role, content, created_at}]
"""
from __future__ import annotations

import os
import json
import re
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-4-5-20250929"
COLL = "public_chatbot_sessions"
MAX_HISTORY = 24


class ChatMsg(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    lang: Optional[str] = Field(default="es", pattern="^(es|en)$")


# ── Context helpers ──────────────────────────────────────────────────────────

async def _public_context(db) -> Dict[str, Any]:
    """Compact snapshot for chatbot: only data the public should see."""
    ctx: Dict[str, Any] = {"company": "Ross House Rentals LLC", "city": "Dumas, TX 79029",
                            "phone": "(806) 934-2018", "email": "info@rosshouserentals.com"}
    try:
        props = await db.properties.find({"status": {"$in": ["active", "available", "vacant"]}}).to_list(50)
        ctx["available_properties"] = [
            {
                "id": str(p.get("_id")),
                "address": p.get("address"),
                "city": p.get("city") or "Dumas",
                "bedrooms": p.get("bedrooms"),
                "bathrooms": p.get("bathrooms"),
                "rent": p.get("rent_amount") or p.get("monthly_rent"),
                "pets_allowed": p.get("pets_allowed"),
                "url": f"https://www.rosshouserentals.com/propiedades/{p.get('_id')}",
            }
            for p in props if p.get("status") in ("active", "available", "vacant")
        ]
    except Exception:
        ctx["available_properties"] = []
    return ctx


SYSTEM_PROMPT_CHATBOT = """Eres **Rossy**, el asistente virtual oficial de Ross House Rentals (Dumas, TX). Atiendes a visitantes del sitio web 24/7.

# Tu personalidad
- Cálida, profesional, eficiente
- Bilingüe (responde en el idioma del usuario — ES o EN)
- Concisa: respuestas de 2-4 oraciones, máx 1 párrafo
- Honesta: si no sabes algo, dirige a (806) 934-2018 o info@rosshouserentals.com

# Tu objetivo principal
1. Responder preguntas sobre propiedades disponibles, requisitos, proceso
2. **CAPTURAR LEADS**: si la persona muestra interés en rentar, pídele de forma natural:
   - Nombre completo
   - Email
   - Teléfono (10 dígitos)
   - Habitaciones deseadas (1-6)
   - Presupuesto máximo mensual
   - Fecha de mudanza (opcional)
3. Cuando tengas los datos mínimos (nombre + email + teléfono + bedrooms + budget), emite UNA SOLA acción de captura.

# Cómo capturar un lead (PROTOCOLO ESTRICTO)
Cuando tengas datos suficientes, incluye AL FINAL de tu mensaje un bloque EXACTAMENTE así:

<CAPTURE_LEAD>
{
  "name": "Nombre completo",
  "email": "email@ejemplo.com",
  "phone": "8069341234",
  "bedrooms_wanted": 3,
  "max_budget": 1500,
  "move_in_date": "2026-08-01",
  "household_size": 4,
  "has_pets": false,
  "language_pref": "es",
  "notes": "Cualquier detalle relevante mencionado en la conversación"
}
</CAPTURE_LEAD>

Reglas del bloque CAPTURE_LEAD:
- SOLO emitir cuando tengas los 5 campos obligatorios (name, email, phone, bedrooms_wanted, max_budget)
- email debe verse válido (contiene @ y dominio)
- phone: solo dígitos, mínimo 10
- bedrooms_wanted: entero 1-6
- max_budget: número (sin $ ni comas)
- move_in_date: formato YYYY-MM-DD o null
- household_size: entero ≥ 1 (default 1)
- has_pets: true/false
- language_pref: "es" o "en"
- notes: contexto adicional útil
- NO incluyas el bloque más de una vez por conversación.
- Después del bloque, sigue conversando normalmente (confirma que los registraste).

# Restricciones de seguridad
- NO inventes propiedades ni precios
- NO prometas aprobación: di "el equipo revisará"
- NO compartas datos de otros clientes
- NO menciones que eres un "modelo de IA" — eres Rossy
- Si te preguntan por mantenimiento (inquilinos actuales), pídeles que entren al portal de inquilinos.

# Datos del negocio en TIEMPO REAL
{context_json}
"""


async def _get_or_create_session(db, session_id: Optional[str], ip: str, ua: str, lang: str) -> Dict[str, Any]:
    if session_id:
        existing = await db[COLL].find_one({"_id": session_id})
        if existing:
            return existing
    new_id = session_id or str(uuid4())
    doc = {
        "_id": new_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ip": ip,
        "user_agent": ua[:500],
        "lang": lang,
        "messages": [],
        "lead_id": None,
        "captured_contact": None,
    }
    await db[COLL].insert_one(doc)
    return doc


CAPTURE_RE = re.compile(r"<CAPTURE_LEAD>\s*(\{.*?\})\s*</CAPTURE_LEAD>", re.DOTALL)


async def _maybe_capture_lead(db, session: Dict[str, Any], full_text: str, request_ip: str, ua: str) -> Optional[str]:
    """If the AI emitted a <CAPTURE_LEAD> block, parse it, create/update the
    tenant lead, link it to the session, and return the new text with the block stripped."""
    m = CAPTURE_RE.search(full_text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
    except Exception as e:
        logger.warning(f"[chatbot] CAPTURE_LEAD parse failed: {e}")
        return CAPTURE_RE.sub("", full_text).strip()

    # Basic validation
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = re.sub(r"\D", "", (data.get("phone") or ""))
    bedrooms = int(data.get("bedrooms_wanted") or 0)
    budget = float(data.get("max_budget") or 0)
    if not name or "@" not in email or len(phone) < 10 or bedrooms < 1 or budget <= 0:
        return CAPTURE_RE.sub("", full_text).strip()

    now = datetime.utcnow()
    existing = await db.tenant_leads.find_one({"email": email})
    lead_payload: Dict[str, Any] = {
        "name": name[:100],
        "email": email,
        "phone": phone,
        "bedrooms_wanted": min(max(bedrooms, 1), 10),
        "max_budget": min(max(budget, 0.0), 20000.0),
        "move_in_date": data.get("move_in_date"),
        "household_size": int(data.get("household_size") or 1),
        "has_pets": bool(data.get("has_pets") or False),
        "pet_details": data.get("pet_details"),
        "language_pref": (data.get("language_pref") or session.get("lang") or "es")[:2],
        "notes": (data.get("notes") or "")[:1000],
        "source": "public_chatbot",
        "updated_at": now,
        "ip_address": request_ip,
        "user_agent": ua[:500],
    }
    if existing:
        lead_id = existing["_id"]
        await db.tenant_leads.update_one({"_id": lead_id}, {"$set": lead_payload})
        is_new = False
    else:
        lead_id = str(uuid4())
        await db.tenant_leads.insert_one({
            "_id": lead_id, "created_at": now, "status": "new", "priority": "medium",
            "admin_notes": "", "notifications_sent": [], "matched_properties": [],
            **lead_payload,
        })
        is_new = True

    # Link to session + try to score + welcome (fire-and-forget)
    await db[COLL].update_one(
        {"_id": session["_id"]},
        {"$set": {"lead_id": lead_id, "captured_contact": {
            "name": name, "email": email, "phone": phone,
            "captured_at": now.isoformat(),
        }, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Async-style notifications: import inside to avoid circular at module load
    try:
        from .lead_scoring import score_and_persist
        await score_and_persist(db, lead_id)
    except Exception as e:
        logger.warning(f"[chatbot] auto-scoring failed: {e}")

    try:
        from .tenant_leads_router import _get_settings, _send_welcome, _notify_admin_new_lead
        settings = await _get_settings(db)
        fresh = await db.tenant_leads.find_one({"_id": lead_id})
        if fresh:
            await _send_welcome(fresh, settings)
            if is_new:
                await _notify_admin_new_lead(fresh, settings)
    except Exception as e:
        logger.warning(f"[chatbot] welcome notify failed: {e}")

    return CAPTURE_RE.sub("", full_text).strip()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/public/chatbot/session")
async def start_session(request: Request):
    db = get_db()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    lang = "es"
    doc = await _get_or_create_session(db, None, ip, ua, lang)
    return {"session_id": doc["_id"]}


@router.post("/public/chatbot/chat")
async def chatbot_chat(body: ChatMsg, request: Request):
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(500, "Chatbot temporarily unavailable")
    db = get_db()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    lang = body.lang or "es"

    session = await _get_or_create_session(db, body.session_id, ip, ua, lang)
    sid = session["_id"]

    # Append user msg
    now_iso = datetime.now(timezone.utc).isoformat()
    await db[COLL].update_one(
        {"_id": sid},
        {"$push": {"messages": {"role": "user", "content": body.message, "created_at": now_iso}},
         "$set": {"updated_at": now_iso, "lang": lang}},
    )

    # Re-fetch trimmed history
    session = await db[COLL].find_one({"_id": sid})
    history = (session.get("messages") or [])[-MAX_HISTORY:]
    ctx = await _public_context(db)
    system = SYSTEM_PROMPT_CHATBOT.replace(
        "{context_json}",
        json.dumps(ctx, default=str, ensure_ascii=False, indent=2),
    )

    async def event_gen() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'meta', 'session_id': sid})}\n\n"
        full = ""
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
            chat = LlmChat(
                api_key=api_key, session_id=sid, system_message=system
            ).with_model(MODEL_PROVIDER, MODEL_NAME)

            prior = [m for m in history if m.get("role") in ("user", "assistant")][:-1]
            if prior:
                history_text = "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in prior)
                composed = f"# Conversación previa:\n{history_text}\n\n# Nuevo mensaje:\n{body.message}"
            else:
                composed = body.message

            async for event in chat.stream_message(UserMessage(text=composed)):
                if isinstance(event, TextDelta):
                    # Strip CAPTURE_LEAD block tokens from the stream so they
                    # don't flicker in the UI. We capture them on the full text.
                    full += event.content
                    safe_delta = event.content
                    # Hide partial < / <C / <CAPT ... etc. by emitting only when
                    # we're confident we're outside a tag. Simple heuristic:
                    # buffer is shown unless it currently looks like we're
                    # inside an open <CAPTURE_LEAD>...</CAPTURE_LEAD>.
                    # For simplicity (low risk of partial bleed since blocks
                    # are typically emitted in one chunk), filter on the buffer.
                    if "<CAPTURE_LEAD>" in full and "</CAPTURE_LEAD>" not in full:
                        continue  # hold until block closes
                    # If block was just closed, re-emit only the non-block portion
                    if "</CAPTURE_LEAD>" in full:
                        cleaned_so_far = CAPTURE_RE.sub("", full).strip()
                        # Send a "replace" event to reset what the client shows
                        yield f"data: {json.dumps({'type': 'replace', 'content': cleaned_so_far})}\n\n"
                        continue
                    yield f"data: {json.dumps({'type': 'delta', 'content': safe_delta})}\n\n"
                elif isinstance(event, StreamDone):
                    break
        except Exception:
            logger.exception("chatbot.chat")
            err = "Lo siento, ocurrió un error. Por favor llama al (806) 934-2018."
            full = err
            yield f"data: {json.dumps({'type': 'delta', 'content': err})}\n\n"

        # Persist assistant message (capture-stripped)
        cleaned = await _maybe_capture_lead(db, session, full, ip, ua) or full
        cleaned = CAPTURE_RE.sub("", cleaned).strip()
        await db[COLL].update_one(
            {"_id": sid},
            {"$push": {"messages": {
                "role": "assistant", "content": cleaned,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_had_capture": bool(CAPTURE_RE.search(full)),
            }}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        # Notify client if a lead was captured
        sess_after = await db[COLL].find_one({"_id": sid})
        if sess_after and sess_after.get("lead_id"):
            yield f"data: {json.dumps({'type': 'lead_captured', 'lead_id': sess_after['lead_id']})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/public/chatbot/sessions/{sid}")
async def get_session(sid: str):
    db = get_db()
    doc = await db[COLL].find_one({"_id": sid})
    if not doc:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": doc["_id"],
        "messages": doc.get("messages", []),
        "lead_captured": bool(doc.get("lead_id")),
        "lang": doc.get("lang", "es"),
    }


# ── Admin endpoints (live under same router) ─────────────────────────────────

@router.get("/admin/chatbot/sessions")
async def admin_list_sessions(request: Request):
    await auth_admin(request)
    db = get_db()
    cursor = db[COLL].find({}).sort("updated_at", -1).limit(200)
    sessions = []
    async for s in cursor:
        sessions.append({
            "session_id": s["_id"],
            "created_at": s.get("created_at"),
            "updated_at": s.get("updated_at"),
            "message_count": len(s.get("messages", [])),
            "lead_id": s.get("lead_id"),
            "captured_contact": s.get("captured_contact"),
            "lang": s.get("lang"),
            "first_message": (s.get("messages") or [{}])[0].get("content", "")[:100],
        })
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/admin/chatbot/sessions/{sid}")
async def admin_get_session(request: Request, sid: str):
    await auth_admin(request)
    db = get_db()
    doc = await db[COLL].find_one({"_id": sid})
    if not doc:
        raise HTTPException(404, "Not found")
    return doc
