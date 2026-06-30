"""Ross House AI Brain — business assistant powered by Claude Sonnet 4.5.

Capabilities:
  - Multi-turn conversational chat with persistent history per admin session
  - Dynamic business snapshot injected as context every turn (read-only)
  - Daily briefing email generator
  - Marketing content generator for properties

Architecture:
  - Anthropic Claude Sonnet 4.5 via emergentintegrations + EMERGENT_LLM_KEY
  - MongoDB collections:
      ai_conversations: {_id, admin_user, title, created_at, updated_at, last_message}
      ai_messages: {_id, conversation_id, role, content, created_at}
  - Streaming SSE responses
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, AsyncGenerator
from uuid import uuid4

from dotenv import load_dotenv
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger(__name__)

from .shared import get_db, auth_admin

router = APIRouter(prefix="/admin/ai-brain", tags=["AI Brain"])

MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-4-5-20250929"
CONVERSATIONS_COLL = "ai_conversations"
MESSAGES_COLL = "ai_messages"
MAX_HISTORY = 30


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    conversation_id: Optional[str] = None


class MarketingRequest(BaseModel):
    property_id: str
    channels: List[str] = ["facebook_es", "facebook_en", "zillow", "waitlist_email"]
    tone: str = "warm_professional"


class BriefingRequest(BaseModel):
    recipient_email: Optional[str] = None


async def build_business_snapshot(db) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    soon_60_days = now + timedelta(days=60)

    snap: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "today": now.strftime("%Y-%m-%d"),
        "current_month": now.strftime("%B %Y"),
    }

    try:
        props = await db.properties.find({}).to_list(None)
        by_status: Dict[str, int] = {}
        total_rent_potential = 0
        rented_total = 0
        for p in props:
            st = p.get("status", "unknown")
            by_status[st] = by_status.get(st, 0) + 1
            rent = float(p.get("rent_amount") or 0)
            total_rent_potential += rent
            if st == "rented":
                rented_total += rent
        snap["properties"] = {
            "total": len(props),
            "by_status": by_status,
            "monthly_rent_potential": total_rent_potential,
            "monthly_rent_actual": rented_total,
            "occupancy_rate_pct": round((by_status.get("rented", 0) / len(props) * 100) if props else 0, 1),
            "list": [
                {"id": str(p.get("_id")), "address": p.get("address"), "city": p.get("city"),
                 "bedrooms": p.get("bedrooms"), "bathrooms": p.get("bathrooms"),
                 "rent": p.get("rent_amount"), "status": p.get("status")}
                for p in props
            ],
        }
    except Exception as e:
        logger.exception("snapshot:properties")
        snap["properties"] = {"error": str(e)}

    try:
        leases = await db.leases.find({"status": {"$in": ["active", "current"]}}).to_list(None)
        ending_soon = []
        for lease in leases:
            end_str = lease.get("end_date") or lease.get("lease_end_date")
            if end_str:
                try:
                    end_dt = datetime.fromisoformat(end_str) if isinstance(end_str, str) else end_str
                    if isinstance(end_dt, datetime):
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                        if now <= end_dt <= soon_60_days:
                            ending_soon.append({
                                "tenant": lease.get("tenant_name"),
                                "property": lease.get("property_address"),
                                "end_date": end_dt.strftime("%Y-%m-%d"),
                                "days_until_end": (end_dt - now).days,
                            })
                except Exception:
                    pass
        snap["leases"] = {"active_count": len(leases), "ending_in_60_days": ending_soon[:10]}
    except Exception as e:
        snap["leases"] = {"error": str(e)}

    try:
        payments_this_month = await db.rent_payments.find({"paid_at": {"$gte": month_start.isoformat()}}).to_list(None)
        total_collected = sum(float(p.get("amount") or 0) for p in payments_this_month)
        overdue = await db.tenant_invoices.find({"status": {"$in": ["pending", "overdue"]}, "due_date": {"$lt": now.isoformat()}}).to_list(10)
        snap["payments"] = {
            "collected_this_month": total_collected,
            "payment_count_this_month": len(payments_this_month),
            "overdue_invoices_count": len(overdue),
            "overdue_invoices_total": sum(float(i.get("amount") or 0) for i in overdue),
            "overdue_list": [{"tenant": i.get("tenant_name") or i.get("tenant_email"), "amount": i.get("amount"), "due_date": i.get("due_date")} for i in overdue[:10]],
        }
    except Exception as e:
        snap["payments"] = {"error": str(e)}

    try:
        leads = await db.tenant_leads.find({}).to_list(None)
        new_this_week = []
        for l in leads:
            ca = l.get("created_at")
            try:
                ts = datetime.fromisoformat(ca).replace(tzinfo=timezone.utc) if isinstance(ca, str) else ca
                if isinstance(ts, datetime) and ts >= week_ago:
                    new_this_week.append(l)
            except Exception:
                pass
        by_status = {}
        for l in leads:
            st = l.get("status", "new")
            by_status[st] = by_status.get(st, 0) + 1
        snap["leads"] = {
            "total": len(leads),
            "by_status": by_status,
            "new_this_week": len(new_this_week),
            "recent_5": [
                {"name": l.get("name"), "email": l.get("email"), "phone": l.get("phone"),
                 "bedrooms_wanted": l.get("bedrooms_wanted"), "max_budget": l.get("max_budget"),
                 "status": l.get("status")}
                for l in sorted(leads, key=lambda x: x.get("created_at") or "", reverse=True)[:5]
            ],
        }
    except Exception as e:
        snap["leads"] = {"error": str(e)}

    try:
        maintenance = await db.maintenance_requests.find({}).to_list(None)
        open_tix = [m for m in maintenance if m.get("status") in ("new", "open", "in_progress")]
        snap["maintenance"] = {
            "open_count": len(open_tix),
            "open_tickets": [
                {"id": str(m.get("_id")), "property": m.get("property_address"),
                 "title": m.get("title") or (m.get("description","")[:80]),
                 "status": m.get("status"), "priority": m.get("priority"),
                 "created_at": m.get("created_at")}
                for m in open_tix[:10]
            ],
        }
    except Exception as e:
        snap["maintenance"] = {"error": str(e)}

    try:
        provs = await db.service_providers.find({"is_active": True}).to_list(None)
        by_cat: Dict[str, int] = {}
        for p in provs:
            for c in (p.get("categories") or []):
                by_cat[c] = by_cat.get(c, 0) + 1
        snap["service_providers"] = {"active_count": len(provs), "by_category": by_cat}
    except Exception as e:
        snap["service_providers"] = {"error": str(e)}

    return snap


SYSTEM_PROMPT_BASE = """Eres **Ross House AI Brain**, el cerebro inteligente del negocio de Yoandy Ross (Ross House Rentals LLC) en Dumas, Texas.

