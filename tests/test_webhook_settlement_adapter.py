"""Regression tests for the public payment-processor webhook adapter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from rental import payment_processors_core as core
from rental import payment_processors_router as ppr


def _route_endpoints(router, path: str):
    return [
        getattr(route, "endpoint", None)
        for route in router.routes
        if getattr(route, "path", None) == path
    ]


def test_public_router_replaces_exactly_three_settlement_webhooks():
    for path, endpoint in (
        ("/webhooks/clover", ppr.clover_webhook),
        ("/webhooks/bofa", ppr.bofa_webhook),
        ("/webhooks/hpay", ppr.helcim_webhook),
    ):
        public = _route_endpoints(ppr.router, path)
        assert public == [endpoint]
        assert endpoint not in _route_endpoints(core.router, path)
        # The legacy core still owns exactly one original route; the adapter does
        # not mutate that router, it merely excludes that route when composing.
        assert len(_route_endpoints(core.router, path)) == 1


def test_non_webhook_public_surface_is_forwarded_from_core():
    assert ppr.create_hosted_checkout is core.create_hosted_checkout
    assert ppr.get_active_processor is core.get_active_processor
    assert ppr._square_order_is_paid is core._square_order_is_paid
    assert ppr._square_webhook_can_complete is core._square_webhook_can_complete
    assert ppr.httpx is core.httpx


def test_clover_signature_helper_accepts_valid_current_signature(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(ppr.time, "time", lambda: now)
    raw = b'{"id":"evt_1","status":"APPROVED"}'
    secret = "test-clover-secret"
    supplied = hmac.new(
        secret.encode(), f"{now}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    header = f"t={now},v1={supplied}"
    assert ppr._clover_signature_valid(raw, header, secret) is True
    assert ppr._clover_signature_valid(raw + b"x", header, secret) is False
    assert ppr._clover_signature_valid(raw, f"t={now - 301},v1={supplied}", secret) is False


def test_bofa_settlement_fields_must_all_be_signed():
    good = {
        "signed_field_names": (
            "decision,req_transaction_uuid,req_reference_number,transaction_id"
        )
    }
    assert ppr._bofa_signed_fields_cover_settlement(good) is True
    for missing in ("decision", "req_transaction_uuid", "req_reference_number"):
        fields = [
            name for name in good["signed_field_names"].split(",") if name != missing
        ]
        assert ppr._bofa_signed_fields_cover_settlement(
            {"signed_field_names": ",".join(fields)}
        ) is False


def test_helcim_signature_helper_matches_documented_hmac_shape():
    raw = json.dumps({"id": "25764674", "type": "cardTransaction"}, separators=(",", ":")).encode()
    webhook_id = "msg_test"
    timestamp = "1800000000"
    raw_key = b"test-helcim-verifier"
    verifier = base64.b64encode(raw_key).decode()
    signed = f"{webhook_id}.{timestamp}.".encode() + raw
    expected = base64.b64encode(hmac.new(raw_key, signed, hashlib.sha256).digest()).decode()
    assert ppr._helcim_signature_valid(
        raw=raw,
        webhook_id=webhook_id,
        webhook_timestamp=timestamp,
        webhook_signature=f"v1,{expected}",
        verifier_token=verifier,
    ) is True
    assert ppr._helcim_signature_valid(
        raw=raw + b"x",
        webhook_id=webhook_id,
        webhook_timestamp=timestamp,
        webhook_signature=f"v1,{expected}",
        verifier_token=verifier,
    ) is False


def test_helcim_provider_lookup_is_required_before_paid_session_write():
    source = open("rental/payment_processors_router.py", encoding="utf-8").read()
    handler = source.split('async def helcim_webhook(request: Request):', 1)[1]
    assert "_helcim_authoritative_transaction" in handler
    assert "helcim_transaction_can_settle" in handler
    assert '"status": "paid"' in handler
    assert handler.index("_helcim_authoritative_transaction") < handler.index('"status": "paid"')
    assert handler.index("helcim_transaction_can_settle") < handler.index('"status": "paid"')
