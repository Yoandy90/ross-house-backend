"""
Drip Email + Blog — Biblioteca de plantillas bilingües generadas con AI
========================================================================
- email_templates: plantillas ES/EN por categoría, generadas con Claude.
- Motor de goteo (drip_cron): envía la siguiente plantilla activa no enviada a
  todos los suscriptores del newsletter, N veces por semana (config).
- Blog público: cada plantilla puede publicarse como post en /noticias.

Config app_settings {_id:'drip'}: {enabled, per_week (1|2|3), hour_ct}
Progreso de generación AI: app_settings {_id:'drip_generation'}
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .shared import get_db, auth_admin

router = APIRouter()
logger = logging.getLogger(__name__)

CATEGORIES = {
    "rentar": "🏠 Primeros pasos para rentar",
    "comprar": "🔑 Compra tu primera casa",
    "credito": "💳 Crédito y finanzas",
    "mantenimiento": "🔧 Mantenimiento del hogar",
    "energia": "⚡ Ahorro de energía",
    "dumas": "🌾 Vida en Dumas y el Panhandle",
    "mudanza": "📦 Mudanzas sin estrés",
    "seguros": "🛡️ Seguros y protección",
    "inversion": "📈 Inversión inmobiliaria básica",
    "derechos": "⚖️ Derechos del inquilino en Texas",
}

GEN_PROMPT = """Eres el redactor de contenido de Ross House Rentals LLC, una empresa \
de rentas y bienes raíces en Dumas, Texas (Panhandle). Escribes emails educativos BILINGÜES \
para suscriptores: familias trabajadoras, muchos hispanohablantes, primeros compradores e inquilinos.