# Tu rol
Eres un asistente ejecutivo experto en bienes raíces residenciales, administración de propiedades, marketing inmobiliario y análisis financiero.

# Tu personalidad
- Profesional pero cercano (tuteo en español, casual en inglés)
- Directo y accionable — no rellenos, ve al grano
- Proactivo: si ves un problema u oportunidad, menciónalo aunque no te pregunten
- Honesto: si no tienes data, dilo. No inventes números.

# Cómo respondes
- Empieza con la respuesta directa, después agrega detalle/contexto
- Usa números reales del snapshot del negocio que recibes en cada turno
- Si te piden "draft" / "redacta" / "genera", entrega el texto listo para copiar
- Usa **markdown** (negritas, listas, tablas) para legibilidad
- Idioma: responde en el idioma del último mensaje del usuario (ES o EN)

# Lo que NO debes hacer
- NO ejecutas acciones que modifican datos. Solo redactas drafts.
- NO inventes datos que no estén en el snapshot
- NO des asesoría legal/financiera vinculante — sugiere consultar profesional cuando aplique

# Contexto del negocio
- **Empresa:** Ross House Rentals LLC (Dumas, TX 79029)
- **Modelo:** Renta residencial (SFRs hoy, mirando multifamily como Jasmine Apartments)
- **Mercado:** Texas Panhandle (Dumas, Amarillo, Canyon)
- **Sitio:** https://www.rosshouserentals.com
"""


def build_system_prompt(snapshot: Dict[str, Any]) -> str:
    snap_json = json.dumps(snapshot, indent=2, default=str, ensure_ascii=False)
    return f"""{SYSTEM_PROMPT_BASE}

