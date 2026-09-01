#!/usr/bin/env python3
"""Fail-closed, read-only smoke checks for a Ross House staging backend.

The smoke never creates or mutates data. It refuses production-looking targets,
verifies the health response is connected to a dedicated staging database, and
proves the renewal workflow boundary rejects anonymous access. If
STAGING_ADMIN_TOKEN is present, it also validates the bounded admin read model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PRODUCTION_HOST_MARKERS = (
    "ross-house-backend-production",
    "rosshouserentals.com",
)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
RENEWAL_PATH = "/api/admin/lease-renewals/workflow-statuses?limit=5"


class SmokeFailure(RuntimeError):
    pass


def validate_base_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if not value or not host or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise SmokeFailure("STAGING_BASE_URL must be an origin without path, query, or fragment")
    if any(marker in host for marker in PRODUCTION_HOST_MARKERS):
        raise SmokeFailure("refusing production-looking host")
    if parsed.scheme != "https" and host not in LOCAL_HOSTS:
        raise SmokeFailure("staging target must use HTTPS")
    if host not in LOCAL_HOSTS and "staging" not in host:
        raise SmokeFailure("staging hostname must contain 'staging'")
    return value


def _request_json(
    base_url: str,
    path: str,
    token: str = "",
    opener: Callable[..., Any] = urlopen,
) -> tuple[int, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ross-house-staging-smoke/1",
        "X-Ross-Environment": "staging-smoke",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(base_url + path, headers=headers, method="GET")
    try:
        with opener(request, timeout=10) as response:
            status = int(response.status)
            raw = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure(f"request failed: {type(exc).__name__}") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeFailure("response was not valid JSON") from exc
    return status, payload


def run_smoke(
    raw_base_url: str,
    admin_token: str = "",
    opener: Callable[..., Any] = urlopen,
) -> list[str]:
    base_url = validate_base_url(raw_base_url)
    checks: list[str] = []

    status, health = _request_json(base_url, "/api/health", opener=opener)
    if status != 200 or not isinstance(health, dict):
        raise SmokeFailure("health endpoint did not return HTTP 200 JSON")
    if health.get("status") != "ok" or health.get("service") != "Ross House Rentals API":
        raise SmokeFailure("health identity mismatch")
    if health.get("database") != "connected":
        raise SmokeFailure("staging database is not connected")
    database_name = str(health.get("database_name") or "").lower()
    if "staging" not in database_name or database_name == "taxportal":
        raise SmokeFailure("health endpoint does not identify a dedicated staging database")
    checks.append("health-and-database")

    anonymous_status, _ = _request_json(base_url, RENEWAL_PATH, opener=opener)
    if anonymous_status not in (401, 403):
        raise SmokeFailure("renewal admin boundary did not reject anonymous access")
    checks.append("renewal-auth-boundary")

    if admin_token:
        auth_status, payload = _request_json(
            base_url, RENEWAL_PATH, token=admin_token, opener=opener
        )
        if auth_status != 200 or not isinstance(payload, dict):
            raise SmokeFailure("authenticated renewal read model unavailable")
        if payload.get("ok") is not True or payload.get("read_only") is not True:
            raise SmokeFailure("renewal response is not the canonical read-only model")
        items = payload.get("items")
        total = payload.get("total")
        if not isinstance(items, list) or not isinstance(total, int) or total != len(items):
            raise SmokeFailure("renewal response shape is invalid")
        if total > 5:
            raise SmokeFailure("renewal response exceeded requested bound")
        for item in items:
            if not isinstance(item, dict) or item.get("read_only") is not True:
                raise SmokeFailure("renewal item is not read-only")
            if item.get("integrity") not in ("verified", "unavailable"):
                raise SmokeFailure("renewal item integrity state is invalid")
        checks.append("renewal-admin-read-model")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("STAGING_BASE_URL", ""),
        help="staging origin; defaults to STAGING_BASE_URL",
    )
    args = parser.parse_args()
    try:
        checks = run_smoke(
            args.base_url,
            admin_token=os.environ.get("STAGING_ADMIN_TOKEN", ""),
        )
    except SmokeFailure as exc:
        print(f"staging renewal smoke: FAIL ({exc})", file=sys.stderr)
        return 1
    print("staging renewal smoke: PASS (" + ", ".join(checks) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
