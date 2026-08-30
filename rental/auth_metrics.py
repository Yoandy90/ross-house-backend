"""C1 Observability — contadores diarios agregados de auth/refresh.

Colección: auth_metrics_daily (1 doc por día UTC, $inc atómico).
REGLAS:
- NUNCA tokens, hashes, PII, IPs ni user_ids: solo contadores agregados.
- bump() es fire-and-forget: jamás rompe el flujo de auth.
- GET /admin/auth-metrics: lectura admin-only (últimos 14 días).
"""
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request

from .shared import get_db, auth_admin
from .tenant_integrity import ensure_tenant_identity_indexes
from .tenant_login_security_router import router as tenant_login_security_router
from .tenant_projection_security_router import router as tenant_projection_security_router
from .tenant_dashboard_security_router import router as tenant_dashboard_security_router
from .tenant_receipt_security_router import router as tenant_receipt_security_router
from .section8_security_router import router as section8_security_router
from .maintenance_security_router import router as maintenance_security_router
from .maintenance_ownership_security_router import router as maintenance_ownership_security_router
from .maintenance_dispatch_security_router import router as maintenance_dispatch_security_router
from .property_visibility_security_router import router as property_visibility_security_router
from .property_archival_security_router import router as property_archival_security_router
from .property_lifecycle_security_router import router as property_lifecycle_security_router
from .unit_topology_security_router import router as unit_topology_security_router
from .admin_contract_mutation_security_router import router as admin_contract_mutation_security_router
from .lease_lifecycle_state_guard_router import router as lease_lifecycle_state_guard_router
from .lease_lifecycle_security_router import router as lease_lifecycle_security_router
from .lease_lifecycle_recovery_router import router as lease_lifecycle_recovery_router
from .lease_creation_security_router import router as lease_creation_security_router
from .lease_renewal_security_router import router as lease_renewal_security_router
from .lease_renewal_notification_security_router import (
    router as lease_renewal_notification_security_router,
    ensure_indexes as ensure_lease_renewal_notification_indexes,
)

logger = logging.getLogger("auth_metrics")
router = APIRouter(tags=["observability"])

# Temporary compatibility shim: server.py mounts auth_metrics_router before
# properties/contracts/tenant/service-provider routers. Canonical security
# routes therefore win FastAPI first-match resolution without changing public
# URLs while the historical endpoints remain available for compatibility.
router.routes.extend(tenant_login_security_router.routes)
router.routes.extend(tenant_projection_security_router.routes)
router.routes.extend(tenant_dashboard_security_router.routes)
router.routes.extend(tenant_receipt_security_router.routes)
router.routes.extend(section8_security_router.routes)
router.routes.extend(maintenance_security_router.routes)
router.routes.extend(maintenance_ownership_security_router.routes)
router.routes.extend(maintenance_dispatch_security_router.routes)
router.routes.extend(property_visibility_security_router.routes)
router.routes.extend(property_archival_security_router.routes)
router.routes.extend(property_lifecycle_security_router.routes)
router.routes.extend(unit_topology_security_router.routes)
router.routes.extend(admin_contract_mutation_security_router.routes)
router.routes.extend(lease_creation_security_router.routes)
router.routes.extend(lease_lifecycle_state_guard_router.routes)
router.routes.extend(lease_lifecycle_security_router.routes)
router.routes.extend(lease_lifecycle_recovery_router.routes)
# Notification approve must precede the generic renewal approve route so the
# durable outbox intent is guaranteed without changing the public endpoint.
router.routes.extend(lease_renewal_notification_security_router.routes)
router.routes.extend(lease_renewal_security_router.routes)

VALID_METRICS = {
    "legacy_fallback_used", "sidless_token_accepted", "sidless_token_rejected",
    "refresh_bootstrap_ok", "refresh_rotate_ok", "refresh_grace_served",
    "refresh_denied", "refresh_reuse_detected", "refresh_config_error",
    "unexpected_401",
}


@router.on_event("startup")
async def _ensure_tenant_identity_indexes() -> None:
    try:
        await ensure_tenant_identity_indexes()
        logger.info("tenant identity lookup indexes ready")
    except Exception as exc:
        # Index availability improves performance but must never prevent the API
        # from starting. Runtime identity resolution still fails closed.
        logger.warning("tenant identity indexes deferred: %s", exc)
    try:
        await ensure_lease_renewal_notification_indexes(get_db())
        logger.info("lease renewal notification outbox indexes ready")
    except Exception as exc:
        logger.warning("lease renewal notification indexes deferred: %s", exc)


async def bump(metric: str) -> None:
    if metric not in VALID_METRICS:
        return
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await get_db().auth_metrics_daily.update_one(
            {"day": day}, {"$inc": {metric: 1}, "$setOnInsert": {"day": day}}, upsert=True
        )
    except Exception as e:
        logger.warning("bump(%s) error: %s", metric, e)


@router.get("/admin/auth-metrics")
async def get_auth_metrics(request: Request, days: int = 14):
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
