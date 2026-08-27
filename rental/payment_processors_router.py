"""Public payment-processor router with hardened settlement webhooks.

The legacy implementation remains byte-for-byte in ``payment_processors_core``.
This adapter reuses every non-webhook route and replaces only Clover, Bank of
America and Helcim settlement webhooks with fail-closed handlers.

This keeps the public import path stable for existing callers while allowing the
financial-integrity boundary to stay small and independently auditable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

from . import payment_processors_core as core
from .webhook_settlement_policy import (
    bofa_webhook_can_settle,
    clover_webhook_can_settle,
    helcim_transaction_can_settle,
    helcim_webhook_may_lookup,
)

# Preserve the old module surface for all existing imports.  ``router`` is
# intentionally excluded because this module publishes the hardened composition.
for _name in dir(core):
    if _name != "router" and not _name.startswith("__"):
        globals().setdefault(_name, getattr(core, _name))

router = APIRouter()
_HARDENED_WEBHOOK_PATHS = {
    "/webhooks/clover",
    "/webhooks/bofa",
    "/webhooks/hpay",
}

# Reuse every route except the three webhook handlers replaced below.
for _route in core.router.routes:
    if getattr(_route, "path", None) not in _HARDENED_WEBHOOK_PATHS:
        router.routes.append(_route)


def __getattr__(name: str):
    """Forward legacy module attributes to the unchanged core implementation."""
    return getattr(core, name)


def _clover_signature_valid(raw: bytes, header: str, secret: str) -> bool:
    if not secret or not header:
        return False
    try:
        parts = {}
        for piece in header.split(","):
            key, value = piece.strip().split("=", 1)
            parts[key] = value
        ts = parts["t"]
        supplied = parts["v1"]
        if abs(time.time() - int(ts)) > 300:
            return False
        expected = hmac.new(
            secret.encode(), ts.encode() + b"." + raw, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, supplied)
    except (KeyError, ValueError):
        return False


@router.post("/webhooks/clover")
async def clover_webhook(request: Request):
    """Clover Hosted Checkout webhook: signed + APPROVED before rent settlement."""
    doc = await core._get_doc()
    cfg = core._active_creds(doc["processors"].get("clover", {}))
    raw = await request.body()
    secret = cfg.get("webhook_signing_secret", "")
    header = request.headers.get("Clover-Signature", "")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    # Clover sends this challenge while registering the endpoint.  It is not a
    # payment event and therefore cannot enter the settlement path.
    if isinstance(payload, dict) and payload.get("verificationCode"):
        await core.get_db().processor_webhook_events.insert_one({
            "processor": "clover",
            "type": "verification",
            "verification_code": payload["verificationCode"],
            "received_at": datetime.now(timezone.utc),
        })
        return {"received": True, "verificationCode": payload["verificationCode"]}

    verified = _clover_signature_valid(raw, header, secret)
    if secret and not verified:
        raise HTTPException(status_code=401, detail="Firma de Clover inválida")

    event_id = (
        payload.get("id")
        or payload.get("paymentId")
        or hashlib.sha256(raw).hexdigest()
    )
    db = core.get_db()
    existing = await db.processor_webhook_events.find_one({
        "event_id": event_id, "processor": "clover"
    })
    if existing:
        return {"received": True, "duplicate": True}

    data_id = payload.get("data") if isinstance(payload.get("data"), str) else None
    candidate_ids = [v for v in [
        payload.get("checkoutSessionId"),
        data_id,
        payload.get("id"),
        payload.get("paymentId"),
    ] if v]

    await db.processor_webhook_events.insert_one({
        "processor": "clover",
        "event_id": event_id,
        "type": payload.get("status", "") or "event",
        "payload": payload,
        "payload_ids": candidate_ids,
        "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })

    if clover_webhook_can_settle(
        verified=verified, status=payload.get("status")
    ):
        inserted = await db.processor_webhook_events.find_one({
            "event_id": event_id, "processor": "clover"
        })
        await core._try_complete_from_webhook(
            "clover",
            candidate_ids,
            inserted["_id"] if inserted else None,
        )
    return {"received": True}


@router.post("/webhooks/bofa")
async def bofa_webhook(request: Request):
    """BofA Secure Acceptance: valid signed ACCEPT before rent settlement."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    doc = await core._get_doc()
    cfg = core._active_creds(doc["processors"].get("bofa", {}))
    secret = cfg.get("sa_secret_key", "")

    verified = False
    if secret:
        if not form.get("signed_field_names") or not form.get("signature"):
            raise HTTPException(
                status_code=403, detail="Firma de Bank of America inválida"
            )
        expected = core._sa_sign(form, secret)
        verified = hmac.compare_digest(expected, form.get("signature", ""))
        if not verified:
            raise HTTPException(
                status_code=403, detail="Firma de Bank of America inválida"
            )

    event_id = (
        form.get("transaction_id")
        or form.get("req_transaction_uuid")
        or hashlib.sha256(json.dumps(form, sort_keys=True).encode()).hexdigest()
    )
    db = core.get_db()
    if await db.processor_webhook_events.find_one({
        "event_id": event_id, "processor": "bofa"
    }):
        return {"ok": True, "duplicate": True}

    payload_ids = [v for v in [
        form.get("req_transaction_uuid"),
        form.get("req_reference_number"),
        form.get("transaction_id"),
    ] if v]
    await db.processor_webhook_events.insert_one({
        "processor": "bofa",
        "event_id": event_id,
        "type": (form.get("decision") or "notification").lower(),
        "payload": form,
        "payload_ids": payload_ids,
        "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })

    if bofa_webhook_can_settle(
        verified=verified, decision=form.get("decision")
    ):
        inserted = await db.processor_webhook_events.find_one({
            "event_id": event_id, "processor": "bofa"
        })
        await core._try_complete_from_webhook(
            "bofa", payload_ids, inserted["_id"] if inserted else None
        )
    return {"ok": True}


def _helcim_signature_valid(
    *, raw: bytes, webhook_id: str, webhook_timestamp: str,
    webhook_signature: str, verifier_token: str,
) -> bool:
    if not all((webhook_id, webhook_timestamp, webhook_signature, verifier_token)):
        return False
    try:
        key = base64.b64decode(verifier_token)
        signed = f"{webhook_id}.{webhook_timestamp}.".encode() + raw
        expected = base64.b64encode(
            hmac.new(key, signed, hashlib.sha256).digest()
        ).decode()
        candidates = [
            part.split(",", 1)[-1]
            for part in webhook_signature.split(" ")
            if part
        ]
        return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
    except Exception:  # noqa: BLE001
        return False


async def _helcim_authoritative_transaction(
    *, api_token: str, transaction_id: str
) -> dict | None:
    """Retrieve the provider record; webhook payload alone is never payment proof."""
    if not api_token or not transaction_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{core.HELCIM_BASE}/card-transactions/{transaction_id}",
                headers={"api-token": api_token, "accept": "application/json"},
            )
        if response.status_code >= 400:
            core.logger.warning(
                "Helcim transaction lookup failed: HTTP %s", response.status_code
            )
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001
        core.logger.warning("Helcim transaction lookup failed: %s", exc)
        return None


@router.post("/webhooks/hpay")
async def helcim_webhook(request: Request):
    """Helcim webhook: verify HMAC, then verify transaction server-to-server."""
    raw = await request.body()
    wh_id = request.headers.get("webhook-id", "")
    wh_ts = request.headers.get("webhook-timestamp", "")
    wh_sig = request.headers.get("webhook-signature", "")

    doc = await core._get_doc()
    cfg = core._active_creds(doc["processors"].get("helcim", {}))
    verifier = cfg.get("webhook_verifier_token", "")
    verified = _helcim_signature_valid(
        raw=raw,
        webhook_id=wh_id,
        webhook_timestamp=wh_ts,
        webhook_signature=wh_sig,
        verifier_token=verifier,
    )
    if verifier and not verified:
        raise HTTPException(status_code=403, detail="Firma de Helcim inválida")

    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        payload = {}

    event_id = wh_id or hashlib.sha256(raw).hexdigest()
    db = core.get_db()
    if await db.processor_webhook_events.find_one({
        "event_id": event_id, "processor": "helcim"
    }):
        return {"ok": True, "duplicate": True}

    txn_id = str(payload.get("id") or "")
    await db.processor_webhook_events.insert_one({
        "processor": "helcim",
        "event_id": event_id,
        "type": payload.get("type", "notification"),
        "payload": payload,
        "payload_ids": [txn_id] if txn_id else [],
        "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })

    # A verified webhook only authorizes lookup.  Settlement requires the
    # authoritative Helcim transaction to match the local session exactly.
    if helcim_webhook_may_lookup(
        verified=verified,
        event_type=payload.get("type"),
        transaction_id=txn_id,
    ):
        session = await db.helcim_checkout_sessions.find_one({
            "transaction_id": txn_id
        })
        if session:
            transaction = await _helcim_authoritative_transaction(
                api_token=cfg.get("api_token", ""),
                transaction_id=txn_id,
            )
            amount_cents = None
            if transaction:
                try:
                    amount_cents = int(round(float(transaction.get("amount")) * 100))
                except (TypeError, ValueError):
                    amount_cents = None

            if transaction and helcim_transaction_can_settle(
                status=transaction.get("status"),
                amount_cents=amount_cents,
                expected_amount_cents=session.get("amount_cents"),
                currency=transaction.get("currency"),
                transaction_id=str(transaction.get("transactionId") or ""),
                expected_transaction_id=txn_id,
            ):
                await db.helcim_checkout_sessions.update_one(
                    {"_id": session["_id"], "status": {"$ne": "paid"}},
                    {"$set": {
                        "status": "paid",
                        "helcim_transaction": transaction,
                        "updated_at": datetime.now(timezone.utc),
                    }},
                )
                inserted = await db.processor_webhook_events.find_one({
                    "event_id": event_id, "processor": "helcim"
                })
                await core._try_complete_from_webhook(
                    "helcim",
                    [session.get("checkout_token"), session.get("_id"), txn_id],
                    inserted["_id"] if inserted else None,
                )
    return {"ok": True}
