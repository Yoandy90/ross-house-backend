"""Newsletter PRO — campañas avanzadas con AI, programación, tracking por
destinatario y CRUD completo.

Admin endpoints (prefijo /api):
  POST   /admin/newsletter/pro/campaigns              → crear (draft | now | schedule | recurring)
  GET    /admin/newsletter/pro/campaigns              → historial con stats agregadas
  GET    /admin/newsletter/pro/campaigns/{id}         → detalle + quién abrió/cuándo, quién no
  PUT    /admin/newsletter/pro/campaigns/{id}         → editar (draft/scheduled/recurring)
  DELETE /admin/newsletter/pro/campaigns/{id}         → cancelar/eliminar
  POST   /admin/newsletter/pro/campaigns/{id}/send    → enviar ahora
  POST   /admin/newsletter/pro/campaigns/{id}/duplicate
  POST   /admin/newsletter/ai/topics                  → AI: lista de temas sugeridos
  POST   /admin/newsletter/ai/generate                → AI: contenido bilingüe (ES+EN) de un tema
  POST   /admin/newsletter/ai/year-plan               → AI: plan anual (12 campañas programadas)

Envío bilingüe: cada destinatario recibe DOS emails (uno en español y uno en
inglés) cuando la campaña tiene contenido EN. Cada email lleva custom_args
(campaign_id, lang) para que el webhook de SendGrid actualice el tracking
por destinatario en `newsletter_recipients`.

Scheduler: `newsletter_scheduler_loop()` (arrancado en server.py) revisa cada
60s las campañas programadas/recurrentes. Las recurrentes generan una campaña
"hija" por cada corrida para conservar historial y stats por envío.
"""
import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException

from .shared import get_db, auth_admin, serialize
from .newsletter_router import _sendgrid, _campaign_html

router = APIRouter()
logger = logging.getLogger(__name__)

FREQ_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
EDITABLE_STATUSES = ("draft", "scheduled", "recurring")


# ════════════════════════════ helpers ════════════════════════════

async def _send_one_tracked(sg_key: str, from_email: str, to_email: str,
                            subject: str, html: str, campaign_id: str, lang: str) -> bool:
    """Envío individual con custom_args para tracking por campaña/idioma."""
    def _send_sync() -> bool:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content, Category, CustomArg
        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        mail = Mail(
            from_email=Email(from_email, "Ross House Rentals"),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html),
        )
        mail.add_category(Category("rhr-newsletter"))
        mail.add_custom_arg(CustomArg("campaign_id", campaign_id))
        mail.add_custom_arg(CustomArg("lang", lang))
        resp = sg.client.mail.send.post(request_body=mail.get())
        return resp.status_code in (200, 201, 202)

    try:
        return await asyncio.to_thread(_send_sync)
    except Exception as e:
        logger.warning(f"[newsletter-pro] send to {to_email} failed: {e}")
        return False


async def _build_recipients(audience: str) -> dict:
    """{email: {email, unsub_token}} deduplicado."""
    db = get_db()
    recipients: dict = {}
    if audience in ("newsletter", "both"):
        async for s in db.newsletter_subscribers.find({"unsubscribed": {"$ne": True}}):
            recipients[s["email"]] = {"email": s["email"], "unsub_token": s.get("unsubscribe_token")}
    if audience in ("leads", "both"):
        async for l in db.tenant_leads.find({}, {"email": 1}):
            em = (l.get("email") or "").strip().lower()
            if em and em not in recipients:
                recipients[em] = {"email": em, "unsub_token": None}
    return recipients


