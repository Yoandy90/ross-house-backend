"""
Facebook (Meta Graph API) Marketing Router
═══════════════════════════════════════════════════════════════════════════════
Publica en la página de Facebook, lee/responde comentarios y mensajes de
Messenger — con IA en modo Automático o Manual (sugerencias).

Credenciales (gestionadas desde el Admin Panel → API Keys, en DB):
  META_APP_ID, META_APP_SECRET, META_CONFIG_ID (Facebook Login for Business)

Flujo OAuth: POST /admin/marketing/facebook/connect → dialog →
  GET /marketing/facebook/oauth/callback → long-lived token → /me/accounts →
  page tokens cifrados en `fb_pages` + suscripción de webhooks (feed, messages).

Webhook (configurar en Meta dashboard → Webhooks → Page):
  URL:    {backend}/api/marketing/facebook/webhook
  Token:  META_WEBHOOK_VERIFY_TOKEN (default: rosshouse-fb-webhook-2026)
"""
import os
import hmac
import json
import base64
import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, PlainTextResponse
from pydantic import BaseModel

from rental.shared import auth_admin, get_db, send_rental_push_to_admins
from rental.vault_router import encrypt, decrypt

logger = logging.getLogger("facebook")
router = APIRouter(tags=["Facebook"])

_ai_brain = None


def set_ai_brain(brain):
    global _ai_brain
    _ai_brain = brain


def _cfg(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _graph_base() -> str:
    return f"https://graph.facebook.com/{_cfg('META_GRAPH_VERSION', 'v22.0')}"


def _redirect_uri() -> str:
    return _cfg("META_REDIRECT_URI",
                "https://ross-house-backend-production.up.railway.app/api/marketing/facebook/oauth/callback")


def _web_redirect() -> str:
    return _cfg("META_WEB_REDIRECT", "https://www.rosshouserentals.com/admin/marketing/facebook")


def _verify_token() -> str:
    return _cfg("META_WEBHOOK_VERIFY_TOKEN", "rosshouse-fb-webhook-2026")


def _configured() -> bool:
    return bool(_cfg("META_APP_ID") and _cfg("META_APP_SECRET"))


def _now():
    return datetime.now(timezone.utc)


async def _graph(method: str, path: str, token: str, *, params: dict = None,
                 data: dict = None, files: dict = None, timeout: int = 60) -> dict:
    """Call Graph API; raise HTTPException(502) with the Meta error on failure."""
    url = f"{_graph_base()}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, url,
                                 params={"access_token": token, **(params or {})},
                                 data=data, files=files)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:300]}
    if r.is_error:
        logger.error(f"Graph {method} {path} → {r.status_code}: {json.dumps(body)[:400]}")
        msg = (body.get("error") or {}).get("message", "Error de Meta Graph API")
        raise HTTPException(status_code=502, detail=f"Facebook: {msg}")
    return body


async def _get_page() -> Optional[dict]:
    """Single business page model — the connected page doc or None."""
    return await get_db().fb_pages.find_one({}, sort=[("updated_at", -1)])


async def _page_token() -> tuple:
    page = await _get_page()
    if not page:
        raise HTTPException(status_code=400, detail="No hay página de Facebook conectada")
    token = decrypt(page["token_encrypted"])
    if not token:
        raise HTTPException(status_code=401, detail="Token inválido. Reconecta la página.")
    return page["page_id"], token


