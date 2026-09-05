"""Fail-closed policy for synthetic staging fixture mutations."""
from __future__ import annotations

import re
from collections.abc import Mapping

_TRUTHY = {"1", "true", "yes", "on"}
_MARKER = re.compile(r"^staging-renewal-[0-9a-f]{32}$")
_EXTERNAL_DELIVERY_KEYS = (
    "SENDGRID_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
)


class StagingFixturePolicyError(RuntimeError):
    """Raised when a synthetic fixture operation is not safely isolated."""


def assert_staging_fixture_allowed(
    environ: Mapping[str, str],
    *,
    database_name: str,
) -> None:
    """Reject fixture writes unless every staging isolation invariant holds."""
    environment = environ.get("ENVIRONMENT", "").strip().lower()
    db_name = (database_name or "").strip().lower()

    if environment != "staging":
        raise StagingFixturePolicyError("fixture_environment_not_staging")
    if "staging" not in db_name or db_name == "taxportal":
        raise StagingFixturePolicyError("fixture_database_not_staging")
    if environ.get("DISABLE_BACKGROUND_JOBS", "").strip().lower() not in _TRUTHY:
        raise StagingFixturePolicyError("fixture_background_jobs_not_disabled")
    if environ.get("STAGING_FIXTURES_ENABLED", "").strip().lower() not in _TRUTHY:
        raise StagingFixturePolicyError("staging_fixtures_not_enabled")
    if any(environ.get(key, "").strip() for key in _EXTERNAL_DELIVERY_KEYS):
        raise StagingFixturePolicyError("fixture_external_delivery_configured")


def validate_fixture_marker(marker: str) -> str:
    """Accept only generated markers that cleanup can target exactly."""
    value = (marker or "").strip().lower()
    if not _MARKER.fullmatch(value):
        raise StagingFixturePolicyError("fixture_marker_invalid")
    return value
