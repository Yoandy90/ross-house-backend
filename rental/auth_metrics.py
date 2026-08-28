"""C1 Observability — contadores diarios agregados de auth/refresh.

Colección: auth_metrics_daily (1 doc por día UTC, $inc atómico).
REGLAS:
- NUNCA tokens, hashes, PII, IPs ni user_ids: solo contadores agregados.
- bump() es fire-and-forget: jamás rompe el flujo de auth.
- GET /admin/auth-metrics: lectura admin-only (últimos 14 días).

Métricas:
  legacy_fallback_used      auth_admin resolvió por user_sessions (legacy raw token)
  sidless_token_accepted    JWT sin sid aceptado (ventana de gracia P1B-5)
  sidless_token_rejected    JWT sin sid rechazado (REQUIRE_SESSION_SID=true)
  refresh_bootstrap_ok      bootstrap exitoso (primer refresh de la sesión)
  refresh_rotate_ok         rotación normal exitosa
  refresh_grace_served      respuesta idempotente dentro del grace window
  refresh_denied            401 genérico (hash desconocido/sesión inválida/DENY)
  refresh_reuse_detected    reuse fuera de grace ⇒ familia revocada
  refresh_config_error      derivación refresh no disponible por config inválida
  unexpected_401            401 en sesión con sid válido no clasificado (candidato a bug)
"""
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request

from .shared import get_db, auth_admin
from .tenant_dashboard_security_router import router as tenant_dashboard_security_router

logger = logging.getLogger("auth_metrics")
router = APIRouter(tags=["observability"])

# Temporary compatibility shim: server.py mounts auth_metrics_router before
# properties_router and tenant_router. Flattening the secure dashboard route
# here makes GET /tenant/dashboard resolve to the canonical actor-bound handler
# first without changing the public URL. Remove this shim when tenant_router is
# decomposed and the historical dashboard handler is deleted.
router.routes.extend(tenant_dashboard_security_router.routes)

VALID_METRICS = {
    "legacy_fallback_used", "sidless_token_accepted", "sidless_token_rejected",
    "refresh_bootstrap_ok", "refresh_rotate_ok", "refresh_grace_served",
    "refresh_denied", "refresh_reuse_detected", "refresh_config_error",
    "unexpected_401",
}


async def bump(metric: str) -> None:
    """Incrementa el contador diario. Nunca lanza (best-effort)."""
    if metric not in VALID_METRICS:
        return
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await get_db().auth_metrics_daily.update_one(
            {"day": day},
            {"$inc": {metric: 1}, "$setOnInsert": {"day": day}},
            upsert=True)
    except Exception as e:  # jamás romper auth por métricas
        logger.warning("bump(%s) error: %s", metric, e)


@router.get("/admin/auth-metrics")
async def get_auth_metrics(request: Request, days: int = 14):
    """Contadores diarios (agregados, sin PII) — para decidir Phase C."""
    await auth_admin(request)
    days = max(1, min(days, 60))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    cur = get_db().auth_metrics_daily.find({"day": {"$gte": since}}).sort("day", -1)
    rows, totals = [], {m: 0 for m in VALID_METRICS}
    async for d in cur:
        rows.append({k: v for k, v in d.items() if k != "_id"})
        for m in VALID_METRICS:
            totals[m] += int(d.get(m, 0) or 0)
    return {"success": True, "days": days, "daily": rows, "totals": totals}