async def _get_settings() -> dict:
    doc = await get_db().admin_config.find_one({"type": "facebook_settings"}) or {}
    return {
        "auto_comments": bool(doc.get("auto_comments", False)),
        "auto_messages": bool(doc.get("auto_messages", False)),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS / CONNECT / CALLBACK / DISCONNECT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/marketing/facebook/status")
async def fb_status(request: Request):
    await auth_admin(request)
    page = await _get_page()
    settings = await _get_settings()
    return {
        "success": True,
        "configured": _configured(),
        "connected": bool(page),
        "page": {
            "id": page["page_id"], "name": page.get("page_name"),
            "picture": page.get("picture"), "connected_at": page.get("connected_at"),
            "webhook_subscribed": page.get("webhook_subscribed", False),
        } if page else None,
        "settings": settings,
        "redirect_uri": _redirect_uri(),
        "webhook_url": _redirect_uri().replace("/oauth/callback", "/webhook"),
        "webhook_verify_token": _verify_token(),
    }


@router.post("/admin/marketing/facebook/connect")
async def fb_connect(request: Request):
    await auth_admin(request)
    if not _configured():
        raise HTTPException(status_code=400, detail="Configura META_APP_ID y META_APP_SECRET en API Keys")
    state = secrets.token_urlsafe(24)
    await get_db().fb_oauth_states.insert_one({"state": state, "created_at": _now()})
    from urllib.parse import urlencode
    params = {
        "client_id": _cfg("META_APP_ID"),
        "redirect_uri": _redirect_uri(),
        "state": state,
        "response_type": "code",
    }
    config_id = _cfg("META_CONFIG_ID")
    if config_id:
        params["config_id"] = config_id
    else:
        params["scope"] = ("pages_show_list,pages_read_engagement,pages_read_user_content,"
                           "pages_manage_posts,pages_manage_engagement,pages_manage_metadata,pages_messaging")
    version = _cfg("META_GRAPH_VERSION", "v22.0")
    return {"success": True,
            "auth_url": f"https://www.facebook.com/{version}/dialog/oauth?{urlencode(params)}"}


@router.get("/marketing/facebook/oauth/callback")
async def fb_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    if error or not code:
        return RedirectResponse(f"{_web_redirect()}?error={error or 'no_code'}")
    if not await get_db().fb_oauth_states.find_one_and_delete({"state": state}):
        return RedirectResponse(f"{_web_redirect()}?error=invalid_state")

    async with httpx.AsyncClient(timeout=30) as client:
        # 1) code → user token
        r = await client.get(f"{_graph_base()}/oauth/access_token", params={
            "client_id": _cfg("META_APP_ID"), "client_secret": _cfg("META_APP_SECRET"),
            "redirect_uri": _redirect_uri(), "code": code,
        })
        tok = r.json()
        if "access_token" not in tok:
            logger.error(f"FB token exchange failed: {tok}")
            return RedirectResponse(f"{_web_redirect()}?error=token_exchange_failed")
        # 2) → long-lived user token (~60 días; los page tokens derivados no expiran)
        r2 = await client.get(f"{_graph_base()}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": _cfg("META_APP_ID"), "client_secret": _cfg("META_APP_SECRET"),
            "fb_exchange_token": tok["access_token"],
        })
        long_tok = r2.json().get("access_token") or tok["access_token"]
        # 3) páginas del usuario
        r3 = await client.get(f"{_graph_base()}/me/accounts", params={
            "access_token": long_tok,
            "fields": "id,name,access_token,tasks,picture{url}",
        })
        pages = (r3.json() or {}).get("data") or []

    if not pages:
        return RedirectResponse(f"{_web_redirect()}?error=no_pages")

    db = get_db()
    for p in pages:
        doc = {
            "page_id": p["id"],
            "page_name": p.get("name"),
            "picture": ((p.get("picture") or {}).get("data") or {}).get("url"),
            "token_encrypted": encrypt(p["access_token"]),
            "tasks": p.get("tasks", []),
            "connected_at": _now(),
            "updated_at": _now(),
        }
        # 4) suscribir webhooks de la página (feed = comentarios, messages = Messenger)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                sub = await client.post(f"{_graph_base()}/{p['id']}/subscribed_apps", params={
                    "access_token": p["access_token"],
                    "subscribed_fields": "feed,messages",
                })
            doc["webhook_subscribed"] = bool((sub.json() or {}).get("success"))
        except Exception as e:
            logger.warning(f"FB webhook subscribe failed for {p['id']}: {e}")
            doc["webhook_subscribed"] = False
        await db.fb_pages.update_one({"page_id": p["id"]}, {"$set": doc}, upsert=True)

    logger.info(f"📘 Facebook conectado: {[p.get('name') for p in pages]}")
    return RedirectResponse(f"{_web_redirect()}?connected=1")


