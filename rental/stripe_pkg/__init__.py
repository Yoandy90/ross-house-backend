"""Stripe routers package — refactored from the legacy 1268-line stripe_router.py.

Each sub-module is self-contained and registers its own routes on its own
APIRouter instance. The top-level rental.stripe_router module aggregates all
of these into a single router so server.py wiring stays unchanged.

Structure:
  - helpers.py            -> shared helpers (_get_stripe_config, _get_or_create_stripe_customer)
  - connect_router.py     -> Stripe Connect (admin + owner + payouts)
  - tenant_payments_router.py -> Tenant create/confirm rent payment
  - admin_config_router.py    -> Admin Stripe config + payment methods + test connection
  - webhooks_router.py        -> Stripe webhook + admin webhook events listing
  - payment_methods_router.py -> Tenant saved payment methods (setup/list/delete)
  - autopay_router.py         -> Tenant + admin autopay endpoints
"""