async def run_campaign_pro(campaign_id: str):
    """Envía una campaña (bilingüe si tiene contenido EN) y registra un doc
    por destinatario en newsletter_recipients."""
    db = get_db()
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    if not camp:
        return
    sg_key, from_email = await _sendgrid()
    if not sg_key:
        await db.newsletter_campaigns.update_one(
            {"_id": campaign_id}, {"$set": {"status": "failed", "error": "SendGrid no configurado"}})
        return

    await db.newsletter_campaigns.update_one(
        {"_id": campaign_id}, {"$set": {"status": "sending", "started_at": datetime.utcnow()}})

    recipients = await _build_recipients(camp.get("audience", "newsletter"))
    subject_es = camp.get("subject") or ""
    message_es = camp.get("message") or ""
    subject_en = (camp.get("subject_en") or "").strip()
    message_en = (camp.get("message_en") or "").strip()
    bilingual = bool(subject_en and message_en)

    sent = failed = 0
    for r in recipients.values():
        unsub_url = (
            f"https://www.rosshouserentals.com/api/public/newsletter/unsubscribe?token={r['unsub_token']}"
            if r.get("unsub_token") else None
        )
        ok_es = await _send_one_tracked(
            sg_key, from_email, r["email"], subject_es,
            _campaign_html(subject_es, message_es, unsub_url), campaign_id, "es")
        ok_en = True
        if bilingual:
            ok_en = await _send_one_tracked(
                sg_key, from_email, r["email"], subject_en,
                _campaign_html(subject_en, message_en, unsub_url), campaign_id, "en")
        ok = ok_es or ok_en
        sent += 1 if ok else 0
        failed += 0 if ok else 1
        await db.newsletter_recipients.update_one(
            {"campaign_id": campaign_id, "email": r["email"]},
            {"$set": {
                "campaign_id": campaign_id,
                "email": r["email"],
                "status": "sent" if ok else "failed",
                "sent_at": datetime.utcnow(),
                "bilingual": bilingual,
            },
             "$setOnInsert": {"opened": False, "clicked": False, "delivered": False, "bounced": False, "opens": 0}},
            upsert=True,
        )

    await db.newsletter_campaigns.update_one(
        {"_id": campaign_id},
        {"$set": {"status": "sent", "sent": sent, "failed": failed,
                  "total_recipients": len(recipients), "completed_at": datetime.utcnow()}})
    logger.info(f"📣 [PRO] Campaign {campaign_id}: sent={sent} failed={failed} bilingual={bilingual}")


async def _campaign_stats(campaign_ids: list) -> dict:
    """Stats agregadas desde newsletter_recipients: {cid: {delivered, opened, clicked, bounced}}."""
    if not campaign_ids:
        return {}
    db = get_db()
    pipeline = [
        {"$match": {"campaign_id": {"$in": campaign_ids}}},
        {"$group": {
            "_id": "$campaign_id",
            "delivered": {"$sum": {"$cond": ["$delivered", 1, 0]}},
            "opened": {"$sum": {"$cond": ["$opened", 1, 0]}},
            "clicked": {"$sum": {"$cond": ["$clicked", 1, 0]}},
            "bounced": {"$sum": {"$cond": ["$bounced", 1, 0]}},
        }},
    ]
    out = {}
    async for row in db.newsletter_recipients.aggregate(pipeline):
        out[row["_id"]] = {k: row[k] for k in ("delivered", "opened", "clicked", "bounced")}
    return out


def _new_campaign_doc(data: dict, admin_email: str) -> dict:
    now = datetime.utcnow()
    return {
        "_id": str(uuid.uuid4()),
        "subject": (data.get("subject") or data.get("subject_es") or "").strip(),
        "message": (data.get("message") or data.get("message_es") or "").strip(),
        "subject_en": (data.get("subject_en") or "").strip(),
        "message_en": (data.get("message_en") or "").strip(),
        "audience": data.get("audience") or "newsletter",
        "topic": (data.get("topic") or "").strip(),
        "status": "draft",
        "send_at": None,
        "frequency": None,
        "next_run_at": None,
        "parent_id": None,
        "sent": 0, "failed": 0, "total_recipients": 0,
        "created_by": admin_email,
        "created_at": now,
        "updated_at": now,
    }


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


# ════════════════════════════ CRUD ════════════════════════════

@router.post("/admin/newsletter/pro/campaigns")
async def create_campaign_pro(request: Request):
    """mode: draft | now | schedule (send_at) | recurring (frequency, send_at opcional)."""
    admin = await auth_admin(request)
    data = await request.json()
    mode = data.get("mode") or "draft"

    doc = _new_campaign_doc(data, admin.get("email", ""))
    if not doc["subject"] or not doc["message"]:
        raise HTTPException(status_code=400, detail="Asunto y mensaje (español) son requeridos")
    if doc["audience"] not in ("newsletter", "leads", "both"):
        raise HTTPException(status_code=400, detail="Audiencia inválida")

    db = get_db()
    if mode == "now":
        doc["status"] = "sending"
        await db.newsletter_campaigns.insert_one(doc)
        asyncio.create_task(run_campaign_pro(doc["_id"]))
        return {"success": True, "campaign": serialize(doc), "message": "Campaña en envío 🚀"}

    if mode == "schedule":
        send_at = _parse_dt(data.get("send_at"))
        if not send_at:
            raise HTTPException(status_code=400, detail="Fecha de envío inválida")
        doc["status"] = "scheduled"
        doc["send_at"] = send_at
        await db.newsletter_campaigns.insert_one(doc)
        return {"success": True, "campaign": serialize(doc),
                "message": f"Programada para {send_at.strftime('%d/%m/%Y %H:%M')} UTC"}

    if mode == "recurring":
        freq = data.get("frequency")
        if freq not in FREQ_DAYS:
            raise HTTPException(status_code=400, detail="Frecuencia inválida (weekly/biweekly/monthly)")
        first_run = _parse_dt(data.get("send_at")) or (datetime.utcnow() + timedelta(days=FREQ_DAYS[freq]))
        doc["status"] = "recurring"
        doc["frequency"] = freq
        doc["next_run_at"] = first_run
        await db.newsletter_campaigns.insert_one(doc)
        return {"success": True, "campaign": serialize(doc),
                "message": f"Recurrente {freq} — próxima corrida {first_run.strftime('%d/%m/%Y %H:%M')} UTC"}

    # draft
    await db.newsletter_campaigns.insert_one(doc)
    return {"success": True, "campaign": serialize(doc), "message": "Borrador guardado"}


