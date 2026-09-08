#!/usr/bin/env python3
"""Validate a sanitized, names-only Railway staging snapshot.

The snapshot must not contain environment variable values. It verifies the
expected Ross House service identity, required variable names, and deployed
commit without making network requests or printing arbitrary snapshot data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

try:
    from scripts.validate_staging_env import REQUIRED
except ModuleNotFoundError:  # Direct execution from scripts/.
    from validate_staging_env import REQUIRED

EXPECTED_IDENTITY = {
    "project_name": "Ross House",
    "environment_name": "staging",
    "service_name": "ross_house_staging",
    "source_repo": "Yoandy90/ross-house-backend",
    "source_branch": "main",
}
ALLOWED_FIELDS = {
    *EXPECTED_IDENTITY,
    "deployed_commit",
    "values_redacted",
    "variable_names",
}
SHA_PATTERN = re.compile(r"[0-9a-f]{7,40}")


def commits_match(deployed: object, expected: str) -> bool:
    left = str(deployed or "").strip().lower()
    right = expected.strip().lower()
    return bool(
        SHA_PATTERN.fullmatch(left)
        and SHA_PATTERN.fullmatch(right)
        and (left.startswith(right) or right.startswith(left))
    )


def validate(snapshot: object, expected_commit: str) -> list[str]:
    if not isinstance(snapshot, dict):
        return ["snapshot_must_be_an_object"]

    errors: list[str] = []
    unexpected = sorted(set(snapshot) - ALLOWED_FIELDS)
    if unexpected:
        errors.append("unexpected_fields:" + ",".join(unexpected))

    if "variables" in snapshot or snapshot.get("values_redacted") is not True:
        errors.append("snapshot_must_be_names_only")

    for field, expected in EXPECTED_IDENTITY.items():
        if snapshot.get(field) != expected:
            errors.append(f"identity_mismatch:{field}")

    names = snapshot.get("variable_names")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        errors.append("variable_names_must_be_a_string_list")
    else:
        missing = sorted(REQUIRED - set(names))
        if missing:
            errors.append("missing_required_variables:" + ",".join(missing))

    if not commits_match(snapshot.get("deployed_commit"), expected_commit):
        errors.append("deployed_commit_mismatch")

    return errors


def load_snapshot(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()

    try:
        snapshot = load_snapshot(args.snapshot)
    except (OSError, json.JSONDecodeError):
        print("staging snapshot: FAIL (snapshot_unreadable)", file=sys.stderr)
        return 1

    errors = validate(snapshot, args.expected_commit)
    if errors:
        for error in errors:
            print(f"staging snapshot: FAIL ({error})", file=sys.stderr)
        return 1

    print("staging snapshot: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
