#!/usr/bin/env python3
"""Fail-closed, GET-only inspection of the deployed production readiness gate."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("PRODUCTION_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("PRODUCTION_ADMIN_TOKEN", "")
SCOPE = os.environ.get("PRODUCTION_READINESS_SCOPE", "deploy").strip().lower()


def fail(code: str) -> None:
    raise RuntimeError(code)


def get_json(path: str, *, auth: bool) -> dict:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(
        f"{BASE_URL}{path}", headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        fail(f"http_{exc.code}:{path}:{detail[:200]}")


def validate_target() -> dict:
    parsed = urllib.parse.urlparse(BASE_URL)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or "staging" in hostname
        or hostname in {"localhost", "127.0.0.1"}
    ):
        fail("production_url_not_fail_closed")
    if not TOKEN:
        fail("production_admin_token_missing")
    health = get_json("/api/health", auth=False)
    database_name = str(health.get("database_name", "")).lower()
    if (
        health.get("status") != "ok"
        or health.get("database") != "connected"
        or not database_name
        or "staging" in database_name
        or database_name == "taxportal"
    ):
        fail("production_health_or_database_invalid")
    return health


def main() -> int:
    validate_target()
    if SCOPE not in {"deploy", "inspection-delivery"}:
        fail("production_readiness_scope_invalid")
    report = get_json(
        "/api/admin/operations/production-readiness", auth=True
    )
    if report.get("success") is not True:
        fail("production_readiness_report_invalid")

    gate = (
        "safe_to_deploy"
        if SCOPE == "deploy"
        else "ready_to_enable_inspection_delivery"
    )
    issues_key = (
        "blocking_issues"
        if SCOPE == "deploy"
        else "inspection_delivery_blocking_issues"
    )
    if report.get(gate) is not True:
        issues = report.get(issues_key) or ["readiness_gate_failed"]
        fail(f"{gate}_false:{','.join(str(item) for item in issues)}")

    print(json.dumps({"status": "PASS", "scope": SCOPE, "gate": gate}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"PRODUCTION_READINESS_INSPECTION_FAILED:{exc}", file=sys.stderr)
        sys.exit(1)
