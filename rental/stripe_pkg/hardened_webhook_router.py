"""Hardened Stripe connect-webhook adapter for native rent PaymentIntents.

All non-rent/non-payment-intent events delegate to the existing audited legacy
handler. ``payment_intent.succeeded`` is intercepted so rental settlement can
only update the canonical monthly invoice through ``rent_reconciliation``.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db
from rental.stripe_pkg.helpers import _get_stripe_config
from rental.stripe_pkg.rent_reconciliation import (
    reconcile_succeeded_rent_payment,
    stripe_payment_identity_query,
)
from rental.stripe_pkg import webhooks_router as legacy_webhooks

router = APIRouter()


async def _verified_event(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    config = await _get_stripe_config()
    secrets = [s for s in (
        os.getenv("STRIPE_WEBHOOK_SECRET", ""),
        config.get("stripe_webhook_secret", ""),
    ) if s]
    if not secrets:
        raise HTTPException(status_code=503, detail="Webhook no disponible")

    import stripe
    stripe.api_key = config.get("stripe_secret_key", "")
    event = None
    for secret in secrets:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            break
        except ValueError:
            raise HTTPException(status_code=400, detail="Payload inválido")
        except Exception:
            continue
    if event is None:
        raise HTTPException(status_code=400, detail="Firma de webhook inválida")
    return stripe, event


def _event_parts(event):
    event_type = event.get("type", "") if isinstance(event, dict) else event.type
    event_data = (
        event.get("data", {}).get("object", {})
        if isinstance(event, dict)
        else event.data.object
    )
    return event_type, event_data


async def _capture_three_ds(stripe, pi_id: str, amount: float):
    """Best-effort 3DS evidence; never authorizes settlement by itself."""
    try:
        pi = stripe.PaymentIntent.retrieve(pi_id, expand=["latest_charge"])
        charge = pi.latest_charge
        card = (
            charge.payment_method_details.card
            if charge
            and charge.payment_method_details
            and charge.payment_method_details.type == "card"
            else None
        )
        tds = getattr(card, "three_d_secure", None) if card else None
        tds_result = getattr(tds, "result", None) if tds else None
        tds_ok = tds_result in ("authenticated", "attempt_acknowledged")
        evidence = {
            "payment_intent_id": pi_id,
            "charge_id": charge.id if charge else None,
            "requested": True,
            "authenticated": tds_ok,
            "result": tds_result if tds else "not_supported_by_card",
            "result_reason": getattr(tds, "result_reason", None) if tds else None,
            "version": getattr(tds, "version", None) if tds else None,
            "authentication_flow": getattr(tds, "authentication_flow", None) if tds else None,
            "liability_shift": "issuer" if tds_ok else "requested_not_supported",
            "amount": amount,
            "recorded_at": datetime.utcnow(),
        }
        await get_db().three_ds_evidence.insert_one(dict(evidence))
        return evidence
    except Exception as exc:
        logging.warning("Stripe 3DS evidence capture failed for %s: %s", pi_id, exc)
        return None


async def _log_payment_event(event, event_data, reconciliation_status: str):
    try:
        await get_db().stripe_webhook_events.insert_one({
            "event_id": event.get("id", "") if isinstance(event, dict) else event.id,
            "event_type": "payment_intent.succeeded",
            "account_id": (
                event_data.get("id", "")
                if isinstance(event_data, dict)
                else getattr(event_data, "id", "")
            ),
            "processed_at": datetime.utcnow(),
            "livemode": (
                event.get("livemode", False)
                if isinstance(event, dict)
                else getattr(event, "livemode", False)
            ),
            "reconciliation_status": reconciliation_status,
        })
    except Exception as exc:
        logging.warning("Could not log Stripe reconciliation event: %s", exc)


@router.post("/stripe/connect-webhook")
async def stripe_connect_webhook(request: Request):
    stripe, event = await _verified_event(request)
    event_type, event_data = _event_parts(event)

    # Preserve every legacy event behavior except the rent financial writer.
    if event_type != "payment_intent.succeeded":
        return await legacy_webhooks.stripe_connect_webhook(request)

    pi_id = event_data.get("id", "") if isinstance(event_data, dict) else event_data.id
    raw_amount = event_data.get("amount", 0) if isinstance(event_data, dict) else getattr(event_data, "amount", 0)
    amount_cents = int(raw_amount or 0)
    amount = amount_cents / 100
    metadata = (
        event_data.get("metadata", {})
        if isinstance(event_data, dict)
        else getattr(event_data, "metadata", {}) or {}
    )

    if not pi_id or amount_cents <= 0:
        await _log_payment_event(event, event_data, "invalid")
        return {"received": True}

    # Avoid duplicate non-financial evidence work on normal Stripe retries.
    existing = await get_db().rental_payments.find_one(stripe_payment_identity_query(pi_id))
    if existing is not None:
        await _log_payment_event(event, event_data, "duplicate")
        return {"received": True}

    three_ds_evidence = await _capture_three_ds(stripe, pi_id, amount)

    try:
        result = await reconcile_succeeded_rent_payment(
            get_db(),
            payment_intent_id=pi_id,
            amount_cents=amount_cents,
            metadata=metadata,
            three_ds_evidence=three_ds_evidence,
            now=datetime.utcnow(),
        )
    except Exception as exc:
        # A DB/infrastructure failure must produce 5xx so Stripe retries instead
        # of silently losing a succeeded financial event.
        logging.exception("Stripe rent reconciliation infrastructure failure")
        raise HTTPException(status_code=503, detail="Webhook processing unavailable") from exc

    status = str(result.get("status") or "unknown")
    await _log_payment_event(event, event_data, status)
    if result.get("settled"):
        logging.info("Stripe rent PI %s reconciled: %s", pi_id, status)
    else:
        logging.warning("Stripe rent PI %s not settled: %s", pi_id, status)

    return {"received": True}