@router.delete("/admin/marketing/facebook/account")
async def fb_disconnect(request: Request):
    await auth_admin(request)
    await get_db().fb_pages.delete_many({})
    return {"success": True, "message": "Página de Facebook desconectada"}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLISH + POSTS
# ═══════════════════════════════════════════════════════════════════════════════

class PublishPayload(BaseModel):
    message: str = ""
    media_base64: Optional[str] = None   # foto o video en base64
    media_type: Optional[str] = None     # "photo" | "video"
    media_url: Optional[str] = None      # alternativa: URL pública
    scheduled_at: Optional[str] = None   # ISO datetime → programar publicación


def _schedule_params(scheduled_at: Optional[str]) -> dict:
    """FB scheduling: published=false + scheduled_publish_time (10 min–75 días)."""
    if not scheduled_at:
        return {"published": "true"}
    try:
        dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="Fecha de programación inválida")
    delta = (dt - _now()).total_seconds()
    if delta < 600:
        raise HTTPException(status_code=400, detail="Programa al menos 10 minutos en el futuro")
    if delta > 75 * 86400:
        raise HTTPException(status_code=400, detail="Máximo 75 días en el futuro")
    return {"published": "false", "scheduled_publish_time": str(int(dt.timestamp()))}


@router.post("/admin/marketing/facebook/publish")
async def fb_publish(payload: PublishPayload, request: Request):
    await auth_admin(request)
    page_id, token = await _page_token()
    sched = _schedule_params(payload.scheduled_at)

    if payload.media_type == "photo":
        if payload.media_base64:
            raw = base64.b64decode(payload.media_base64.split(",")[-1])
            result = await _graph("POST", f"/{page_id}/photos", token,
                                  data={"caption": payload.message, **sched},
                                  files={"source": ("photo.jpg", raw, "image/jpeg")}, timeout=120)
        elif payload.media_url:
            result = await _graph("POST", f"/{page_id}/photos", token,
                                  data={"url": payload.media_url, "caption": payload.message, **sched})
        else:
            raise HTTPException(status_code=400, detail="Falta la foto")
    elif payload.media_type == "video":
        if payload.media_base64:
            raw = base64.b64decode(payload.media_base64.split(",")[-1])
            if len(raw) > 90 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Video demasiado grande (máx 90MB)")
            result = await _graph("POST", f"/{page_id}/videos", token,
                                  data={"description": payload.message, **sched},
                                  files={"source": ("video.mp4", raw, "video/mp4")}, timeout=300)
        elif payload.media_url:
            result = await _graph("POST", f"/{page_id}/videos", token,
                                  data={"file_url": payload.media_url, "description": payload.message, **sched},
                                  timeout=120)
        else:
            raise HTTPException(status_code=400, detail="Falta el video")
    else:
        if not payload.message.strip():
            raise HTTPException(status_code=400, detail="Escribe el mensaje del post")
        result = await _graph("POST", f"/{page_id}/feed", token,
                              data={"message": payload.message, **sched})

    await get_db().fb_posts_log.insert_one({
        "page_id": page_id, "message": payload.message[:200],
        "media_type": payload.media_type, "scheduled_at": payload.scheduled_at,
        "meta_result": result, "created_at": _now(),
    })
    return {"success": True, "result": result,
            "message": "Publicación programada 🗓️" if payload.scheduled_at else "¡Publicado en Facebook! 🎉"}


@router.get("/admin/marketing/facebook/posts")
async def fb_posts(request: Request):
    await auth_admin(request)
    page_id, token = await _page_token()
    data = await _graph("GET", f"/{page_id}/posts", token, params={
        "fields": "id,message,created_time,full_picture,permalink_url,"
                  "comments.summary(true).limit(0),likes.summary(true).limit(0),shares",
        "limit": 20,
    })
    posts = []
    for p in data.get("data", []):
        posts.append({
            "id": p["id"],
            "message": p.get("message", ""),
            "created_time": p.get("created_time"),
            "picture": p.get("full_picture"),
            "permalink": p.get("permalink_url"),
            "comments_count": ((p.get("comments") or {}).get("summary") or {}).get("total_count", 0),
            "likes_count": ((p.get("likes") or {}).get("summary") or {}).get("total_count", 0),
            "shares_count": (p.get("shares") or {}).get("count", 0),
        })
    return {"success": True, "posts": posts}


