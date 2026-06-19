"""
Rental Stripe Router
=====================

Aggregator module — keeps the public import surface `from rental.stripe_router
import router` stable for `server.py` while the actual implementation lives in
focused sub-modules under `rental.stripe_pkg/`.

Sub-modules:
    - connect_router         -> Stripe Connect (admin + owner + payouts)
    - tenant_payments_router -> Tenant rent payments (PaymentIntent + confirm)
    - admin_config_router    -> Admin Stripe config + payment methods + test
    - webhooks_router        -> Stripe webhook + admin webhook events listing
    - payment_methods_router -> Tenant saved payment methods (setup/list/delete)
    - autopay_router         -> Tenant + admin autopay endpoints

All routes preserve their original paths so no client (web admin, mobile app,
or Stripe webhook URL) needs reconfiguration.
"""
from fastapi import APIRouter

from rental.stripe_pkg.connect_router import router as _connect_router
from rental.stripe_pkg.tenant_payments_router import router as _tenant_payments_router
from rental.stripe_pkg.admin_config_router import router as _admin_config_router
from rental.stripe_pkg.webhooks_router import router as _webhooks_router
from rental.stripe_pkg.payment_methods_router import router as _payment_methods_router
from rental.stripe_pkg.autopay_router import router as _autopay_router

# Re-export shared helpers for any legacy callers that imported them directly
from rental.stripe_pkg.helpers import (  # noqa: F401
    _get_stripe_config,
    _get_or_create_stripe_customer,
)

router = APIRouter()
router.include_router(_connect_router)
router.include_router(_tenant_payments_router)
router.include_router(_admin_config_router)
router.include_router(_webhooks_router)
router.include_router(_payment_methods_router)
router.include_router(_autopay_router)
