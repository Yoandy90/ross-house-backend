"""Public payment-processor router with hardened financial-integrity boundaries.

The legacy implementation remains byte-for-byte in ``payment_processors_core``.
This adapter reuses every non-overridden route and replaces only the small set
of payment paths that need fail-closed behavior.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from . import payment_processors_core as core
from .rent_charge_policy import (
    preview_period_rent_charge,
    resolve_period_rent_charge,
)
from .webhook_settlement_policy import (
    bofa_webhook_can_settle,
    clover_webhook_can_settle,
    helcim_transaction_can_settle,
    helcim_webhook_may_lookup,
)

# Preserve the old module surface for all existing imports. ``router`` is
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
_OVERRIDDEN_PATHS = _HARDENED_WEBHOOK_PATHS | {
    "/tenant/create-checkout-payment",
}

# Reuse every core route except the small set replaced in this adapter.
for _route in core.router.routes:
    if getattr(_route, "path", None) not in _OVERRIDDEN_PATHS:
        router.routes.append(_route)


def __getattr__(name: str):
    """Forward legacy module attributes to the unchanged core implementation."""
    return getattr(core, name)


def _hosted_checkout_claim_id(contract_id, year: int, month: int) -> ObjectId:
    """Deterministic Mongo identity for one hosted rent checkout per period."""
    raw = f"hosted-rent:{contract_id}:{int(year):04d}-{int(month):02d}".encode()
    return ObjectId(hashlib.sha256(raw).hexdigest()[:24])


def _hosted_checkout_batch_claim_id(contract_id, periods: list[str]) -> ObjectId:
    if len(periods) == 1:
        year, month = map(int, periods[0].split("-"))
        return _hosted_checkout_claim_id(contract_id, year, month)
    raw = f"hosted-rent:{contract_id}:{','.join(periods)}".encode()
    return ObjectId(hashlib.sha256(raw).hexdigest()[:24])


def _requested_periods(data: dict, now: datetime) -> list[str]:
    raw = data.get("periods")
    if raw is None:
        raw = [now.strftime("%Y-%m")]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        raise HTTPException(status_code=400, detail="Selecciona entre 1 y 12 meses")
    periods = []
    for value in raw:
        value = str(value or "")
        try:
            parsed = datetime.strptime(value, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Período de renta inválido") from exc
        if parsed < now.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
            raise HTTPException(status_code=400, detail="No se puede seleccionar un mes anterior")
        periods.append(parsed.strftime("%Y-%m"))
    if len(set(periods)) != len(periods):
        raise HTTPException(status_code=400, detail="No puedes seleccionar el mismo mes dos veces")
    periods = sorted(periods)
    for previous, current in zip(periods, periods[1:]):
        previous_dt = datetime.strptime(previous, "%Y-%m")
        expected = previous_dt.replace(
            year=previous_dt.year + (1 if previous_dt.month == 12 else 0),
            month=1 if previous_dt.month == 12 else previous_dt.month + 1,
        ).strftime("%Y-%m")
        if current != expected:
            raise HTTPException(
                status_code=400,
                detail="Los meses adelantados deben ser consecutivos",
            )
    return periods


async def _payable_period_previews(db, contract: dict, now: datetime) -> list[dict]:
    """Return the next lease months without creating accounting records."""
    from .rent_payment_cron import _parse_contract_date

    cursor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_dt = await _parse_contract_date(contract.get("end_date"))
    result = []
    for _ in range(12):
        if end_dt and cursor > end_dt:
            break
        try:
            charge = await preview_period_rent_charge(db, contract, cursor)
        except ValueError:
            charge = None
        if charge:
            invoice = charge.get("invoice") or {}
            attempt = invoice.get("charge_attempt") or {}
            in_flight = attempt.get("status") in {"processing", "unknown"}
            result.append({
                "period": cursor.strftime("%Y-%m"),
                "month": cursor.strftime("%B"),
                "year": cursor.year,
                "invoice_id": charge["invoice_id"],
                "amount": charge["amount"],
                "late_fee": charge["late_fee"],
                "total_due": charge["total_due"],
                "outstanding": charge["outstanding"],
                "status": charge["status"],
                "paid": charge["status"] in {"paid", "completed"},
                "in_flight": in_flight,
                "payable": False,
            })
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)

    # Only a consecutive prefix beginning with the first unpaid month can be
    # charged.  An unresolved attempt blocks later months until it settles.
    blocked = False
    for item in result:
        if item["paid"] or item["outstanding"] <= 0:
            continue
        if item["in_flight"]:
            blocked = True
        item["payable"] = not blocked
    return result


def _require_next_period_prefix(previews: list[dict], periods: list[str]) -> None:
    """Require requested months to start at the next unpaid lease month."""
    eligible = [item["period"] for item in previews if item["payable"]]
    if not eligible or periods != eligible[:len(periods)]:
        raise HTTPException(
            status_code=409,
            detail=(
                "Debes comenzar por la próxima renta sin pagar y seleccionar "
                "solamente meses consecutivos"
            ),
        )


def _existing_checkout_response(payment: dict) -> dict:
    """Return a reusable checkout only when its provider URL is already known."""
    if payment.get("status") == "pending_checkout" and payment.get("checkout_url"):
        return {
            "success": True,
            "processor": payment.get("checkout_processor", ""),
            "url": payment["checkout_url"],
            "payment_id": str(payment["_id"]),
            "amount": float(payment.get("total_paid") or 0),
            "reused": True,
        }
    raise HTTPException(
        status_code=409,
        detail=(
            "Ya existe un checkout para esta renta y su estado requiere verificación "
            "antes de crear otro."
        ),
    )


async def _square_cash_app_config() -> dict:
    """Return Square credentials for Cash App Pay without changing the active processor."""
    doc = await core._get_doc()
    square = doc.get("processors", {}).get("square", {})
    creds = dict(core._active_creds(square))
    creds["environment"] = square.get("environment", "sandbox")
    creds["available"] = bool(creds.get("access_token") and creds.get("location_id"))
    return creds


@router.get("/tenant/payment-capabilities")
async def tenant_payment_capabilities(request: Request):
    """Expose only safe availability flags; provider credentials never leave the server."""
    await core.auth_tenant_flex(request)
    square = await _square_cash_app_config()
    return {"success": True, "cash_app_pay": bool(square["available"])}


@router.get("/tenant/rent-payable-periods")
async def tenant_rent_payable_periods(request: Request):
    """Preview twelve lease months without materializing future invoices."""
    tenant = await core.auth_tenant_flex(request)
    db = core.get_db()
    contract = await db.rental_contracts.find_one({
        "tenant_id": tenant["_id"], "status": "active",
    })
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")
    now = datetime.now(timezone.utc)
    result = await _payable_period_previews(db, contract, now)
    return {"success": True, "periods": result, "max_months": 12}


async def _create_square_cash_app_checkout(*, amount_cents: int, reference: str,
                                           redirect_url: str, idempotency_key: str) -> dict:
    """Create a Square-hosted checkout with the official Cash App Pay option."""
    cfg = await _square_cash_app_config()
    if not cfg["available"]:
        raise HTTPException(status_code=409,
                            detail="Cash App Pay no está configurado")
    base = core.SQUARE_BASE.get(cfg["environment"], core.SQUARE_BASE["sandbox"])
    checkout_options = {
        "accepted_payment_methods": {
            "apple_pay": False,
            "google_pay": False,
            "cash_app_pay": True,
            "afterpay_clearpay": False,
        },
    }
    if redirect_url:
        checkout_options["redirect_url"] = redirect_url
    body = {
        "idempotency_key": idempotency_key,
        "quick_pay": {
            "name": reference[:255] or "Pago de renta",
            "price_money": {"amount": amount_cents, "currency": "USD"},
            "location_id": cfg["location_id"],
        },
        "checkout_options": checkout_options,
        "payment_note": reference[:500],
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{base}/online-checkout/payment-links",
            headers={
                "Authorization": f"Bearer {cfg['access_token']}",
                "Square-Version": core.SQUARE_VERSION,
                "Content-Type": "application/json",
            },
            json=body,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"Cash App Pay no disponible (Square HTTP {response.status_code})")
    link = response.json().get("payment_link", {})
    url = link.get("url") or link.get("long_url", "")
    if not url:
        raise HTTPException(status_code=502,
                            detail="Square no devolvió el enlace de Cash App Pay")
    return {"processor": "square", "url": url, "external_id": link.get("id", ""),
            "order_id": link.get("order_id", "")}


@router.post("/tenant/create-checkout-payment")
async def tenant_create_checkout_payment(request: Request):
    """Create at most one hosted checkout for a contract/month.

    A deterministic Mongo `_id` is inserted before any provider-side creation.
    Therefore only one concurrent worker can call Stripe/Square/Clover/BofA/
    Helcim. Ambiguous failures retain the claim and fail closed: silently opening
    a second payable checkout is more dangerous than requiring reconciliation.
    """
    tenant = await core.auth_tenant_flex(request)
    data = await request.json()
    hosted = bool(data.get("hosted"))
    requested_method = str(data.get("payment_method") or "").strip().lower()
    cash_app_pay = requested_method == "cashapp"

    if cash_app_pay:
        square = await _square_cash_app_config()
        if not square["available"]:
            raise HTTPException(status_code=409, detail="Cash App Pay no está configurado")
        name = "square"
    else:
        name, _ = await core.get_active_processor()
    if name == "stripe" and not hosted and data.get("periods") is None:
        return {"success": True, "processor": "stripe"}

    db = core.get_db()
    contract = await db.rental_contracts.find_one({
        "tenant_id": tenant["_id"],
        "status": "active",
    })
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")

    now = datetime.now(timezone.utc)
    contract_id = str(contract["_id"])
    periods = _requested_periods(data, now)
    previews = await _payable_period_previews(db, contract, now)
    _require_next_period_prefix(previews, periods)
    claim_id = _hosted_checkout_batch_claim_id(contract_id, periods)

    # Every amount and allocation comes from a canonical server-side invoice.
    try:
        charges = [
            await resolve_period_rent_charge(
                db, contract,
                datetime.strptime(period, "%Y-%m").replace(tzinfo=timezone.utc),
            )
            for period in periods
        ]
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Uno de los meses seleccionados no está disponible para cobro",
        ) from exc
    if any(charge["outstanding"] <= 0 for charge in charges):
        raise HTTPException(status_code=409,
                            detail="Uno de los meses ya está pagado o no tiene saldo")
    amount = round(sum(charge["amount"] for charge in charges), 2)
    late_fee = round(sum(charge["late_fee"] for charge in charges), 2)
    total = round(sum(charge["outstanding"] for charge in charges), 2)

    claim_doc = {
        "_id": claim_id,
        "record_type": "checkout_attempt",
        "contract_id": contract_id,
        "property_id": str(contract.get("property_id", "")),
        "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name", ""),
        "invoice_id": charges[0]["invoice_id"] if len(charges) == 1 else "",
        "periods": periods,
        "invoice_allocations": [],
        "amount": amount,
        "late_fee": late_fee,
        "total_paid": total,
        "payment_method": "cash_app_pay" if cash_app_pay else name,
        "checkout_processor": name,
        "period_month": datetime.strptime(periods[0], "%Y-%m").strftime("%B").lower(),
        "period_month_num": int(periods[0][5:7]),
        "period_year": int(periods[0][:4]),
        "period": periods[0] if len(periods) == 1 else "",
        "status": "creating_checkout",
        "submitted_by": f"tenant_{name}",
        "submitted_at": now,
        "created_at": now,
        "updated_at": now,
    }

    try:
        await db.rental_payments.insert_one(claim_doc)
    except DuplicateKeyError:
        existing = await db.rental_payments.find_one({"_id": claim_id})
        if existing and str(existing.get("tenant_id", "")) == str(tenant["_id"]):
            return _existing_checkout_response(existing)
        raise HTTPException(status_code=409, detail="Checkout ya iniciado")

    from .rent_charge_claim import claim_rent_charge
    allocations = []
    for charge in charges:
        attempt = await claim_rent_charge(
            db, charge["invoice_id"],
            source="cash_app_pay_checkout" if cash_app_pay else f"{name}_checkout",
            amount=charge["outstanding"], contract_id=contract_id,
            checkout_id=claim_id,
        )
        if not attempt:
            # Provider creation has not started, so claims acquired by this batch
            # can be safely released before returning the conflict.
            for acquired in allocations:
                acquired_id = acquired["invoice_id"]
                acquired_ids = [acquired_id]
                if ObjectId.is_valid(acquired_id):
                    acquired_ids.append(ObjectId(acquired_id))
                await db.rental_payments.update_one(
                    {"_id": {"$in": acquired_ids},
                     "charge_attempt.id": acquired["attempt_id"]},
                    {"$unset": {"charge_attempt": ""}},
                )
            await db.rental_payments.update_one(
                {"_id": claim_id}, {"$set": {
                    "status": "checkout_claim_blocked",
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            raise HTTPException(
                409, "Ya existe un intento de pago o el saldo cambió; requiere revisión"
            )
        allocations.append({
            "invoice_id": charge["invoice_id"], "period": charge["period"],
            "amount": charge["outstanding"], "attempt_id": attempt["id"],
        })
    await db.rental_payments.update_one(
        {"_id": claim_id, "status": "creating_checkout"},
        {"$set": {"invoice_allocations": allocations}},
    )

    period_label = ", ".join(periods)
    reference = f"Renta {period_label} - {tenant.get('name', '')}"
    try:
        if cash_app_pay:
            checkout = await _create_square_cash_app_checkout(
                amount_cents=int(round(total * 100)),
                reference=reference,
                redirect_url="https://www.rosshouserentals.com/pago-exitoso",
                idempotency_key=f"cash-app-rent:{claim_id}",
            )
        elif name == "stripe":
            company = await db.rental_config.find_one({"type": "company"}) or {}
            sk = company.get("stripe_secret_key", "")
            if not sk:
                raise HTTPException(status_code=400, detail="Stripe no está configurado")
            import stripe as stripe_lib
            stripe_lib.api_key = sk
            session = stripe_lib.checkout.Session.create(
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": reference},
                        "unit_amount": int(round(total * 100)),
                    },
                    "quantity": 1,
                }],
                customer_email=tenant.get("email") or None,
                success_url="https://www.rosshouserentals.com/pago-exitoso",
                cancel_url="https://www.rosshouserentals.com/tenant/dashboard",
                metadata={"tenant_id": str(tenant["_id"]), "contract_id": contract_id,
                          "periods": ",".join(periods)},
                idempotency_key=f"hosted-rent:{claim_id}",
            )
            checkout = {
                "processor": "stripe",
                "url": session.url,
                "external_id": session.id,
                "order_id": "",
            }
        else:
            checkout = await core.create_hosted_checkout(
                amount_cents=int(round(total * 100)),
                reference=reference,
                customer_email=tenant.get("email", ""),
                redirect_url="https://www.rosshouserentals.com/pago-exitoso",
                payment_method=(data.get("payment_method") or "cc-ach"),
            )

        checkout_url = checkout.get("url", "")
        if not checkout_url:
            raise RuntimeError("El procesador no devolvió URL de checkout")

        await db.rental_payments.update_one(
            {"_id": claim_id, "status": "creating_checkout"},
            {"$set": {
                "status": "pending_checkout",
                "payment_method": "cash_app_pay" if cash_app_pay else name,
                "checkout_processor": name,
                "checkout_external_id": checkout.get("external_id", ""),
                "checkout_order_id": checkout.get("order_id", ""),
                "checkout_url": checkout_url,
                "reference_number": checkout.get("external_id", ""),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return {
            "success": True,
            "processor": "cash_app_pay" if cash_app_pay else name,
            "url": checkout_url,
            "payment_id": str(claim_id),
            "amount": total,
            "periods": periods,
            "reused": False,
        }
    except Exception as exc:
        # Never delete/release the claim after provider creation begins. A local
        # timeout/exception cannot prove that the remote checkout was not created.
        for allocation in allocations:
            invoice_id = allocation["invoice_id"]
            invoice_ids = [invoice_id]
            if ObjectId.is_valid(invoice_id):
                invoice_ids.append(ObjectId(invoice_id))
            await db.rental_payments.update_one(
                {"_id": {"$in": invoice_ids},
                 "charge_attempt.id": allocation["attempt_id"]},
                {"$set": {"charge_attempt.status": "unknown",
                          "charge_attempt.updated_at": datetime.now(timezone.utc)}},
            )
        await db.rental_payments.update_one(
            {"_id": claim_id, "status": "creating_checkout"},
            {"$set": {
                "status": "checkout_creation_unknown",
                "checkout_creation_error": str(exc)[:500],
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        raise


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

    event_id = payload.get("id") or payload.get("paymentId") or hashlib.sha256(raw).hexdigest()
    db = core.get_db()
    existing = await db.processor_webhook_events.find_one({
        "event_id": event_id, "processor": "clover"
    })
    if existing:
        return {"received": True, "duplicate": True}

    data_id = payload.get("data") if isinstance(payload.get("data"), str) else None
    candidate_ids = [v for v in [
        payload.get("checkoutSessionId"), data_id, payload.get("id"), payload.get("paymentId")
    ] if v]
    await db.processor_webhook_events.insert_one({
        "processor": "clover", "event_id": event_id,
        "type": payload.get("status", "") or "event", "payload": payload,
        "payload_ids": candidate_ids, "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })
    if clover_webhook_can_settle(verified=verified, status=payload.get("status")):
        inserted = await db.processor_webhook_events.find_one({
            "event_id": event_id, "processor": "clover"
        })
        await core._try_complete_from_webhook(
            "clover", candidate_ids, inserted["_id"] if inserted else None,
        )
    return {"received": True}


def _bofa_signed_fields_cover_settlement(form: dict) -> bool:
    signed = {
        name.strip()
        for name in str(form.get("signed_field_names") or "").split(",")
        if name.strip()
    }
    return {"decision", "req_transaction_uuid", "req_reference_number"}.issubset(signed)


@router.post("/webhooks/bofa")
async def bofa_webhook(request: Request):
    form = {k: str(v) for k, v in (await request.form()).items()}
    doc = await core._get_doc()
    cfg = core._active_creds(doc["processors"].get("bofa", {}))
    secret = cfg.get("sa_secret_key", "")

    verified = False
    if secret:
        if (
            not form.get("signed_field_names")
            or not form.get("signature")
            or not _bofa_signed_fields_cover_settlement(form)
        ):
            raise HTTPException(status_code=403, detail="Firma de Bank of America inválida")
        expected = core._sa_sign(form, secret)
        verified = hmac.compare_digest(expected, form.get("signature", ""))
        if not verified:
            raise HTTPException(status_code=403, detail="Firma de Bank of America inválida")

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
        form.get("req_transaction_uuid"), form.get("req_reference_number"), form.get("transaction_id")
    ] if v]
    settlement_ids = [v for v in [
        form.get("req_transaction_uuid"), form.get("req_reference_number")
    ] if v]
    await db.processor_webhook_events.insert_one({
        "processor": "bofa", "event_id": event_id,
        "type": (form.get("decision") or "notification").lower(),
        "payload": form, "payload_ids": payload_ids, "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })
    if bofa_webhook_can_settle(verified=verified, decision=form.get("decision")):
        inserted = await db.processor_webhook_events.find_one({
            "event_id": event_id, "processor": "bofa"
        })
        await core._try_complete_from_webhook(
            "bofa", settlement_ids, inserted["_id"] if inserted else None
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
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        candidates = [
            part.split(",", 1)[-1] for part in webhook_signature.split(" ") if part
        ]
        return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
    except Exception:  # noqa: BLE001
        return False


async def _helcim_authoritative_transaction(
    *, api_token: str, transaction_id: str
) -> dict | None:
    if not api_token or not transaction_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{core.HELCIM_BASE}/card-transactions/{transaction_id}",
                headers={"api-token": api_token, "accept": "application/json"},
            )
        if response.status_code >= 400:
            core.logger.warning("Helcim transaction lookup failed: HTTP %s", response.status_code)
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001
        core.logger.warning("Helcim transaction lookup failed: %s", exc)
        return None


@router.post("/webhooks/hpay")
async def helcim_webhook(request: Request):
    raw = await request.body()
    wh_id = request.headers.get("webhook-id", "")
    wh_ts = request.headers.get("webhook-timestamp", "")
    wh_sig = request.headers.get("webhook-signature", "")

    doc = await core._get_doc()
    cfg = core._active_creds(doc["processors"].get("helcim", {}))
    verifier = cfg.get("webhook_verifier_token", "")
    verified = _helcim_signature_valid(
        raw=raw, webhook_id=wh_id, webhook_timestamp=wh_ts,
        webhook_signature=wh_sig, verifier_token=verifier,
    )
    if verifier and wh_sig and not verified:
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
        "processor": "helcim", "event_id": event_id,
        "type": payload.get("type", "notification"), "payload": payload,
        "payload_ids": [txn_id] if txn_id else [], "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })

    if helcim_webhook_may_lookup(
        verified=verified, event_type=payload.get("type"), transaction_id=txn_id,
    ):
        session = await db.helcim_checkout_sessions.find_one({"transaction_id": txn_id})
        if session:
            transaction = await _helcim_authoritative_transaction(
                api_token=cfg.get("api_token", ""), transaction_id=txn_id,
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
                        "status": "paid", "helcim_transaction": transaction,
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