# ═══════════════════════════════════════════════════════════════════════════════
# COMMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/marketing/facebook/posts/{post_id}/comments")
async def fb_comments(post_id: str, request: Request):
    await auth_admin(request)
    page_id, token = await _page_token()
    data = await _graph("GET", f"/{post_id}/comments", token, params={
        "fields": "id,message,from{name,id},created_time,like_count,"
                  "comments{id,message,from{name,id},created_time}",
        "limit": 50, "order": "chronological",
    })
    comments = []
    for c in data.get("data", []):
        replies = [{
            "id": r["id"], "message": r.get("message", ""),
            "from_name": (r.get("from") or {}).get("name", "Usuario"),
            "from_id": (r.get("from") or {}).get("id"),
            "is_page": (r.get("from") or {}).get("id") == page_id,
            "created_time": r.get("created_time"),
        } for r in ((c.get("comments") or {}).get("data") or [])]
        comments.append({
            "id": c["id"], "message": c.get("message", ""),
            "from_name": (c.get("from") or {}).get("name", "Usuario"),
            "from_id": (c.get("from") or {}).get("id"),
            "is_page": (c.get("from") or {}).get("id") == page_id,
            "created_time": c.get("created_time"),
            "like_count": c.get("like_count", 0),
            "replies": replies,
        })
    return {"success": True, "comments": comments}


class ReplyPayload(BaseModel):
    message: str
    source: str = "human"  # human | ai


@router.post("/admin/marketing/facebook/comments/{comment_id}/reply")
async def fb_reply_comment(comment_id: str, payload: ReplyPayload, request: Request):
    admin = await auth_admin(request)
    _, token = await _page_token()
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Escribe la respuesta")
    result = await _graph("POST", f"/{comment_id}/comments", token,
                          data={"message": payload.message})
    await get_db().fb_activity.insert_one({
        "type": "comment_reply", "comment_id": comment_id, "source": payload.source,
        "by": admin.get("email", ""), "message": payload.message[:300], "created_at": _now(),
    })
    return {"success": True, "result": result}


class AISuggestPayload(BaseModel):
    text: str                 # comentario o mensaje del usuario
    author: str = "Usuario"
    context: str = ""         # texto del post, si aplica


@router.post("/admin/marketing/facebook/ai-suggest")
async def fb_ai_suggest(payload: AISuggestPayload, request: Request):
    await auth_admin(request)
    reply = await _generate_ai_reply(payload.text, payload.author, payload.context)
    if not reply:
        raise HTTPException(status_code=503, detail="La IA no está disponible en este momento")
    return {"success": True, "suggestion": reply}


# ═══════════════════════════════════════════════════════════════════════════════
# MESSENGER
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/marketing/facebook/messenger/conversations")
async def fb_messenger_conversations(request: Request):
    await auth_admin(request)
    page_id, token = await _page_token()
    data = await _graph("GET", f"/{page_id}/conversations", token, params={
        "platform": "messenger",
        "fields": "id,participants,updated_time,unread_count,"
                  "messages.limit(15){message,from,created_time}",
        "limit": 25,
    })
    convs = []
    for c in data.get("data", []):
        parts = ((c.get("participants") or {}).get("data") or [])
        other = next((p for p in parts if p.get("id") != page_id), {})
        msgs = [{
            "id": m.get("id"), "message": m.get("message", ""),
            "from_name": (m.get("from") or {}).get("name", ""),
            "is_page": (m.get("from") or {}).get("id") == page_id,
            "created_time": m.get("created_time"),
        } for m in reversed(((c.get("messages") or {}).get("data") or []))]
        convs.append({
            "id": c["id"],
            "user_name": other.get("name", "Usuario"),
            "user_id": other.get("id"),
            "updated_time": c.get("updated_time"),
            "unread_count": c.get("unread_count", 0),
            "messages": msgs,
        })
    return {"success": True, "conversations": convs}


class MessengerSendPayload(BaseModel):
    recipient_id: str
    message: str
    source: str = "human"


