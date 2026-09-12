"""Fail-closed runtime boundary for the Ross House Rentals service."""
from __future__ import annotations

from collections.abc import Mapping


RENTALS_CORS_ORIGINS = (
    "https://rosshouserentals.com",
    "https://www.rosshouserentals.com",
)
RENTALS_STAGING_CORS_ORIGINS = (
    "https://ross-house-rentals-git-staging-yoandyross-2350s-projects.vercel.app",
)
_DATABASE_BY_ENVIRONMENT = {
    "production": "ross_house_production",
    "staging": "ross_house_staging",
}
_FOREIGN_DATABASE_MARKERS = ("taxportal", "ross_tax", "ross_lending", "loan", "lending")


def resolve_database_name(environ: Mapping[str, str]) -> str:
    """Select only a Ross House database; deployed environments require exact names."""
    environment = str(environ.get("ENVIRONMENT", "")).strip().lower()
    configured = str(environ.get("DB_NAME", "")).strip()
    if not configured:
        if environment in _DATABASE_BY_ENVIRONMENT:
            raise RuntimeError("ross_house_database_name_required")
        return "ross_house_local"
    lowered = configured.casefold()
    if any(marker in lowered for marker in _FOREIGN_DATABASE_MARKERS):
        raise RuntimeError("foreign_business_database_prohibited")
    expected = _DATABASE_BY_ENVIRONMENT.get(environment)
    if expected is not None and configured != expected:
        raise RuntimeError("deployed_database_name_mismatch")
    if not lowered.startswith("ross_house_"):
        raise RuntimeError("ross_house_database_namespace_required")
    return configured


def deployed_cors_origins(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return the exact frontend origins allowed for each environment."""
    environment = str(environ.get("ENVIRONMENT", "")).strip().lower()
    if environment in {"development", "dev", "local"}:
        return ("*",)
    if environment == "staging":
        return RENTALS_CORS_ORIGINS + RENTALS_STAGING_CORS_ORIGINS
    return RENTALS_CORS_ORIGINS