# Snapshot del negocio en TIEMPO REAL
```json
{snap_json}
```
"""


@router.post("/chat")
async def chat_stream(body: ChatRequest, db=Depends(get_db), admin=Depends(auth_admin)):
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(500, "EMERGENT_LLM_KEY not configured")

    admin_id = str(admin.get("_id") or admin.get("email", "default"))

    if body.conversation_id:
        conv = await db[CONVERSATIONS_COLL].find_one({"_id": body.conversation_id})
        if not conv:
            raise HTTPException(404, "Conversation not found")
    else:
        new_id = str(uuid4())
        title = body.message[:60] + ("…" if len(body.message) > 60 else "")
        conv = {
            "_id": new_id, "admin_id": admin_id, "title": title,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_message": body.message[:200], "message_count": 0,
        }
        await db[CONVERSATIONS_COLL].insert_one(conv)

    conversation_id = conv["_id"]

    user_msg_doc = {
        "_id": str(uuid4()), "conversation_id": conversation_id,
        "role": "user", "content": body.message,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db[MESSAGES_COLL].insert_one(user_msg_doc)

    history = await db[MESSAGES_COLL].find({"conversation_id": conversation_id}).sort("created_at", 1).to_list(MAX_HISTORY)
    snapshot = await build_business_snapshot(db)
    system_prompt = build_system_prompt(snapshot)

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conversation_id})}\n\n"
        full_response = ""
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
            chat = LlmChat(
                api_key=api_key, session_id=conversation_id,
                system_message=system_prompt,
            ).with_model(MODEL_PROVIDER, MODEL_NAME)

            prior_messages = [m for m in history if m["_id"] != user_msg_doc["_id"]]
            if len(prior_messages) > 0:
                history_text = "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in prior_messages)
                composed_msg = f"# Conversación previa:\n{history_text}\n\n# Nuevo mensaje del usuario:\n{body.message}"
            else:
                composed_msg = body.message

            async for event in chat.stream_message(UserMessage(text=composed_msg)):
                if isinstance(event, TextDelta):
                    full_response += event.content
                    yield f"data: {json.dumps({'type': 'delta', 'content': event.content})}\n\n"
                elif isinstance(event, StreamDone):
                    break
        except Exception as e:
            logger.exception("ai_brain chat")
            err = f"Error: {str(e)[:200]}"
            yield f"data: {json.dumps({'type': 'error', 'message': err})}\n\n"
            full_response = err

        await db[MESSAGES_COLL].insert_one({
            "_id": str(uuid4()), "conversation_id": conversation_id,
            "role": "assistant", "content": full_response,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await db[CONVERSATIONS_COLL].update_one(
            {"_id": conversation_id},
            {"$set": {"updated_at": datetime.now(timezone.utc).isoformat(),
                      "last_message": full_response[:200]},
             "$inc": {"message_count": 2}},
        )
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/conversations")
async def list_conversations(db=Depends(get_db), admin=Depends(auth_admin)):
    admin_id = str(admin.get("_id") or admin.get("email", "default"))
    convs = await db[CONVERSATIONS_COLL].find({"admin_id": admin_id}).sort("updated_at", -1).limit(50).to_list(None)
    return {"conversations": convs}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    conv = await db[CONVERSATIONS_COLL].find_one({"_id": conv_id})
    if not conv:
        raise HTTPException(404, "Not found")
    messages = await db[MESSAGES_COLL].find({"conversation_id": conv_id}).sort("created_at", 1).to_list(None)
    return {"conversation": conv, "messages": messages}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    await db[CONVERSATIONS_COLL].delete_one({"_id": conv_id})
    await db[MESSAGES_COLL].delete_many({"conversation_id": conv_id})
    return {"ok": True}


@router.post("/marketing/generate")
async def generate_marketing(body: MarketingRequest, db=Depends(get_db), admin=Depends(auth_admin)):
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(500, "EMERGENT_LLM_KEY not configured")

    try:
        prop = await db.properties.find_one({"_id": ObjectId(body.property_id)})
    except Exception:
        prop = await db.properties.find_one({"_id": body.property_id})
    if not prop:
        raise HTTPException(404, "Property not found")

    prop_summary = {
        "address": prop.get("address"), "city": prop.get("city"), "state": prop.get("state"),
        "bedrooms": prop.get("bedrooms"), "bathrooms": prop.get("bathrooms"),
        "square_feet": prop.get("square_feet"), "rent_amount": prop.get("rent_amount"),
        "description": prop.get("description"), "features": prop.get("features"),
        "section8_accepted": prop.get("section8_accepted"), "property_type": prop.get("property_type"),
    }
    prop_url = f"https://www.rosshouserentals.com/propiedades/{prop.get('_id')}"

    channels_desc = {
        "facebook_es": "Post para Facebook en ESPAÑOL — emojis amigables, 2-3 párrafos, hashtags al final, CTA a aplicar",
        "facebook_en": "Facebook post in ENGLISH — friendly emojis, 2-3 paragraphs, hashtags, CTA to apply",
        "zillow": "Descripción para Zillow — profesional, bullets de amenities, sin emojis",
        "waitlist_email": "Email a inquilinos en waitlist — saludo personalizado con {{name}}, descripción atractiva, CTA",
        "instagram": "Caption para Instagram — corto, visual, emojis + hashtags",
        "craigslist": "Listado Craigslist — texto simple, no markdown",
    }
    requested = [c for c in body.channels if c in channels_desc]
    requested_desc = "\n".join(f"- **{c}**: {channels_desc[c]}" for c in requested)
    tone_guide = {
        "warm_professional": "cálido, profesional, accesible",
        "casual": "casual, amigable, conversacional",
        "luxury": "lujo, exclusivo, sofisticado",
        "family_oriented": "familiar, hogareño",
    }.get(body.tone, "cálido y profesional")

    system_msg = f"""Eres un copywriter inmobiliario senior para Ross House Rentals (Dumas, TX).