@router.get("/admin/newsletter/pro/campaigns")
async def list_campaigns_pro(request: Request, limit: int = 100):
    await auth_admin(request)
    db = get_db()
    camps = await db.newsletter_campaigns.find().sort("created_at", -1).limit(limit).to_list(limit)
    stats = await _campaign_stats([c["_id"] for c in camps])
    out = []
    for c in camps:
        s = serialize(c)
        s["tracking"] = stats.get(c["_id"], {"delivered": 0, "opened": 0, "clicked": 0, "bounced": 0})
        out.append(s)
    return {"success": True, "campaigns": out}


@router.get("/admin/newsletter/pro/campaigns/{campaign_id}")
async def get_campaign_pro(campaign_id: str, request: Request):
    """Detalle + lista de destinatarios: quién abrió (y a qué hora), quién no."""
    await auth_admin(request)
    db = get_db()
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    recipients = await db.newsletter_recipients.find(
        {"campaign_id": campaign_id}
    ).sort([("opened", -1), ("first_open_at", 1)]).to_list(2000)

    opened = [r for r in recipients if r.get("opened")]
    not_opened = [r for r in recipients if not r.get("opened")]
    stats = (await _campaign_stats([campaign_id])).get(
        campaign_id, {"delivered": 0, "opened": 0, "clicked": 0, "bounced": 0})

    return {
        "success": True,
        "campaign": serialize(camp),
        "tracking": stats,
        "opened": [serialize(r) for r in opened],
        "not_opened": [serialize(r) for r in not_opened],
    }


@router.put("/admin/newsletter/pro/campaigns/{campaign_id}")
async def update_campaign_pro(campaign_id: str, request: Request):
    await auth_admin(request)
    data = await request.json()
    db = get_db()
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    if camp.get("status") not in EDITABLE_STATUSES:
        raise HTTPException(status_code=400, detail="Solo se pueden editar borradores o campañas programadas")

    updates = {"updated_at": datetime.utcnow()}
    for f_in, f_db in (("subject", "subject"), ("subject_es", "subject"), ("message", "message"),
                       ("message_es", "message"), ("subject_en", "subject_en"),
                       ("message_en", "message_en"), ("topic", "topic")):
        if f_in in data:
            updates[f_db] = (data.get(f_in) or "").strip()
    if data.get("audience") in ("newsletter", "leads", "both"):
        updates["audience"] = data["audience"]
    if "send_at" in data:
        send_at = _parse_dt(data.get("send_at"))
        if send_at:
            updates["send_at" if camp["status"] == "scheduled" else "next_run_at"] = send_at
    if data.get("frequency") in FREQ_DAYS and camp["status"] == "recurring":
        updates["frequency"] = data["frequency"]

    await db.newsletter_campaigns.update_one({"_id": campaign_id}, {"$set": updates})
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    return {"success": True, "campaign": serialize(camp), "message": "Campaña actualizada"}


@router.delete("/admin/newsletter/pro/campaigns/{campaign_id}")
async def delete_campaign_pro(campaign_id: str, request: Request):
    """Borradores/programadas/recurrentes → se eliminan (cancela envíos futuros).
    Enviadas → se elimina del historial junto con su tracking."""
    await auth_admin(request)
    db = get_db()
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    await db.newsletter_campaigns.delete_one({"_id": campaign_id})
    await db.newsletter_recipients.delete_many({"campaign_id": campaign_id})
    was_future = camp.get("status") in EDITABLE_STATUSES
    return {"success": True,
            "message": "Campaña cancelada y eliminada" if was_future else "Campaña eliminada del historial"}


