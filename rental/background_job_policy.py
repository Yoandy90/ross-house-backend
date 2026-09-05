"""Central safety policy for autonomous background jobs."""
from __future__ import annotations

import os
from collections.abc import Mapping

_TRUTHY = {"1", "true", "yes", "on"}
_NON_PRODUCTION_ENVIRONMENTS = {"staging", "test", "testing"}


def should_disable_background_jobs(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when autonomous jobs must not start.

    Staging and test environments are always fail-closed. The explicit flag is
    an emergency kill switch for every other environment. With neither setting,
    existing production/development behavior remains unchanged.
    """
    values = os.environ if environ is None else environ
    environment = values.get("ENVIRONMENT", "").strip().lower()
    explicitly_disabled = (
        values.get("DISABLE_BACKGROUND_JOBS", "").strip().lower() in _TRUTHY
    )
    return environment in _NON_PRODUCTION_ENVIRONMENTS or explicitly_disabled
