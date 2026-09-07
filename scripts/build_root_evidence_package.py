#!/usr/bin/env python3
"""Build a private, non-executable v2 root evidence package offline."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.plan_database_isolation import (
    SHA256_RE,
    _canonical_sha256,
    _expected_evidence_id,
    apply_offline_filter_evidence,
    load_filter_contract,
    load_inventory,
)


ROOTS = {"app_users", "tenants"}
ROOT_REQUIREMENT = "explicit_root_id_allowlist"
PROVENANCE_FIELDS = {
    "prepared_by",
    "approved_by",
    "prepared_at",
    "approved_at",
    "expires_at",
}


def build_root_evidence_package(
    inventory: dict,
    contract: dict,
    submission: dict,
    *,
    now: datetime | None = None,
) -> dict:
    fixed = {
        "version": 1,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
    }
    allowed = {
        *fixed,
        "inventory_sha256",
        "contract_sha256",
        "provenance",
        "collections",
    }
    if not isinstance(submission, dict) or set(submission) != allowed:
        raise ValueError("root_submission_fields_invalid")
    for field, expected in fixed.items():
        if submission.get(field) != expected:
            raise ValueError(f"root_submission_{field}_invalid")
    if inventory.get("database_name") != "taxportal":
        raise ValueError("root_submission_inventory_source_invalid")
    if contract.get("source_database") != "taxportal":
        raise ValueError("root_submission_contract_source_invalid")
    if contract.get("target_database") != "ross_house_production":
        raise ValueError("root_submission_contract_target_invalid")
    if contract.get("migration_authorized") is not False:
        raise ValueError("root_submission_contract_authorization_invalid")
    if contract.get("default_action") != "block":
        raise ValueError("root_submission_contract_default_invalid")
    root_definition = contract.get("requirements", {}).get(ROOT_REQUIREMENT, {})
    if set(root_definition.get("collections", [])) != ROOTS:
        raise ValueError("root_submission_contract_roots_invalid")
    for field, expected in (
        ("inventory_sha256", inventory["_inventory_sha256"]),
        ("contract_sha256", contract["_contract_sha256"]),
    ):
        value = submission.get(field)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ValueError(f"root_submission_{field}_invalid")
        if value != expected:
            raise ValueError(f"root_submission_{field}_mismatch")

    submitted = submission.get("collections")
    if not isinstance(submitted, list):
        raise ValueError("root_submission_collections_invalid")
    names = [row.get("name") for row in submitted if isinstance(row, dict)]
    if (
        len(names) != len(submitted)
        or len(names) != len(set(names))
        or set(names) != ROOTS
    ):
        raise ValueError("root_submission_collections_must_be_exact")

    entries = []
    for row in sorted(submitted, key=lambda item: item["name"]):
        if set(row) != {"name", "root_ids", "ownership_basis"}:
            raise ValueError(f"root_submission_collection_fields_invalid:{row['name']}")
        entries.append(
            {
                "name": row["name"],
                "requirement": ROOT_REQUIREMENT,
                "evidence": {
                    "root_ids": row["root_ids"],
                    "ownership_basis": row["ownership_basis"],
                },
            }
        )

    provenance = submission.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_FIELDS:
        raise ValueError("root_submission_provenance_invalid")
    provenance = {**provenance, "evidence_id": ""}
    collections_sha256 = _canonical_sha256(entries)
    provenance["evidence_id"] = _expected_evidence_id(
        inventory["_inventory_sha256"],
        contract["_contract_sha256"],
        collections_sha256,
        provenance,
    )
    package = {
        "version": 2,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
        "default_action": "block",
        "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "collections_sha256": collections_sha256,
        "provenance": provenance,
        "collections": entries,
    }
    rows = [
        {
            "name": name,
            "filter_requirement": ROOT_REQUIREMENT,
            "filter_status": "blocked_pending_evidence",
        }
        for name in sorted(ROOTS)
    ]
    apply_offline_filter_evidence(
        rows,
        package,
        inventory["_inventory_sha256"],
        contract["_contract_sha256"],
        now=now,
    )
    return package


def write_private_package(path: Path, package: dict, repository_root: Path) -> None:
    destination = path.resolve()
    root = repository_root.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("root_evidence_output_must_be_outside_repository")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(package, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("submission", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--filter-contract",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "database_isolation_filter_contract.json",
    )
    args = parser.parse_args()
    package = build_root_evidence_package(
        load_inventory(args.inventory),
        load_filter_contract(args.filter_contract),
        json.loads(args.submission.read_text(encoding="utf-8-sig")),
        now=datetime.now(timezone.utc),
    )
    write_private_package(
        args.output, package, Path(__file__).resolve().parents[1]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