@router.post("/admin/newsletter/pro/campaigns/{campaign_id}/send")
async def send_campaign_now_pro(campaign_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    if camp.get("status") not in EDITABLE_STATUSES:
        raise HTTPException(status_code=400, detail="Esta campaña ya fue enviada")
    await db.newsletter_campaigns.update_one(
        {"_id": campaign_id}, {"$set": {"status": "sending", "frequency": None, "next_run_at": None}})
    asyncio.create_task(run_campaign_pro(campaign_id))
    return {"success": True, "message": "Campaña en envío 🚀"}


@router.post("/admin/newsletter/pro/campaigns/{campaign_id}/duplicate")
async def duplicate_campaign_pro(campaign_id: str, request: Request):
    admin = await auth_admin(request)
    db = get_db()
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    doc = _new_campaign_doc(camp, admin.get("email", ""))
    doc["subject"] = f"{camp.get('subject', '')} (copia)"
    await db.newsletter_campaigns.insert_one(doc)
    return {"success": True, "campaign": serialize(doc), "message": "Duplicada como borrador"}


# ════════════════════════════ AI ════════════════════════════

async def _llm_json(system_msg: str, prompt: str, max_retries: int = 1):
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="EMERGENT_LLM_KEY no configurada")
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    last_err = None
    for _ in range(max_retries + 1):
        try:
            chat = LlmChat(
                api_key=api_key,
                session_id=f"newsletter-ai-{datetime.utcnow().timestamp()}",
                system_message=system_msg,
            ).with_model("openai", "gpt-5.4")
            resp = await chat.send_message(UserMessage(text=prompt))
            txt = (resp or "").strip()
            if txt.startswith("```"):
                txt = txt.strip("`").lstrip("json").strip()
            return json.loads(txt)
        except json.JSONDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            logger.exception("newsletter AI failed")
            raise HTTPException(status_code=502, detail=f"Error de la AI: {e}")
    raise HTTPException(status_code=502, detail=f"La AI no devolvió JSON válido: {last_err}")


async def _available_properties_ctx() -> str:
    db = get_db()
    props = await db.properties.find(
        {"status": {"$in": ["available", "disponible"]}},
        {"address": 1, "rent": 1, "bedrooms": 1, "bathrooms": 1}
    ).limit(5).to_list(5)
    if not props:
        return "Sin propiedades disponibles en este momento."
    return "\n".join(
        f"- {p.get('address', '')} · {p.get('bedrooms', '?')} hab / {p.get('bathrooms', '?')} baños · ${p.get('rent', '?')}/mes"
        for p in props
    )


_AI_SYSTEM = (
    "Eres el experto en marketing de Ross House Rentals LLC, una empresa de renta de casas "
    "en Dumas, Texas. Audiencia: inquilinos actuales y prospectos (familias trabajadoras, "
    "muchas hispanas). Tono cercano, útil y profesional. SIEMPRE respondes SOLO con JSON válido, "
    "sin texto adicional ni markdown."
)


@router.post("/admin/newsletter/ai/topics")
async def ai_topics(request: Request):
    await auth_admin(request)
    data = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    count = min(int(data.get("count", 8) or 8), 12)
    props = await _available_properties_ctx()
    month = datetime.utcnow().strftime("%B %Y")
    prompt = f"""Genera {count} ideas de temas para el newsletter de este mes ({month}).
Propiedades disponibles ahora:
{props}

Mezcla: tips para inquilinos, mantenimiento de temporada, noticias de propiedades disponibles,
finanzas del hogar, comunidad de Dumas TX, y fechas importantes (pagos, clima, festividades).

Devuelve JSON: {{"topics": [{{"title": "...", "why": "por qué funciona en 1 frase"}}]}}"""
    result = await _llm_json(_AI_SYSTEM, prompt)
    return {"success": True, "topics": result.get("topics", [])}