Tono: cercano, práctico, sin tecnicismos, consejos accionables. Menciona el contexto local \
(Dumas, el clima del Panhandle, Texas) cuando aplique. NO inventes precios exactos ni leyes específicas \
con números; habla en términos generales y recomienda verificar. Longitud: 200-320 palabras por idioma. \
Usa párrafos cortos y listas con •. TEXTO PLANO únicamente: NO uses markdown (nada de **, ##, _). \
Termina con una llamada a la acción suave invitando a contactar \
a Ross House Rentals o visitar www.rosshouserentals.com.

Genera {n} emails DIFERENTES para la categoría "{category}". Temas ya usados que NO debes repetir: {used}

Responde SOLO con JSON válido (dentro de los textos usa comillas simples, nunca comillas dobles):
{{"emails": [{{"subject_es": "...", "subject_en": "...", "body_es": "...", "body_en": "..."}}]}}"""


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()
               .replace("á", "a").replace("é", "e").replace("í", "i")
               .replace("ó", "o").replace("ú", "u").replace("ñ", "n")).strip("-")
    return s[:80] or str(uuid.uuid4())[:8]


def _tpl_out(d: dict) -> dict:
    return {
        "id": str(d["_id"]),
        "category": d.get("category", ""),
        "category_label": CATEGORIES.get(d.get("category", ""), d.get("category", "")),
        "subject_es": d.get("subject_es", ""),
        "subject_en": d.get("subject_en", ""),
        "body_es": d.get("body_es", ""),
        "body_en": d.get("body_en", ""),
        "status": d.get("status", "active"),
        "sent_at": d["sent_at"].isoformat() if d.get("sent_at") else None,
        "sent_count": d.get("sent_count", 0),
        "published_to_blog": bool(d.get("published_to_blog")),
        "slug": d.get("slug", ""),
        "created_at": d["created_at"].isoformat() if d.get("created_at") else "",
    }


# ═══ AI Generation ═══════════════════════════════════════════

async def _generate_batch(category: str, n: int, used_subjects: list[str]) -> list[dict]:
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise RuntimeError("EMERGENT_LLM_KEY no configurada")
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    from rental.ai_brain_router import MODEL_PROVIDER, MODEL_NAME
    prompt = GEN_PROMPT.format(
        n=n, category=CATEGORIES.get(category, category),
        used=", ".join(used_subjects[-25:]) or "ninguno")
    chat = LlmChat(api_key=api_key, session_id=f"drip_{uuid.uuid4()}",
                   system_message="Eres un redactor experto de email marketing inmobiliario bilingüe."
                   ).with_model(MODEL_PROVIDER, MODEL_NAME)
    raw = str(await chat.send_message(UserMessage(text=prompt)))
    blob = raw[raw.index("{"):raw.rindex("}") + 1]
    try:
        data = json.loads(blob, strict=False)
    except json.JSONDecodeError:
        # reparar: escapar saltos de línea crudos dentro de strings
        repaired = re.sub(r'(?<!\\)\n', '\\\\n', blob)
        data = json.loads(repaired, strict=False)
    return data.get("emails", [])


async def _run_generation(total: int, categories: list[str]):
    db = get_db()
    state_id = "drip_generation"

    def _batch_in_thread(cat: str, n: int, used: list[str]) -> list[dict]:
        # Corre la llamada al LLM en un hilo con su propio event loop para
        # NO bloquear el servidor (litellm hace llamadas sincrónicas internas).
        return asyncio.run(_generate_batch(cat, n, used))

    async def _upd(**f):
        await db.app_settings.update_one({"_id": state_id}, {"$set": f}, upsert=True)

    try:
        per_cat = max(1, round(total / len(categories)))
        done = 0
        for cat in categories:
            used = [t["subject_es"] async for t in db.email_templates.find(
                {"category": cat}, {"subject_es": 1}) if t.get("subject_es")]
            # generar solo lo que falta para llegar al objetivo por categoría
            remaining = max(0, per_cat - len(used))
            while remaining > 0:
                batch_n = min(5, remaining)
                try:
                    emails = await asyncio.to_thread(_batch_in_thread, cat, batch_n, list(used))
                except Exception as e:
                    logger.error(f"[drip] generación falló ({cat}): {e}")
                    await asyncio.sleep(3)
                    emails = []
                    remaining -= batch_n
                    continue
                for em in emails:
                    subj = (em.get("subject_es") or "").strip()
                    if not subj or not em.get("body_es"):
                        continue
                    if subj.lower() in {u.strip().lower() for u in used}:
                        continue  # evitar duplicados
                    await db.email_templates.insert_one({
                        "category": cat,
                        "subject_es": subj.replace("**", ""),
                        "subject_en": (em.get("subject_en") or "").strip().replace("**", ""),
                        "body_es": em["body_es"].strip().replace("**", ""),
                        "body_en": (em.get("body_en") or "").strip().replace("**", ""),
                        "status": "active",
                        "sent_at": None, "sent_count": 0,
                        "published_to_blog": False,
                        "slug": _slugify(subj),
                        "ai_generated": True,
                        "created_at": datetime.utcnow(),
                    })
                    used.append(subj)
                    done += 1
                remaining -= batch_n
                await _upd(done=done)
        await _upd(running=False, done=done, finished_at=datetime.utcnow())
        logger.info(f"[drip] generación completa: {done} plantillas")
    except Exception as e:
        logger.error(f"[drip] generación abortada: {e}")
        await _upd(running=False, error=str(e))


class GenerateRequest(BaseModel):
    count: int = 50
    categories: Optional[list[str]] = None


@router.post("/admin/drip/generate")
async def generate_templates(request: Request, body: GenerateRequest):
    await auth_admin(request)
    db = get_db()
    state = await db.app_settings.find_one({"_id": "drip_generation"}) or {}
    if state.get("running"):
        raise HTTPException(409, "Ya hay una generación en curso")
    cats = [c for c in (body.categories or list(CATEGORIES)) if c in CATEGORIES]
    if not cats:
        raise HTTPException(400, "Categorías inválidas")
    total = max(1, min(body.count, 200))
    await db.app_settings.update_one(
        {"_id": "drip_generation"},
        {"$set": {"running": True, "done": 0, "total": total, "error": "",
                  "started_at": datetime.utcnow()}}, upsert=True)
    asyncio.create_task(_run_generation(total, cats))
    return {"success": True, "message": f"Generando {total} plantillas con AI en segundo plano"}


@router.get("/admin/drip/generation-status")
async def generation_status(request: Request):
    await auth_admin(request)
    st = await get_db().app_settings.find_one({"_id": "drip_generation"}) or {}
    return {"success": True, "running": bool(st.get("running")),
            "done": st.get("done", 0), "total": st.get("total", 0),
            "error": st.get("error", "")}


# ═══ Templates CRUD ══════════════════════════════════════════

@router.get("/admin/drip/templates")
async def list_templates(request: Request, category: Optional[str] = None,
                         status: Optional[str] = None, sent: Optional[str] = None,
                         q: Optional[str] = None, limit: int = 60, skip: int = 0):
    await auth_admin(request)
    db = get_db()
    filt: dict = {}
    if category:
        filt["category"] = category
    if status:
        filt["status"] = status
    if sent == "yes":
        filt["sent_at"] = {"$ne": None}
    elif sent == "no":
        filt["sent_at"] = None
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        filt["$or"] = [{"subject_es": rx}, {"subject_en": rx}, {"body_es": rx}]
    docs = await db.email_templates.find(filt).sort("created_at", 1).skip(skip).to_list(min(limit, 100))
    total = await db.email_templates.count_documents(filt)
    return {"success": True, "templates": [_tpl_out(d) for d in docs], "total": total,
            "categories": [{"key": k, "label": v} for k, v in CATEGORIES.items()]}


class TemplateUpdate(BaseModel):
    subject_es: Optional[str] = None
    subject_en: Optional[str] = None
    body_es: Optional[str] = None
    body_en: Optional[str] = None
    status: Optional[str] = None
    published_to_blog: Optional[bool] = None


@router.patch("/admin/drip/templates/{tpl_id}")
async def update_template(request: Request, tpl_id: str, body: TemplateUpdate):
    await auth_admin(request)
    db = get_db()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nada que actualizar")
    if "status" in updates and updates["status"] not in ("active", "draft", "archived"):
        raise HTTPException(400, "status inválido")
    if updates.get("published_to_blog"):
        updates["blog_published_at"] = datetime.utcnow()
    updates["updated_at"] = datetime.utcnow()
    res = await db.email_templates.update_one({"_id": ObjectId(tpl_id)}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(404, "Plantilla no encontrada")
    doc = await db.email_templates.find_one({"_id": ObjectId(tpl_id)})
    return {"success": True, "template": _tpl_out(doc)}


@router.delete("/admin/drip/templates/{tpl_id}")
async def delete_template(request: Request, tpl_id: str):
    await auth_admin(request)
    res = await get_db().email_templates.delete_one({"_id": ObjectId(tpl_id)})
    if res.deleted_count == 0:
        raise HTTPException(404, "Plantilla no encontrada")
    return {"success": True}


# ═══ Drip engine ═════════════════════════════════════════════

def _bilingual_message(tpl: dict) -> str:
    parts = [tpl.get("body_es", "").strip()]
    if tpl.get("body_en"):
        parts.append("\n————————  English  ————————\n")
        parts.append(f"{tpl.get('subject_en', '')}\n")
        parts.append(tpl["body_en"].strip())
    return "\n".join(parts)


async def send_template_now(tpl: dict) -> dict:
    """Envía una plantilla a todos los suscriptores (reusa la infra de campañas)."""
    from .newsletter_router import _run_campaign
    db = get_db()
    campaign_id = str(uuid.uuid4())
    await db.newsletter_campaigns.insert_one({
        "_id": campaign_id,
        "subject": tpl.get("subject_es", ""),
        "message": _bilingual_message(tpl),
        "audience": "newsletter",
        "status": "sending", "sent": 0, "failed": 0, "total_recipients": 0,
        "type": "drip", "template_id": str(tpl["_id"]),
        "created_by": "drip-engine",
        "created_at": datetime.utcnow(),
    })
    await _run_campaign(campaign_id, tpl.get("subject_es", ""), _bilingual_message(tpl), "newsletter")
    camp = await db.newsletter_campaigns.find_one({"_id": campaign_id})
    await db.email_templates.update_one(
        {"_id": tpl["_id"]},
        {"$set": {"sent_at": datetime.utcnow(), "sent_count": (camp or {}).get("sent", 0)}})
    return {"campaign_id": campaign_id, "sent": (camp or {}).get("sent", 0)}


@router.get("/admin/drip/config")
async def get_drip_config(request: Request):
    await auth_admin(request)
    db = get_db()
    cfg = await db.app_settings.find_one({"_id": "drip"}) or {}
    pending = await db.email_templates.count_documents({"status": "active", "sent_at": None})
    sent_total = await db.email_templates.count_documents({"sent_at": {"$ne": None}})
    total = await db.email_templates.count_documents({})
    next_tpl = await db.email_templates.find_one(
        {"status": "active", "sent_at": None}, sort=[("created_at", 1)])
    subs = await db.newsletter_subscribers.count_documents({"unsubscribed": {"$ne": True}})
    return {"success": True, "config": {
        "enabled": cfg.get("enabled", True),
        "per_week": int(cfg.get("per_week") or 2),
        "hour_ct": int(cfg.get("hour_ct") or 9),
    }, "queue": {
        "pending": pending, "sent": sent_total, "total": total,
        "subscribers": subs,
        "next": _tpl_out(next_tpl) if next_tpl else None,
        "last_sent_at": cfg.get("last_sent_at").isoformat() if cfg.get("last_sent_at") else None,
    }}


class DripConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    per_week: Optional[int] = None
    hour_ct: Optional[int] = None


@router.patch("/admin/drip/config")
async def update_drip_config(request: Request, body: DripConfigUpdate):
    await auth_admin(request)
    updates: dict = {}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.per_week is not None:
        if body.per_week not in (1, 2, 3):
            raise HTTPException(400, "per_week debe ser 1, 2 o 3")
        updates["per_week"] = body.per_week
    if body.hour_ct is not None:
        updates["hour_ct"] = max(6, min(body.hour_ct, 20))
    if not updates:
        raise HTTPException(400, "Nada que actualizar")
    await get_db().app_settings.update_one({"_id": "drip"}, {"$set": updates}, upsert=True)
    return {"success": True, "updated": updates}


@router.post("/admin/drip/send-next")
async def send_next_now(request: Request):
    """Envía AHORA la siguiente plantilla de la cola (manual)."""
    await auth_admin(request)
    db = get_db()
    tpl = await db.email_templates.find_one(
        {"status": "active", "sent_at": None}, sort=[("created_at", 1)])
    if not tpl:
        raise HTTPException(404, "No hay plantillas pendientes — genera más con AI")
    result = await send_template_now(tpl)
    await db.app_settings.update_one(
        {"_id": "drip"}, {"$set": {"last_sent_at": datetime.utcnow()}}, upsert=True)
    return {"success": True, "subject": tpl.get("subject_es"), **result}


@router.post("/admin/drip/templates/{tpl_id}/preview")
async def send_preview(request: Request, tpl_id: str):
    """Envía ESTA plantilla solo al email indicado (vista previa, no marca como enviada)."""
    await auth_admin(request)
    data = await request.json()
    to = (data.get("email") or "").strip().lower()
    if not to or "@" not in to:
        raise HTTPException(400, "Email inválido")
    db = get_db()
    tpl = await db.email_templates.find_one({"_id": ObjectId(tpl_id)})
    if not tpl:
        raise HTTPException(404, "Plantilla no encontrada")
    from .newsletter_router import _sendgrid, _send_one, _campaign_html
    sg_key, from_email = await _sendgrid()
    if not sg_key:
        raise HTTPException(500, "SendGrid no configurado")
    subject = f"[VISTA PREVIA] {tpl.get('subject_es', '')}"
    ok = await _send_one(sg_key, from_email, to, subject,
                         _campaign_html(tpl.get("subject_es", ""), _bilingual_message(tpl), None, ""))
    if not ok:
        raise HTTPException(502, "SendGrid rechazó el envío")
    return {"success": True, "sent_to": to, "subject": tpl.get("subject_es", "")}


# ═══ Comentarios del blog ════════════════════════════════════

@router.get("/public/blog/posts/{slug}/comments")
async def blog_comments(slug: str):
    db = get_db()
    docs = await db.blog_comments.find(
        {"slug": slug, "hidden": {"$ne": True}}).sort("created_at", -1).to_list(100)
    return {"success": True, "comments": [{
        "id": str(d["_id"]), "name": d.get("name", "Anónimo"),
        "comment": d.get("comment", ""),
        "created_at": d["created_at"].isoformat() if d.get("created_at") else "",
    } for d in docs]}


@router.post("/public/blog/posts/{slug}/comments")
async def add_blog_comment(request: Request, slug: str):
    db = get_db()
    post = await db.email_templates.find_one({"slug": slug, "published_to_blog": True})
    if not post:
        raise HTTPException(404, "Post no encontrado")
    data = await request.json()
    name = (data.get("name") or "").strip()[:60]
    comment = (data.get("comment") or "").strip()[:1000]
    if not name or len(comment) < 3:
        raise HTTPException(400, "Escribe tu nombre y un comentario")
    ip = request.client.host if request.client else "unknown"
    # anti-spam simple: máx 3 comentarios por IP por hora
    from datetime import timedelta
    recent = await db.blog_comments.count_documents(
        {"ip": ip, "created_at": {"$gte": datetime.utcnow() - timedelta(hours=1)}})
    if recent >= 3:
        raise HTTPException(429, "Demasiados comentarios — intenta más tarde")
    doc = {"slug": slug, "name": name, "comment": comment, "ip": ip,
           "hidden": False, "created_at": datetime.utcnow()}
    res = await db.blog_comments.insert_one(doc)
    return {"success": True, "comment": {
        "id": str(res.inserted_id), "name": name, "comment": comment,
        "created_at": doc["created_at"].isoformat()}}


@router.delete("/admin/blog/comments/{comment_id}")
async def delete_blog_comment(request: Request, comment_id: str):
    await auth_admin(request)
    res = await get_db().blog_comments.delete_one({"_id": ObjectId(comment_id)})
    if res.deleted_count == 0:
        raise HTTPException(404, "Comentario no encontrado")
    return {"success": True}


# ═══ Blog público ════════════════════════════════════════════

@router.get("/public/blog/posts")
async def public_blog_list(limit: int = 20, skip: int = 0, category: Optional[str] = None):
    db = get_db()
    filt: dict = {"published_to_blog": True}
    if category:
        filt["category"] = category
    docs = await db.email_templates.find(
        filt, {"body_en": 0}).sort("blog_published_at", -1).skip(skip).to_list(min(limit, 50))
    total = await db.email_templates.count_documents(filt)
    posts = []
    for d in docs:
        posts.append({
            "slug": d.get("slug", ""),
            "title_es": d.get("subject_es", ""),
            "title_en": d.get("subject_en", ""),
            "category": d.get("category", ""),
            "category_label": CATEGORIES.get(d.get("category", ""), ""),
            "excerpt": (d.get("body_es", "")[:180] + "…") if len(d.get("body_es", "")) > 180 else d.get("body_es", ""),
            "published_at": d["blog_published_at"].isoformat() if d.get("blog_published_at") else "",
        })
    return {"success": True, "posts": posts, "total": total,
            "categories": [{"key": k, "label": v} for k, v in CATEGORIES.items()]}


@router.get("/public/blog/posts/{slug}")
async def public_blog_post(slug: str):
    db = get_db()
    d = await db.email_templates.find_one({"slug": slug, "published_to_blog": True})
    if not d:
        raise HTTPException(404, "Post no encontrado")
    return {"success": True, "post": {
        "slug": d.get("slug", ""),
        "title_es": d.get("subject_es", ""),
        "title_en": d.get("subject_en", ""),
        "category": d.get("category", ""),
        "category_label": CATEGORIES.get(d.get("category", ""), ""),
        "body_es": d.get("body_es", ""),
        "body_en": d.get("body_en", ""),
        "published_at": d["blog_published_at"].isoformat() if d.get("blog_published_at") else "",
    }}
