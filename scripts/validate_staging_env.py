#!/usr/bin/env python3
"""Fail-closed validation for Ross House staging environment files.

The validator never prints values. Use --template for committed examples; omit it
when validating an actual staging export before creating or updating a service.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

REQUIRED = {
    "ENVIRONMENT", "MONGO_URL", "DB_NAME", "TENANT_JWT_SECRET",
    "JWT_SECRET_KEY", "REFRESH_DERIVE_KEY", "VISITOR_IP_SALT",
    "REFRESH_TOKENS_ENABLED", "ALLOW_LEGACY_USER_SESSIONS",
    "REQUIRE_SESSION_SID", "STRIPE_SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET",
    "RENEWAL_TERM_MONTHS", "DISABLE_BACKGROUND_JOBS", "STAGING_FIXTURES_ENABLED",
}
SECRET_KEYS = {
    "TENANT_JWT_SECRET", "JWT_SECRET_KEY", "REFRESH_DERIVE_KEY",
    "VISITOR_IP_SALT",
}
PLACEHOLDER_MARKERS = ("replace", "your-", "staging_user", "staging_password", "staging_cluster")
PRODUCTION_MARKERS = (
    "ross-house-backend-production", "up.railway.app", "sk_live_", "pk_live_",
)
DELIVERY_KEYS = ("SENDGRID_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"line {number}: invalid key")
        if key in values:
            raise ValueError(f"line {number}: duplicate key {key}")
        values[key] = value.strip()
    return values


def validate(values: dict[str, str], template: bool) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED - values.keys())
    if missing:
        errors.append("missing required keys: " + ", ".join(missing))

    if values.get("ENVIRONMENT", "").lower() != "staging":
        errors.append("ENVIRONMENT must be staging")

    db_name = values.get("DB_NAME", "").lower()
    if "staging" not in db_name or db_name == "taxportal":
        errors.append("DB_NAME must identify a dedicated staging database")

    mongo = values.get("MONGO_URL", "").lower()
    if "staging" not in mongo or "taxportal" in mongo:
        errors.append("MONGO_URL must identify staging and must not select taxportal")

    encoded = "\n".join(values.values()).lower()
    for marker in PRODUCTION_MARKERS:
        if marker in encoded:
            errors.append(f"production marker is forbidden: {marker}")

    if values.get("DISABLE_BACKGROUND_JOBS", "").lower() != "true":
        errors.append("DISABLE_BACKGROUND_JOBS must be true in staging")
    if values.get("STAGING_FIXTURES_ENABLED", "").lower() not in {"true", "false"}:
        errors.append("STAGING_FIXTURES_ENABLED must be true or false")

    if values.get("REFRESH_TOKENS_ENABLED", "").lower() != "true":
        errors.append("REFRESH_TOKENS_ENABLED must be true")
    if values.get("ALLOW_LEGACY_USER_SESSIONS", "").lower() != "false":
        errors.append("ALLOW_LEGACY_USER_SESSIONS must be false")
    if values.get("REQUIRE_SESSION_SID", "").lower() != "true":
        errors.append("REQUIRE_SESSION_SID must be true")

    if not values.get("STRIPE_SECRET_KEY", "").startswith("sk_test_"):
        errors.append("STRIPE_SECRET_KEY must be a Stripe test key")
    if not values.get("STRIPE_PUBLISHABLE_KEY", "").startswith("pk_test_"):
        errors.append("STRIPE_PUBLISHABLE_KEY must be a Stripe test key")

    try:
        months = int(values.get("RENEWAL_TERM_MONTHS", ""))
        if not 1 <= months <= 36:
            raise ValueError
    except ValueError:
        errors.append("RENEWAL_TERM_MONTHS must be an integer from 1 to 36")

    if not template:
        for key in sorted(SECRET_KEYS):
            value = values.get(key, "")
            lower = value.lower()
            if len(value) < 32 or any(marker in lower for marker in PLACEHOLDER_MARKERS):
                errors.append(f"{key} must be an independent non-placeholder secret of at least 32 characters")
        delivery_configured = [key for key in DELIVERY_KEYS if values.get(key)]
        if delivery_configured and values.get("STAGING_EXTERNAL_DELIVERY_ACK") != (
            "I_UNDERSTAND_STAGING_CAN_CONTACT_REAL_RECIPIENTS"
        ):
            errors.append(
                "external delivery credentials require explicit STAGING_EXTERNAL_DELIVERY_ACK"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--template", action="store_true")
    args = parser.parse_args()
    try:
        values = parse_env(args.path)
    except (OSError, ValueError) as exc:
        print(f"staging environment: FAIL ({exc})", file=sys.stderr)
        return 1
    errors = validate(values, args.template)
    if errors:
        for error in errors:
            print(f"staging environment: FAIL ({error})", file=sys.stderr)
        return 1
    print("staging environment: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