**Tono:** {tone_guide}
**URL:** {prop_url}
**Tel:** (806) 934-2018
**Web:** https://www.rosshouserentals.com

Responde EXCLUSIVAMENTE en JSON válido con esta estructura:
{{"channel_key_1": "contenido listo para copiar", "channel_key_2": "..."}}
Las keys deben coincidir EXACTAMENTE con las solicitadas. No agregues comentarios fuera del JSON."""

    user_msg = f"""Propiedad:
```json
{json.dumps(prop_summary, indent=2, default=str, ensure_ascii=False)}
```

Genera contenido para estos canales:
{requested_desc}"""

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(api_key=api_key, session_id=f"marketing_{uuid4()}", system_message=system_msg).with_model(MODEL_PROVIDER, MODEL_NAME)
        resp = await chat.send_message(UserMessage(text=user_msg))
        text = resp.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        text = text.strip()
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = {"raw": resp}
        return {"property_id": body.property_id, "url": prop_url, "content": parsed}
    except Exception as e:
        logger.exception("marketing.generate")
        raise HTTPException(500, f"Generation failed: {e}")


@router.post("/briefing/send")
async def send_daily_briefing(body: BriefingRequest = Body(default=BriefingRequest()), db=Depends(get_db), admin=Depends(auth_admin)):
    return await _generate_and_send_briefing(db, recipient=body.recipient_email)


@router.post("/briefing/run-cron")
async def briefing_cron_endpoint(db=Depends(get_db), secret: str = Body(..., embed=True)):
    expected = os.environ.get("AI_BRIEFING_CRON_SECRET")
    if not expected or secret != expected:
        raise HTTPException(401, "Invalid cron secret")
    return await _generate_and_send_briefing(db, recipient=None)


async def _generate_and_send_briefing(db, recipient: Optional[str] = None) -> Dict[str, Any]:
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(500, "EMERGENT_LLM_KEY not configured")

    snapshot = await build_business_snapshot(db)
    if not recipient:
        settings = await db.lead_settings.find_one({}) or {}
        recipient = settings.get("notify_admin_email") or os.environ.get("ADMIN_BRIEFING_EMAIL", "yoandyross@gmail.com")

    system_msg = """Eres el AI Brain de Ross House Rentals. Generas un briefing ejecutivo DIARIO en ESPAÑOL para Yoandy Ross.

Responde en JSON válido EXACTAMENTE así:
{
  "headline": "frase corta (max 90 caracteres)",
  "highlight": "1 dato/hecho más importante para hoy",
  "kpis": [{"label": "...", "value": "...", "trend": "up|down|flat|info"}],
  "alerts": ["alerta 1"],
  "action_items": ["acción 1"],
  "opportunities": ["oportunidad 1"],
  "insight": "1 frase de insight estratégico"
}

Reglas: usa SOLO datos del snapshot. KPIs: 3-5 métricas clave. Tono directo, ejecutivo, accionable."""

    user_msg = f"""Snapshot:
```json
{json.dumps(snapshot, indent=2, default=str, ensure_ascii=False)}
```