@router.post("/admin/marketing/facebook/messenger/send")
async def fb_messenger_send(payload: MessengerSendPayload, request: Request):
    admin = await auth_admin(request)
    page_id, token = await _page_token()
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Escribe el mensaje")
    result = await _graph("POST", f"/{page_id}/messages", token, data={
        "recipient": json.dumps({"id": payload.recipient_id}),
        "message": json.dumps({"text": payload.message}),
        "messaging_type": "RESPONSE",
    })
    await get_db().fb_activity.insert_one({
        "type": "messenger_send", "recipient_id": payload.recipient_id,
        "source": payload.source, "by": admin.get("email", ""),
        "message": payload.message[:300], "created_at": _now(),
    })
    return {"success": True, "result": result}


# ═══════════════════════════════════════════════════════════════════════════════
# AI SETTINGS (Automático / Manual)
# ═══════════════════════════════════════════════════════════════════════════════

class SettingsPayload(BaseModel):
    auto_comments: Optional[bool] = None
    auto_messages: Optional[bool] = None


@router.put("/admin/marketing/facebook/settings")
async def fb_save_settings(payload: SettingsPayload, request: Request):
    admin = await auth_admin(request)
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Nada que actualizar")
    updates["updated_at"] = _now()
    updates["updated_by"] = admin.get("email", "")
    await get_db().admin_config.update_one(
        {"type": "facebook_settings"}, {"$set": updates}, upsert=True)
    return {"success": True, "settings": await _get_settings()}


# ═══════════════════════════════════════════════════════════════════════════════
# AI COMPOSER: temas, posts bilingües, imágenes (Gemini Nano Banana)
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_llm_json(raw: str):
    """Parse JSON from an LLM reply (strips code fences / prose)."""
    if not raw:
        return None
    txt = raw.strip()
    if "```" in txt:
        parts = txt.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                txt = p
                break
    start = min([i for i in [txt.find("["), txt.find("{")] if i >= 0] or [0])
    txt = txt[start:]
    try:
        return json.loads(txt)
    except Exception:
        return None


async def _llm_json(system: str, prompt: str):
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(api_key=api_key, session_id=f"fbc-{secrets.token_hex(6)}",
                       system_message=system).with_model("anthropic", "claude-sonnet-4-5-20250929")
        result = await chat.send_message(UserMessage(text=prompt))
        return _parse_llm_json(result if isinstance(result, str) else str(result))
    except Exception as e:
        logger.error(f"FB composer LLM error: {e}")
        return None


COMPOSER_CONTEXT = """Contexto del negocio: Ross House Rentals LLC — renta y venta de casas en
Dumas, Texas y alrededores (Moore County / Texas Panhandle). Audiencia: familias trabajadoras,
comunidad hispana y anglo. Teléfono (806) 934-2018 · www.rosshouserentals.com"""


class TopicsPayload(BaseModel):
    strategy: str = ""


@router.post("/admin/marketing/facebook/ai-topics")
async def fb_ai_topics(payload: TopicsPayload, request: Request):
    await auth_admin(request)
    system = f"""Eres estratega de contenido para redes sociales de una empresa de bienes raíces.
{COMPOSER_CONTEXT}
Responde SOLO con JSON válido, sin texto extra."""
    prompt = f"""Genera 8 ideas de publicaciones para la página de Facebook.
{f'Enfoque solicitado: {payload.strategy}' if payload.strategy.strip() else 'Mezcla: destacar propiedades, tips para inquilinos, mercado local de Dumas TX, comunidad/temporada, testimonios, detrás de cámaras.'}
Formato JSON EXACTO:
[{{"emoji":"🏠","title":"título corto del tema","angle":"1 frase de por qué funciona","image_idea":"descripción en inglés de la imagen ideal para este post"}}]"""
    topics = await _llm_json(system, prompt)
    if not isinstance(topics, list) or not topics:
        raise HTTPException(status_code=503, detail="La IA no pudo generar temas, intenta de nuevo")
    return {"success": True, "topics": topics[:8]}


class ComposePayload(BaseModel):
    topic: str = ""
    property_id: Optional[str] = None
    extra: str = ""


