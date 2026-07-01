"""
Cloudflare Turnstile verification helper.

Usage as a FastAPI dependency:

    from rental.turnstile_helper import verify_turnstile

    @router.post('/public/something')
    async def something(request: Request, _: bool = Depends(verify_turnstile)):
        ...

Or call directly inside an endpoint:

    from rental.turnstile_helper import verify_turnstile_token

    body = await request.json()
    await verify_turnstile_token(body.get('captcha_token'), request)

Env vars (set in backend/.env):
    TURNSTILE_SECRET_KEY     — Cloudflare secret. Defaults to the always-pass test key.
    TURNSTILE_ENABLED        — "0" disables verification (dev shortcut). Defaults to "1".

The site-key is configured on the frontend (NEXT_PUBLIC_TURNSTILE_SITE_KEY).
"""
import os
import logging
import httpx
from fastapi import Request, HTTPException, Header
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Cloudflare always-pass test secret (returns success for any token).
# Replace with the real secret in production via env var.
_DEFAULT_TEST_SECRET = "1x0000000000000000000000000000000AA"

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _is_enabled() -> bool:
    val = os.getenv("TURNSTILE_ENABLED", "1").strip()
    return val not in ("0", "false", "no", "off", "")


async def verify_turnstile_token(token: str | None, request: Request, optional: bool = False) -> bool:
    """Verify a Turnstile token against Cloudflare. Raises HTTPException on failure.

    Args:
        token: The Turnstile token from the client.
        request: Current FastAPI request (used for remote IP).
        optional: If True and token is empty/None, allow the request through.
                  Use this for endpoints called by BOTH the mobile app (which
                  cannot render a Turnstile widget) AND the web frontend.
    """
    if not _is_enabled():
        return True

    if not token:
        if optional:
            return True
        raise HTTPException(status_code=400, detail="Captcha requerido")

    secret = os.getenv("TURNSTILE_SECRET_KEY", _DEFAULT_TEST_SECRET).strip() or _DEFAULT_TEST_SECRET
    client_ip = request.client.host if request.client else None

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            data = {"secret": secret, "response": token}
            if client_ip:
                data["remoteip"] = client_ip
            resp = await client.post(SITEVERIFY_URL, data=data)
            payload = resp.json()
    except Exception as e:
        logger.exception("Turnstile verification request failed")
        raise HTTPException(status_code=503, detail="Captcha temporalmente no disponible") from e

    if not payload.get("success"):
        codes = payload.get("error-codes", [])
        logger.warning(f"Turnstile rejected token (ip={client_ip}, codes={codes})")
        # Cloudflare codes reference:
        #   timeout-or-duplicate / invalid-input-response / bad-request etc.
        raise HTTPException(status_code=400, detail="Captcha inválido. Recarga la página e intenta de nuevo.")

    return True


async def verify_turnstile(
    request: Request,
    x_turnstile_token: str | None = Header(None, alias="X-Turnstile-Token"),
) -> bool:
    """FastAPI dependency form. Reads the token from the X-Turnstile-Token header
    OR falls back to a `captcha_token` field in the JSON body (for clients that
    don't easily set custom headers).
    """
    token = x_turnstile_token

    if not token:
        # Best-effort body inspection (does not consume the body for downstream
        # handlers because we cache it on request.state).
        try:
            cached = getattr(request.state, "_cached_body", None)
            if cached is None:
                raw = await request.body()
                request.state._cached_body = raw
                cached = raw
            if cached:
                import json
                body = json.loads(cached)
                if isinstance(body, dict):
                    token = body.get("captcha_token") or body.get("cf_turnstile_token")
        except Exception:
            pass

    return await verify_turnstile_token(token, request)
