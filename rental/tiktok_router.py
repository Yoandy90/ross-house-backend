"""
TikTok Content Posting Router
═══════════════════════════════════════════════════════════════════════════════
Connect the business TikTok account via OAuth (Login Kit) and publish videos
using the Content Posting API (Direct Post).

Env vars required (from https://developers.tiktok.com → Manage apps → Credentials):
  TIKTOK_CLIENT_KEY      - public app identifier
  TIKTOK_CLIENT_SECRET   - server-side secret
  TIKTOK_REDIRECT_URI    - must EXACTLY match Login Kit callback registered on TikTok
  TIKTOK_WEB_REDIRECT    - where to send the admin after connecting (default: prod admin page)

Flow: POST /connect → TikTok authorize → GET /public/tiktok/callback →
      token exchange + user info → stored in `tiktok_account` collection.
Publish: creator_info/query → video/init (PULL_FROM_URL) → status/fetch polling.
"""
import os
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from rental.shared import auth_admin, get_db

logger = logging.getLogger("tiktok")
router = APIRouter(tags=["TikTok"])

TIKTOK_CLIENT_KEY = (os.environ.get("TIKTOK_CLIENT_KEY") or "").strip()
TIKTOK_CLIENT_SECRET = (os.environ.get("TIKTOK_CLIENT_SECRET") or "").strip()
TIKTOK_REDIRECT_URI = (os.environ.get("TIKTOK_REDIRECT_URI") or "").strip()
TIKTOK_WEB_REDIRECT = (os.environ.get("TIKTOK_WEB_REDIRECT")
                       or "https://www.rosshouserentals.com/admin/marketing/tiktok").strip()

SCOPES = "user.info.basic,video.publish,video.upload"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
INBOX_VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_FETCH_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"


def _configured() -> bool:
    return bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and TIKTOK_REDIRECT_URI)


def _now():
    return datetime.now(timezone.utc)


async def _get_account():
    """Single business account model — return the connected account doc or None."""
    return await get_db().tiktok_account.find_one({}, sort=[("updated_at", -1)])


async def _save_tokens(open_id: str, data: dict, extra: Optional[dict] = None):
    now = _now()
    doc = {
        "open_id": open_id,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "access_expires_at": now + timedelta(seconds=int(data.get("expires_in", 86400))),
        "refresh_expires_at": now + timedelta(seconds=int(data.get("refresh_expires_in", 31536000))),
        "scopes": data.get("scope", ""),
        "updated_at": now,
    }
    if extra:
        doc.update(extra)
    await get_db().tiktok_account.update_one({"open_id": open_id}, {"$set": doc}, upsert=True)