Genera el briefing diario para hoy ({snapshot.get('today')})."""

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(api_key=api_key, session_id=f"briefing_{uuid4()}", system_message=system_msg).with_model(MODEL_PROVIDER, MODEL_NAME)
        resp = await chat.send_message(UserMessage(text=user_msg))

        text = resp.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        text = text.strip()
        try:
            briefing = json.loads(text)
        except Exception:
            briefing = {"headline": "Briefing diario", "highlight": resp[:200], "kpis": [], "alerts": [], "action_items": [], "opportunities": [], "insight": ""}

        html = _render_briefing_email(briefing, snapshot)
        subject = f"☕ Briefing diario · {snapshot.get('today')} · {briefing.get('headline','Resumen')}"
        sent = await _send_email_branded(recipient, subject, html, briefing.get("headline", ""))
        return {"ok": sent, "recipient": recipient, "briefing": briefing, "snapshot_generated_at": snapshot.get("generated_at")}
    except Exception as e:
        logger.exception("briefing")
        raise HTTPException(500, f"Briefing failed: {e}")


def _render_briefing_email(briefing: Dict[str, Any], snap: Dict[str, Any]) -> str:
    kpis_html = ""
    for k in briefing.get("kpis", [])[:6]:
        trend_emoji = {"up": "📈", "down": "📉", "flat": "→", "info": "ℹ️"}.get(k.get("trend"), "•")
        kpis_html += f"""
        <td style="padding:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;text-align:center;vertical-align:top;">
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:6px;">{k.get('label','')}</div>
          <div style="font-size:20px;font-weight:800;color:#0f172a;">{k.get('value','')}</div>
          <div style="font-size:14px;margin-top:4px;">{trend_emoji}</div>
        </td>"""
    kpis_table = f"<table cellpadding=0 cellspacing=8 border=0 width=100%><tr>{kpis_html}</tr></table>" if kpis_html else ""

    def _bullet_block(items: List[str], color: str, emoji: str, label: str) -> str:
        if not items:
            return ""
        bullets = "".join(f'<li style="margin-bottom:8px;line-height:1.55;">{i}</li>' for i in items[:6])
        return f"""
        <div style="margin-top:18px;padding:16px 18px;background:#ffffff;border:1px solid {color};border-radius:10px;">
          <div style="font-weight:700;color:{color};font-size:13px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">{emoji} {label}</div>
          <ul style="margin:0;padding-left:20px;font-size:14px;color:#334155;">{bullets}</ul>
        </div>"""

    alerts_block = _bullet_block(briefing.get("alerts", []), "#dc2626", "⚠️", "Alertas")
    actions_block = _bullet_block(briefing.get("action_items", []), "#1e40af", "✅", "Acciones para hoy")
    opps_block = _bullet_block(briefing.get("opportunities", []), "#059669", "💡", "Oportunidades")
    insight_block = ""
    if briefing.get("insight"):
        insight_block = f"""
        <div style="margin-top:22px;padding:16px 18px;background:linear-gradient(135deg,#fef3c7,#fde68a);border-radius:12px;border-left:4px solid #d97706;">
          <div style="font-size:12px;font-weight:700;color:#78350f;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">🧠 Insight del AI Brain</div>
          <div style="font-size:14px;color:#451a03;line-height:1.6;">{briefing.get("insight")}</div>
        </div>"""

    content_html = f"""
      <p style="margin:0 0 8px 0;font-size:16px;color:#334155;line-height:1.5;">
        <strong style="color:#0f172a;">{briefing.get('highlight','')}</strong>
      </p>
      {kpis_table}{alerts_block}{actions_block}{opps_block}{insight_block}"""

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Briefing diario</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#0f172a;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9;padding:24px 12px;">
  <tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
      <tr><td bgcolor="#0d1a2e" style="background:#0d1a2e;padding:26px 32px;">
        <div style="display:inline-block;background:#3b2a06;border:1px solid #b45309;padding:5px 12px;border-radius:999px;">
          <span style="color:#fbbf24;font-size:11px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;">🧠 AI Brain · Briefing · {snap.get('today','')}</span>
        </div>
        <h1 style="margin:10px 0 4px 0;color:#ffffff;font-size:22px;font-weight:800;">{briefing.get('headline','Briefing diario')}</h1>
      </td></tr>
      <tr><td style="padding:28px 32px;background:#ffffff;">{content_html}</td></tr>
      <tr><td bgcolor="#f8fafc" style="background:#f8fafc;padding:22px 32px;border-top:1px solid #e2e8f0;">
        <div style="font-size:12px;color:#64748b;line-height:1.6;text-align:center;">
          <strong style="color:#0f172a;">Ross House Rentals</strong> · Dumas, TX · (806) 934-2018<br>
          <a href="https://www.rosshouserentals.com/admin/ai-brain" style="color:#d97706;text-decoration:none;">Abrir AI Brain →</a>
        </div>
      </td></tr>
    </table>
  </td></tr>
</table></body></html>"""


async def _send_email_branded(to_email: str, subject: str, html: str, plain_fallback: str) -> bool:
    try:
        api_key = os.environ.get("SENDGRID_API_KEY")
        from_email = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
        if not api_key:
            return False
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        msg = Mail(
            from_email=(from_email, "Ross House AI Brain"),
            to_emails=to_email, subject=subject,
            plain_text_content=plain_fallback, html_content=html,
        )
        resp = SendGridAPIClient(api_key).send(msg)
        return 200 <= resp.status_code < 300
    except Exception:
        logger.exception("send_email_branded")
        return False