@router.post("/admin/marketing/facebook/ai-compose")
async def fb_ai_compose(payload: ComposePayload, request: Request):
    await auth_admin(request)
    prop_block = ""
    if payload.property_id:
        from bson import ObjectId
        db = get_db()
        try:
            prop = await db.properties.find_one({"_id": ObjectId(payload.property_id)})
        except Exception:
            prop = await db.properties.find_one({"_id": payload.property_id})
        if prop:
            rent = prop.get("rent_amount") or prop.get("monthly_rent") or prop.get("rent")
            prop_block = f"""
PROPIEDAD REAL a destacar (usa estos datos, NO inventes otros):
- Dirección: {prop.get('address', '')}, {prop.get('city', 'Dumas')}, TX
- Recámaras: {prop.get('bedrooms', '?')} · Baños: {prop.get('bathrooms', '?')}
- Renta: {f'${rent}/mes' if rent else 'consultar precio por teléfono'}
- Descripción: {str(prop.get('description', ''))[:300]}
- Características: {', '.join([str(f) for f in (prop.get('features') or [])][:8])}"""
    system = f"""Eres copywriter de redes sociales para bienes raíces.
{COMPOSER_CONTEXT}
Responde SOLO con JSON válido, sin texto extra."""
    prompt = f"""Escribe 3 variaciones distintas de un post de Facebook.
Tema: {payload.topic or 'promocionar los servicios de renta de casas'}
{prop_block}
{f'Instrucciones extra: {payload.extra}' if payload.extra.strip() else ''}
REGLAS:
- BILINGÜE: primero la versión en ESPAÑOL, línea divisoria, luego la versión en INGLÉS en el mismo post.
- Tono cálido y profesional, 2-4 emojis por idioma, llamada a la acción con el teléfono y la web.
- NO inventes precios ni datos que no estén arriba.
- 4-6 hashtags relevantes al final (mezcla ES/EN, ej: #DumasTX #CasasEnRenta #HomesForRent).
Formato JSON EXACTO:
[{{"style":"nombre corto del estilo (ej: Directo, Storytelling, Urgencia)","text":"post completo bilingüe con hashtags incluidos","image_prompt":"detailed English prompt to generate the perfect photo for this post (no text/words in image)"}}]"""
    variations = await _llm_json(system, prompt)
    if not isinstance(variations, list) or not variations:
        raise HTTPException(status_code=503, detail="La IA no pudo generar el post, intenta de nuevo")
    return {"success": True, "variations": variations[:3]}


class AIImagePayload(BaseModel):
    prompt: str


