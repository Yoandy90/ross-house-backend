"""Side-effect-free production readiness assessment.

The report intentionally returns booleans and issue codes only. It never exposes
environment values or secrets, and it does not change startup behavior.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .background_job_policy import should_disable_background_jobs
from .runtime_business_boundary import resolve_database_name

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}
_PLACEHOLDER_FRAGMENTS = ("changeme", "replace-me", "example", "password", "secret123")


def _is_true(values: Mapping[str, str], name: str) -> bool:
    return str(values.get(name, "")).strip().lower() in _TRUTHY


def _is_false(values: Mapping[str, str], name: str) -> bool:
    return str(values.get(name, "")).strip().lower() in _FALSEY


def _strong_secret(value: str) -> bool:
    secret = str(value or "").strip()
    lowered = secret.lower()
    return len(secret) >= 64 and not any(fragment in lowered for fragment in _PLACEHOLDER_FRAGMENTS)


def assess_production_readiness(
    environ: Mapping[str, str],
    *,
    database_name: str,
) -> dict[str, Any]:
    """Assess deployment safety separately from autonomous delivery activation."""
    environment = str(environ.get("ENVIRONMENT", "")).strip().lower()
    db_name = str(database_name or "").strip().lower()
    worker_enabled = _is_true(environ, "INSPECTION_DELIVERY_WORKER_ENABLED")
    background_jobs_disabled = _is_true(environ, "DISABLE_BACKGROUND_JOBS")

    checks = {
        "environment_is_production": environment == "production",
        "database_is_explicit_and_isolated": db_name == "ross_house_production",
        "tenant_jwt_secret_is_stable_and_strong": _strong_secret(
            environ.get("TENANT_JWT_SECRET", "")
        ),
        "staging_fixtures_are_disabled": not _is_true(
            environ, "STAGING_FIXTURES_ENABLED"
        ),
        "refresh_tokens_are_enabled": _is_true(
            environ, "REFRESH_TOKENS_ENABLED"
        ),
        "legacy_sessions_are_disabled": _is_false(
            environ, "ALLOW_LEGACY_USER_SESSIONS"
        ),
        "session_sid_is_required": _is_true(environ, "REQUIRE_SESSION_SID"),
    }
    blocking_issues = [name for name, passed in checks.items() if not passed]
    safe_to_deploy = not blocking_issues

    delivery_checks = {
        "production_deploy_gate_passed": safe_to_deploy,
        "background_jobs_are_enabled": not background_jobs_disabled,
        "inspection_worker_is_enabled": worker_enabled,
        "sendgrid_api_key_is_present": bool(
            str(environ.get("SENDGRID_API_KEY", "")).strip()
        ),
        "sendgrid_from_email_is_present": bool(
            str(environ.get("SENDGRID_FROM_EMAIL", "")).strip()
        ),
    }
    delivery_blocking_issues = [
        name for name, passed in delivery_checks.items() if not passed
    ]

    return {
        "safe_to_deploy": safe_to_deploy,
        "ready_to_enable_inspection_delivery": not delivery_blocking_issues,
        "checks": checks,
        "blocking_issues": blocking_issues,
        "inspection_delivery_checks": delivery_checks,
        "inspection_delivery_blocking_issues": delivery_blocking_issues,
    }


def assess_staging_readiness(environ: Mapping[str, str], *, database_name: str) -> dict[str, Any]:
    """Report staging safety using booleans and codes without exposing values."""
    environment = str(environ.get("ENVIRONMENT", "")).strip().lower()
    try:
        resolved = resolve_database_name({**dict(environ), "DB_NAME": database_name})
        database_valid = resolved == "ross_house_staging"
    except RuntimeError:
        database_valid = False
    checks = {
        "environment_is_staging": environment == "staging",
        "database_is_ross_house_staging": database_valid,
        "background_jobs_are_disabled": should_disable_background_jobs(environ),
        "tenant_jwt_secret_is_stable_and_strong": _strong_secret(environ.get("TENANT_JWT_SECRET", "")),
        "vault_encryption_key_is_present": bool(str(environ.get("VAULT_ENCRYPTION_KEY", "")).strip()),
        "refresh_tokens_are_enabled": _is_true(environ, "REFRESH_TOKENS_ENABLED"),
        "legacy_sessions_are_disabled": _is_false(environ, "ALLOW_LEGACY_USER_SESSIONS"),
        "session_sid_is_required": _is_true(environ, "REQUIRE_SESSION_SID"),
    }
    issues = [name for name, passed in checks.items() if not passed]
    return {"safe_for_staging_validation": not issues, "checks": checks,
            "blocking_issues": issues, "secrets_exposed": False}