@router.post("/admin/newsletter/ai/generate")
async def ai_generate_content(request: Request):
    await auth_admin(request)
    data = await request.json()
    topic = (data.get("topic") or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Tema requerido")
    props = await _available_properties_ctx()
    prompt = f"""Escribe el contenido de un newsletter sobre el tema: "{topic}".
Propiedades disponibles (menciónalas solo si es relevante):
{props}

Requisitos:
- Versión en ESPAÑOL y versión en INGLÉS (traducción natural, no literal).
- Asunto llamativo (max 60 caracteres, puede llevar 1 emoji).
- Mensaje de 120-180 palabras, párrafos cortos, con 1 llamada a la acción
  (visitar rosshouserentals.com o llamar al (806) 934-2018).
- Texto plano con saltos de línea (sin HTML ni markdown).

Devuelve JSON:
{{"subject_es": "...", "message_es": "...", "subject_en": "...", "message_en": "..."}}"""
    result = await _llm_json(_AI_SYSTEM, prompt, max_retries=1)
    return {"success": True, **{k: result.get(k, "") for k in
                                ("subject_es", "message_es", "subject_en", "message_en")}}


@router.post("/admin/newsletter/ai/year-plan")
async def ai_year_plan(request: Request):
    """Genera un plan de contenido para 12 meses y crea 12 campañas programadas
    (una por mes, editables antes de su fecha de envío)."""
    admin = await auth_admin(request)
    data = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    day_of_month = min(max(int(data.get("day_of_month", 15) or 15), 1), 28)
    hour_utc = 16  # ~10-11 AM Texas

    prompt = """Crea un PLAN ANUAL de newsletter (12 meses, empezando el próximo mes).
Cada mes: un tema relevante a su temporada en Texas (clima, festividades, pagos,
mantenimiento estacional, mudanzas de verano, impuestos en enero-abril, etc.).

Para CADA mes devuelve el contenido COMPLETO bilingüe:
- topic: tema del mes (corto)
- subject_es / subject_en: asunto (max 60 chars, 1 emoji opcional)
- message_es / message_en: 100-150 palabras, texto plano, con llamada a la acción
  (rosshouserentals.com o (806) 934-2018)

Devuelve JSON: {"plan": [{"month_offset": 1, "topic": "...", "subject_es": "...",
"message_es": "...", "subject_en": "...", "message_en": "..."}, ...]} con month_offset 1..12."""
    result = await _llm_json(_AI_SYSTEM, prompt, max_retries=1)
    plan = result.get("plan", [])
    if not plan:
        raise HTTPException(status_code=502, detail="La AI no generó el plan")

    db = get_db()
    now = datetime.utcnow()
    created = []
    for item in plan[:12]:
        offset = int(item.get("month_offset", 1) or 1)
        month = (now.month - 1 + offset) % 12 + 1
        year = now.year + (now.month - 1 + offset) // 12
        send_at = datetime(year, month, day_of_month, hour_utc, 0)
        doc = _new_campaign_doc(item, admin.get("email", ""))
        doc["status"] = "scheduled"
        doc["send_at"] = send_at
        doc["topic"] = (item.get("topic") or "").strip()
        if not doc["subject"] or not doc["message"]:
            continue
        await db.newsletter_campaigns.insert_one(doc)
        created.append(serialize(doc))

    return {"success": True, "created": len(created), "campaigns": created,
            "message": f"Plan anual creado: {len(created)} campañas programadas (editables)"}


# ════════════════════════════ Scheduler ════════════════════════════

async def _scheduler_tick():
    db = get_db()
    now = datetime.utcnow()

    # Programadas de una sola vez
    scheduled = await db.newsletter_campaigns.find(
        {"status": "scheduled", "send_at": {"$lte": now}}).to_list(20)
    for camp in scheduled:
        res = await db.newsletter_campaigns.update_one(
            {"_id": camp["_id"], "status": "scheduled"}, {"$set": {"status": "sending"}})
        if res.modified_count:
            logger.info(f"📣 [scheduler] enviando campaña programada {camp['_id']}")
            asyncio.create_task(run_campaign_pro(camp["_id"]))

    # Recurrentes → crear campaña hija por corrida
    recurring = await db.newsletter_campaigns.find(
        {"status": "recurring", "next_run_at": {"$lte": now}}).to_list(20)
    for camp in recurring:
        freq = camp.get("frequency") or "monthly"
        next_run = now + timedelta(days=FREQ_DAYS.get(freq, 30))
        res = await db.newsletter_campaigns.update_one(
            {"_id": camp["_id"], "status": "recurring", "next_run_at": camp.get("next_run_at")},
            {"$set": {"next_run_at": next_run, "last_sent_at": now}})
        if not res.modified_count:
            continue
        child = _new_campaign_doc(camp, camp.get("created_by", ""))
        child["status"] = "sending"
        child["parent_id"] = camp["_id"]
        child["subject"] = camp.get("subject", "")
        await db.newsletter_campaigns.insert_one(child)
        logger.info(f"📣 [scheduler] corrida recurrente {camp['_id']} → hija {child['_id']}")
        asyncio.create_task(run_campaign_pro(child["_id"]))


async def newsletter_scheduler_loop():
    """Loop de fondo — arrancado desde server.py."""
    await asyncio.sleep(20)  # esperar a que el server termine de arrancar
    logger.info("📅 Newsletter scheduler activo (revisa cada 60s)")
    while True:
        try:
            await _scheduler_tick()
        except Exception as e:
            logger.warning(f"newsletter scheduler tick error: {e}")
        await asyncio.sleep(60)