@router.post("/admin/marketing/facebook/ai-image")
async def fb_ai_image(payload: AIImagePayload, request: Request):
    await auth_admin(request)
    if not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="Escribe el prompt de la imagen")
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="EMERGENT_LLM_KEY no configurada")
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(api_key=api_key, session_id=f"fbimg-{secrets.token_hex(6)}",
                       system_message="You are a professional real estate social media image generator.")
        chat.with_model("gemini", "gemini-3.1-flash-image-preview").with_params(modalities=["image", "text"])
        msg = UserMessage(text=(
            f"{payload.prompt}. Photorealistic, warm natural lighting, high quality, "
            "social-media ready, 4:3 composition. Do NOT include any text, words or logos in the image."))
        _text, images = await chat.send_message_multimodal_response(msg)
        if not images:
            raise HTTPException(status_code=503, detail="La IA no devolvió imagen, intenta de nuevo")
        img = images[0]
        mime = img.get("mime_type", "image/png")
        return {"success": True, "image_base64": f"data:{mime};base64,{img['data']}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"FB ai-image error: {e}")
        raise HTTPException(status_code=503, detail="Error generando la imagen, intenta de nuevo")


@router.get("/admin/marketing/facebook/composer-properties")
async def fb_composer_properties(request: Request):
    """Propiedades del inventario con sus fotos (para el composer)."""
    await auth_admin(request)
    db = get_db()
    site = "https://www.rosshouserentals.com"
    out = []
    async for prop in db.properties.find({}).limit(50):
        pid = str(prop["_id"])
        photos = await db.property_photos.find(
            {"property_id": pid, "is_deleted": {"$ne": True}}
        ).sort("uploaded_at", -1).to_list(10)
        urls = []
        for p in photos:
            sp = (p.get("storage_path") or "")
            if sp.startswith("ross-rentals/"):
                sp = sp[len("ross-rentals/"):]
            if sp:
                urls.append(f"{site}/api/public/property-file/{sp}")
        rent = prop.get("rent_amount") or prop.get("monthly_rent") or prop.get("rent")
        out.append({
            "id": pid,
            "label": prop.get("name") or prop.get("address") or pid,
            "address": prop.get("address", ""),
            "bedrooms": prop.get("bedrooms"),
            "bathrooms": prop.get("bathrooms"),
            "rent": rent,
            "photos": urls,
        })
    return {"success": True, "properties": out}


@router.get("/admin/marketing/facebook/scheduled")
async def fb_scheduled_posts(request: Request):
    await auth_admin(request)
    page_id, token = await _page_token()
    data = await _graph("GET", f"/{page_id}/scheduled_posts", token, params={
        "fields": "id,message,scheduled_publish_time,full_picture", "limit": 25,
    })
    posts = [{
        "id": p["id"],
        "message": p.get("message", ""),
        "scheduled_publish_time": p.get("scheduled_publish_time"),
        "picture": p.get("full_picture"),
    } for p in data.get("data", [])]
    return {"success": True, "scheduled": posts}


# ═══════════════════════════════════════════════════════════════════════════════
# AI REPLY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

FB_AI_SYSTEM = """Eres el asistente de redes sociales de Ross House Rentals LLC, empresa de
renta y venta de casas en Dumas, Texas y alrededores. Respondes comentarios y mensajes de
Facebook de la página oficial.

REGLAS ESTRICTAS:
- Responde en el MISMO idioma del usuario (español o inglés).
- Sé breve (1-3 oraciones), cálido y profesional. Usa máximo 1 emoji.
- NUNCA inventes precios, disponibilidad ni promesas. Si preguntan detalles específicos,
  invítalos a llamar al (806) 934-2018 o visitar www.rosshouserentals.com
- NUNCA pidas datos personales sensibles (SSN, tarjetas) por Facebook.
- No des consejos legales ni financieros.
- Si el comentario es ofensivo o spam, responde con neutralidad profesional o sugiere contacto directo.
- Firma implícita: hablas en nombre del equipo de Ross House Rentals."""


async def _generate_ai_reply(text: str, author: str = "Usuario", context: str = "") -> Optional[str]:
    prompt = ""
    if context:
        prompt += f"Publicación de la página: \"{context[:400]}\"\n\n"
    prompt += f"{author} escribió: \"{text[:600]}\"\n\nGenera la respuesta de la página."

    # 1) emergentintegrations (mismo cliente que Rossy — probado en producción)
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if api_key:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage
            chat = LlmChat(api_key=api_key, session_id=f"fb-{secrets.token_hex(6)}",
                           system_message=FB_AI_SYSTEM).with_model("anthropic", "claude-sonnet-4-5-20250929")
            result = await chat.send_message(UserMessage(text=prompt))
            reply = (result or "").strip() if isinstance(result, str) else str(result or "").strip()
            if reply:
                return reply[:900]
        except Exception as e:
            logger.warning(f"FB AI (emergent) failed: {e}")

    # 2) fallback: ai_brain (OpenAI directo)
    if _ai_brain is not None:
        try:
            return await _ai_brain._call_openai(FB_AI_SYSTEM, prompt)
        except Exception as e:
            logger.error(f"FB AI reply error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK (comentarios + Messenger en tiempo real)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/marketing/facebook/webhook")
async def fb_webhook_verify(request: Request):
    qp = request.query_params
    if qp.get("hub.mode") == "subscribe" and qp.get("hub.verify_token") == _verify_token():
        return PlainTextResponse(qp.get("hub.challenge") or "")
    raise HTTPException(status_code=403, detail="Verify token inválido")


@router.post("/marketing/facebook/webhook")
async def fb_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256", "")
    secret = _cfg("META_APP_SECRET")
    if secret and sig.startswith("sha256="):
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig[7:], expected):
            raise HTTPException(status_code=403, detail="Firma inválida")

    try:
        payload = json.loads(raw)
    except Exception:
        return {"ok": True}

    db = get_db()
    # idempotencia
    payload_hash = hashlib.sha256(raw).hexdigest()
    dup = await db.fb_webhook_events.find_one({"hash": payload_hash})
    if dup:
        return {"ok": True}
    await db.fb_webhook_events.insert_one(
        {"hash": payload_hash, "payload": payload, "received_at": _now()})

    try:
        await _process_webhook(payload)
    except Exception:
        logger.exception("FB webhook processing failed")
    return {"ok": True}


