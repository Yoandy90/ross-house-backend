"""
Rental Stripe Router
=====================

Aggregator module — keeps the public import surface `from rental.stripe_router
import router` stable while focused implementations live in `rental/stripe_pkg`.
The hardened adapter replaces only `/stripe/connect-webhook`; every other
legacy Stripe route remains composed unchanged.
"""
from fastapi import APIRouter

from rental.stripe_pkg.connect_router import router as _connect_router
from rental.stripe_pkg.tenant_payments_router import router as _tenant_payments_router
from rental.stripe_pkg.admin_config_router import router as _admin_config_router
from rental.stripe_pkg.webhooks_router import router as _webhooks_router
from rental.stripe_pkg.hardened_webhook_router import router as _hardened_webhook_router
from rental.stripe_pkg.payment_methods_router import router as _payment_methods_router
from rental.stripe_pkg.autopay_router import router as _autopay_router
from rental.stripe_pkg.reconciliation_queue_router import router as _reconciliation_queue_router
from rental.stripe_pkg.reconciliation_resolution_router import router as _reconciliation_resolution_router
from rental.stripe_pkg.reconciliation_execution_router import router as _reconciliation_execution_router
from rental.stripe_pkg.reconciliation_workflow_router import router as _reconciliation_workflow_router
from rental.stripe_pkg.reconciliation_recovery_router import router as _reconciliation_recovery_router

from rental.stripe_pkg.helpers import (  # noqa: F401
    _get_stripe_config,
    _get_or_create_stripe_customer,
)


def _webhooks_without_connect() -> APIRouter:
    filtered = APIRouter()
    for route in _webhooks_router.routes:
        if getattr(route, "path", "") == "/stripe/connect-webhook":
            continue
        filtered.routes.append(route)
    return filtered


router = APIRouter()
router.include_router(_connect_router)
router.include_router(_tenant_payments_router)
router.include_router(_admin_config_router)
router.include_router(_webhooks_without_connect())
router.include_router(_hardened_webhook_router)
router.include_router(_payment_methods_router)
router.include_router(_autopay_router)
router.include_router(_reconciliation_queue_router)
router.include_router(_reconciliation_resolution_router)
router.include_router(_reconciliation_execution_router)
router.include_router(_reconciliation_workflow_router)
router.include_router(_reconciliation_recovery_router)
