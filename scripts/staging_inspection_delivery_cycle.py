#!/usr/bin/env python3
"""Fail-closed staging smoke for inspection delivery observability and safe retry."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


BASE_URL = os.environ.get("STAGING_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("STAGING_ADMIN_TOKEN", "")
MARKER = f"staging-inspection-{uuid.uuid4().hex}"


def fail(message: str) -> None:
    raise RuntimeError(message)


def request(method: str, path: str, body=None, *, auth=True, expected=(200,)):
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode())
            if response.status not in expected:
                fail(f"unexpected_status:{response.status}:{path}")
            return payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        fail(f"http_{exc.code}:{path}:{detail[:300]}")


def validate_target() -> None:
    parsed = urllib.parse.urlparse(BASE_URL)
    if parsed.scheme != "https" or "staging" not in (parsed.hostname or "").lower():
        fail("staging_url_not_fail_closed")
    if not TOKEN:
        fail("staging_admin_token_missing")
    health = request("GET", "/api/health", auth=False)
    if health.get("status") != "ok" or "staging" not in str(health.get("database_name", "")).lower():
        fail("staging_health_or_database_invalid")


def main() -> int:
    validate_target()
    fixture_path = f"/api/admin/staging-fixtures/inspection-delivery/{MARKER}"
    confirmation = {"confirm_marker": MARKER}
    created = False
    try:
        fixture = request("POST", fixture_path, confirmation)
        created = True
        intent_id = fixture.get("intent_id")
        inspection_id = fixture.get("inspection_id")
        if not intent_id or not inspection_id:
            fail("fixture_identity_missing")

        state = request("GET", fixture_path)
        if not state.get("complete") or state.get("delivery_status") != "failed" or state.get("attempts") != 3:
            fail("fixture_initial_state_invalid")

        listed = request("GET", "/api/admin/inspections/delivery-outbox?status=failed&limit=100")
        item = next((row for row in listed.get("items", []) if row.get("_id") == intent_id), None)
        if not item or item.get("inspection_id") != inspection_id:
            fail("delivery_monitor_missing_fixture")
        if "email" in item:
            fail("delivery_monitor_exposed_recipient")

        retried = request(
            "POST",
            f"/api/admin/inspections/delivery-outbox/{intent_id}/retry",
            {"reason": "Staging smoke verified provider remains disabled"},
        )
        if retried.get("item", {}).get("status") != "pending" or retried.get("item", {}).get("attempts") != 0:
            fail("manual_retry_transition_invalid")

        state = request("GET", fixture_path)
        if state.get("delivery_status") != "pending" or state.get("attempts") != 0:
            fail("fixture_retry_state_invalid")

        print(json.dumps({"status": "PASS", "marker": MARKER, "provider_contacted": False}))
        return 0
    finally:
        if created:
            deleted = request("DELETE", fixture_path, confirmation)
            counts = deleted.get("deleted", {})
            if counts.get("outbox") != 1 or counts.get("inspections") != 1 or counts.get("tenants") != 1:
                fail(f"fixture_cleanup_incomplete:{counts}")
            final = request("GET", fixture_path)
            if final.get("present"):
                fail("fixture_cleanup_residue")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"STAGING_INSPECTION_DELIVERY_SMOKE_FAILED:{exc}", file=sys.stderr)
        sys.exit(1)