async def _fresh_access_token() -> str:
    """Return a valid access token, refreshing if it expires in <5 minutes."""
    acct = await _get_account()
    if not acct:
        raise HTTPException(status_code=400, detail="No hay cuenta TikTok conectada")
    exp = acct.get("access_expires_at")
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp and exp > _now() + timedelta(minutes=5):
        return acct["access_token"]
    # Refresh
    if not acct.get("refresh_token"):
        raise HTTPException(status_code=401, detail="Token expirado. Reconecta la cuenta TikTok.")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(TOKEN_URL, data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": acct["refresh_token"],
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    data = r.json()
    if r.status_code != 200 or "access_token" not in data:
        logger.error(f"TikTok refresh failed: {data}")
        raise HTTPException(status_code=401, detail="No se pudo renovar el token. Reconecta la cuenta TikTok.")
    await _save_tokens(data.get("open_id", acct["open_id"]), data)
    return data["access_token"]


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS / CONNECT / CALLBACK / DISCONNECT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/marketing/tiktok/status")
async def tiktok_status(request: Request):
    await auth_admin(request)
    acct = await _get_account()
    posts_count = await get_db().tiktok_posts.count_documents({})
    out = {
        "configured": _configured(),
        "redirect_uri": TIKTOK_REDIRECT_URI or "(configura TIKTOK_REDIRECT_URI en el backend)",
        "connected": bool(acct),
        "account": None,
        "posts_count": posts_count,
    }
    if acct:
        exp = acct.get("access_expires_at")
        out["account"] = {
            "open_id": acct["open_id"],
            "display_name": acct.get("display_name", ""),
            "username": acct.get("username", ""),
            "avatar_url": acct.get("avatar_url", ""),
            "scopes": acct.get("scopes", ""),
            "access_expires_at": exp.isoformat() if exp else None,
            "connected_at": acct.get("connected_at").isoformat() if acct.get("connected_at") else None,
        }
    return out


@router.post("/admin/marketing/tiktok/connect")
async def tiktok_connect(request: Request):
    await auth_admin(request)
    if not _configured():
        raise HTTPException(status_code=400, detail="Faltan TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REDIRECT_URI en el backend")
    state = secrets.token_urlsafe(32)
    await get_db().tiktok_oauth_states.insert_one({"state": state, "created_at": _now()})
    from urllib.parse import urlencode
    params = urlencode({
        "client_key": TIKTOK_CLIENT_KEY,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": TIKTOK_REDIRECT_URI,
        "state": state,
    })
    return {"authorize_url": f"{AUTH_URL}?{params}"}


@router.get("/public/tiktok/callback")
async def tiktok_callback(request: Request):
    """OAuth callback hit directly by TikTok — public, validated via state."""
    q = request.query_params
    code, state = q.get("code"), q.get("state")
    error = q.get("error")
    if error:
        return RedirectResponse(f"{TIKTOK_WEB_REDIRECT}?error={error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan code/state")
    st = await get_db().tiktok_oauth_states.find_one_and_delete({"state": state})
    if not st:
        raise HTTPException(status_code=400, detail="State inválido o expirado")
    created = st["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if _now() - created > timedelta(minutes=15):
        raise HTTPException(status_code=400, detail="State expirado, intenta de nuevo")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(TOKEN_URL, data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": TIKTOK_REDIRECT_URI,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = r.json()
        if r.status_code != 200 or "access_token" not in data:
            logger.error(f"TikTok token exchange failed: {data}")
            return RedirectResponse(f"{TIKTOK_WEB_REDIRECT}?error=token_exchange_failed")

        # Fetch basic profile for display
        extra = {"connected_at": _now()}
        try:
            ui = await client.get(
                USER_INFO_URL,
                params={"fields": "open_id,avatar_url,display_name,username"},
                headers={"Authorization": f"Bearer {data['access_token']}"},
            )
            u = (ui.json().get("data") or {}).get("user") or {}
            extra.update({
                "display_name": u.get("display_name", ""),
                "username": u.get("username", ""),
                "avatar_url": u.get("avatar_url", ""),
            })
        except Exception as e:
            logger.warning(f"TikTok user.info fetch failed: {e}")

    await _save_tokens(data["open_id"], data, extra)
    return RedirectResponse(f"{TIKTOK_WEB_REDIRECT}?connected=1")


@router.delete("/admin/marketing/tiktok/account")
async def tiktok_disconnect(request: Request):
    await auth_admin(request)
    acct = await _get_account()
    if not acct:
        return {"success": True, "message": "No había cuenta conectada"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(REVOKE_URL, data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "token": acct["access_token"],
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    except Exception as e:
        logger.warning(f"TikTok revoke failed (continuing): {e}")
    await get_db().tiktok_account.delete_many({})
    return {"success": True, "message": "Cuenta TikTok desconectada"}


# ═══════════════════════════════════════════════════════════════════════════════
# CREATOR INFO / PUBLISH / STATUS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/marketing/tiktok/creator-info")
async def tiktok_creator_info(request: Request):
    """Must be called right before showing the publish form (TikTok UX rule)."""
    await auth_admin(request)
    token = await _fresh_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(CREATOR_INFO_URL, json={}, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    data = r.json()
    if r.status_code != 200 or (data.get("error", {}).get("code") not in (None, "ok")):
        raise HTTPException(status_code=400, detail=data.get("error", {}).get("message", "Error consultando creator info"))
    return data.get("data", {})


class PublishPayload(BaseModel):
    title: str = ""
    video_url: str
    privacy_level: str = "SELF_ONLY"  # from creator_info privacy_level_options
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    video_cover_timestamp_ms: int = 0
    mode: str = "direct"  # direct = video.publish | draft = video.upload (inbox)


@router.post("/admin/marketing/tiktok/publish")
async def tiktok_publish(payload: PublishPayload, request: Request):
    user = await auth_admin(request)
    if not payload.video_url.strip().startswith("https://"):
        raise HTTPException(status_code=422, detail="URL HTTPS del video es requerida")
    if payload.mode == "direct" and not payload.title.strip():
        raise HTTPException(status_code=422, detail="El título es requerido para publicación directa")
    token = await _fresh_access_token()
    video_url = payload.video_url.strip()

    post_info = {
        "title": payload.title.strip(),
        "privacy_level": payload.privacy_level,
        "disable_comment": payload.disable_comment,
        "disable_duet": payload.disable_duet,
        "disable_stitch": payload.disable_stitch,
        "video_cover_timestamp_ms": payload.video_cover_timestamp_ms,
    }
    init_url = INBOX_VIDEO_INIT_URL if payload.mode == "draft" else VIDEO_INIT_URL
    used_source = "PULL_FROM_URL"

    async with httpx.AsyncClient(timeout=120) as client:
        body = {"source_info": {"source": "PULL_FROM_URL", "video_url": video_url}}
        if payload.mode == "direct":
            body["post_info"] = post_info
        r = await client.post(init_url, json=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        data = r.json()
        err = data.get("error", {})

        # Fallback: if domain is not verified for pull_by_url, download the
        # video server-side and push it via FILE_UPLOAD (no verification needed).
        if err.get("code") == "url_ownership_unverified":
            logger.info("TikTok pull_by_url rejected (domain unverified) — falling back to FILE_UPLOAD")
            vid = await client.get(video_url, follow_redirects=True)
            if vid.status_code != 200 or not vid.content:
                raise HTTPException(status_code=400, detail="No se pudo descargar el video para subirlo a TikTok")
            content = vid.content
            size = len(content)
            if size > 64 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Video >64MB: usa un dominio verificado (pull_by_url)")
            body = {"source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            }}
            if payload.mode == "direct":
                body["post_info"] = post_info
            r = await client.post(init_url, json=body, headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            data = r.json()
            err = data.get("error", {})
            if r.status_code == 200 and err.get("code") in (None, "ok"):
                upload_url = (data.get("data") or {}).get("upload_url")
                up = await client.put(upload_url, content=content, headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                    "Content-Range": f"bytes 0-{size-1}/{size}",
                })
                if up.status_code not in (200, 201, 206):
                    logger.error(f"TikTok chunk upload failed: {up.status_code} {up.text[:300]}")
                    raise HTTPException(status_code=400, detail="Error subiendo el archivo de video a TikTok")
                used_source = "FILE_UPLOAD"

    if err.get("code") not in (None, "ok"):
        logger.error(f"TikTok publish failed: {data}")
        detail = f"[{err.get('code')}] {err.get('message') or 'Error publicando en TikTok'}"
        raise HTTPException(status_code=400, detail=detail)

    publish_id = (data.get("data") or {}).get("publish_id")
    await get_db().tiktok_posts.insert_one({
        "publish_id": publish_id,
        "title": payload.title.strip(),
        "video_url": video_url,
        "privacy_level": payload.privacy_level if payload.mode == "direct" else None,
        "mode": payload.mode,
        "source": used_source,
        "status": "PROCESSING_DOWNLOAD" if used_source == "PULL_FROM_URL" else "PROCESSING_UPLOAD",
        "created_by": user.get("email", "admin"),
        "created_at": _now(),
        "updated_at": _now(),
    })
    return {"success": True, "publish_id": publish_id, "mode": payload.mode, "source": used_source}


@router.get("/admin/marketing/tiktok/posts")
async def tiktok_posts(request: Request):
    await auth_admin(request)
    docs = await get_db().tiktok_posts.find({}).sort("created_at", -1).limit(50).to_list(None)
    posts = []
    for d in docs:
        posts.append({
            "publish_id": d.get("publish_id"),
            "title": d.get("title"),
            "privacy_level": d.get("privacy_level"),
            "mode": d.get("mode", "direct"),
            "status": d.get("status"),
            "fail_reason": d.get("fail_reason"),
            "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
        })
    return {"posts": posts}


@router.get("/admin/marketing/tiktok/posts/{publish_id}/status")
async def tiktok_post_status(publish_id: str, request: Request):
    await auth_admin(request)
    token = await _fresh_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(STATUS_FETCH_URL, json={"publish_id": publish_id}, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    data = r.json()
    d = data.get("data") or {}
    status = d.get("status")
    if status:
        await get_db().tiktok_posts.update_one(
            {"publish_id": publish_id},
            {"$set": {"status": status, "fail_reason": d.get("fail_reason"), "updated_at": _now()}})
    return {"status": status, "fail_reason": d.get("fail_reason"), "raw": d}
