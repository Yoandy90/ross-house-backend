"""Fail-closed resolution for authentication signing secrets."""
import logging
import os
import secrets
from collections.abc import Mapping
from typing import Optional

_PRODUCTION_NAMES = {"production", "prod"}


def is_production_environment(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    names = (
        env.get("ENVIRONMENT", ""),
        env.get("RAILWAY_ENVIRONMENT_NAME", ""),
        env.get("RAILWAY_ENVIRONMENT", ""),
    )
    return any(str(name).strip().lower() in _PRODUCTION_NAMES for name in names)


def resolve_runtime_secret(
    primary: str,
    *aliases: str,
    purpose: str,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the first configured secret and refuse production fallback."""
    env = environ if environ is not None else os.environ
    for name in (primary, *aliases):
        value = env.get(name)
        if value and value.strip():
            return value

    if is_production_environment(env):
        names = ", ".join((primary, *aliases))
        raise RuntimeError(f"Missing required production secret for {purpose}: {names}")

    logging.warning(
        "[SECURITY] %s is not configured outside production; using an ephemeral process secret",
        primary,
    )
    return secrets.token_urlsafe(64)