async def _process_webhook(payload: dict):
    db = get_db()
    page = await _get_page()
    if not page:
        return
    page_id = page["page_id"]
    settings = await _get_settings()

    for entry in payload.get("entry", []):
        # ── Comentarios (feed) ──────────────────────────────────────────────
        for change in entry.get("changes", []):
            if change.get("field") != "feed":
                continue
            v = change.get("value") or {}
            if v.get("item") != "comment" or v.get("verb") != "add":
                continue
            from_id = (v.get("from") or {}).get("id")
            from_name = (v.get("from") or {}).get("name", "Alguien")
            comment_id = v.get("comment_id")
            text = v.get("message", "")
            if not comment_id or from_id == page_id:
                continue  # nuestra propia respuesta

            # push al admin
            try:
                await send_rental_push_to_admins(
                    title=f"📘 {from_name} comentó en Facebook",
                    body=text[:80] or "(sin texto)",
                    data={"type": "fb_comment", "comment_id": comment_id})
            except Exception:
                pass

            # IA automática
            if settings["auto_comments"] and text.strip():
                already = await db.fb_ai_replies.find_one({"target_id": comment_id})
                if already:
                    continue
                reply = await _generate_ai_reply(text, from_name, v.get("post", {}).get("message", "") if isinstance(v.get("post"), dict) else "")
                if reply:
                    try:
                        _, token = await _page_token()
                        await _graph("POST", f"/{comment_id}/comments", token,
                                     data={"message": reply})
                        await db.fb_ai_replies.insert_one({
                            "target_id": comment_id, "kind": "comment",
                            "reply": reply[:400], "created_at": _now()})
                        await db.fb_activity.insert_one({
                            "type": "comment_reply", "comment_id": comment_id,
                            "source": "ai_auto", "message": reply[:300], "created_at": _now()})
                        logger.info(f"🤖 IA respondió comentario {comment_id}")
                    except Exception as e:
                        logger.error(f"AI auto-reply comment failed: {e}")

        # ── Messenger (messages) ────────────────────────────────────────────
        for msg_event in entry.get("messaging", []):
            message = msg_event.get("message") or {}
            if message.get("is_echo"):
                continue  # mensajes enviados por la página
            sender_id = (msg_event.get("sender") or {}).get("id")
            text = message.get("text", "")
            mid = message.get("mid")
            if not sender_id or sender_id == page_id or not mid:
                continue

            try:
                await send_rental_push_to_admins(
                    title="📘 Nuevo mensaje en Messenger",
                    body=text[:80] or "(adjunto)",
                    data={"type": "fb_message", "sender_id": sender_id})
            except Exception:
                pass

            if settings["auto_messages"] and text.strip():
                already = await db.fb_ai_replies.find_one({"target_id": mid})
                if already:
                    continue
                reply = await _generate_ai_reply(text, "Usuario de Messenger")
                if reply:
                    try:
                        pid, token = await _page_token()
                        await _graph("POST", f"/{pid}/messages", token, data={
                            "recipient": json.dumps({"id": sender_id}),
                            "message": json.dumps({"text": reply}),
                            "messaging_type": "RESPONSE",
                        })
                        await db.fb_ai_replies.insert_one({
                            "target_id": mid, "kind": "message",
                            "reply": reply[:400], "created_at": _now()})
                        await db.fb_activity.insert_one({
                            "type": "messenger_send", "recipient_id": sender_id,
                            "source": "ai_auto", "message": reply[:300], "created_at": _now()})
                        logger.info(f"🤖 IA respondió Messenger de {sender_id}")
                    except Exception as e:
                        logger.error(f"AI auto-reply message failed: {e}")
