#!/usr/bin/env python3
"""Prepare a non-executable request for offline root-ID ownership evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.plan_database_isolation import (
    SHA256_RE,
    load_filter_contract,
    load_inventory,
)


ROOT_REQUIREMENT = "explicit_root_id_allowlist"
EXPECTED_ROOT_COLLECTIONS = {"app_users", "tenants"}


def build_root_evidence_request(inventory: dict, contract: dict) -> dict:
    """Describe required offline inputs without including IDs or queries."""
    if inventory.get("database_name") != "taxportal":
        raise ValueError("root_request_source_database_invalid")
    if not SHA256_RE.fullmatch(str(inventory.get("_inventory_sha256") or "")):
        raise ValueError("root_request_inventory_sha256_invalid")
    if contract.get("source_database") != "taxportal":
        raise ValueError("root_request_contract_source_invalid")
    if contract.get("target_database") != "ross_house_production":
        raise ValueError("root_request_target_database_invalid")
    if contract.get("migration_authorized") is not False:
        raise ValueError("root_request_migration_must_remain_unauthorized")
    if contract.get("default_action") != "block":
        raise ValueError("root_request_default_must_block")
    if not SHA256_RE.fullmatch(str(contract.get("_contract_sha256") or "")):
        raise ValueError("root_request_contract_sha256_invalid")

    definition = contract["requirements"].get(ROOT_REQUIREMENT)
    if not isinstance(definition, dict):
        raise ValueError("root_request_requirement_missing")
    configured = definition.get("collections")
    if (
        not isinstance(configured, list)
        or len(configured) != len(set(configured))
        or set(configured) != EXPECTED_ROOT_COLLECTIONS
    ):
        raise ValueError("root_request_collections_must_be_exact")

    inventory_rows = {
        row["name"]: row
        for row in inventory["collections"]
        if row["name"] in EXPECTED_ROOT_COLLECTIONS
    }
    missing = sorted(EXPECTED_ROOT_COLLECTIONS - set(inventory_rows))
    if missing:
        raise ValueError(f"root_request_inventory_missing:{','.join(missing)}")

    collections = []
    for name in sorted(EXPECTED_ROOT_COLLECTIONS):
        row = inventory_rows[name]
        collections.append(
            {
                "name": name,
                "requirement": ROOT_REQUIREMENT,
                "estimated_documents": int(
                    row.get("estimated_documents") or 0
                ),
                "required_submission_fields": [
                    "exact_root_ids",
                    "ownership_basis",
                    "prepared_by",
                    "approved_by",
                ],
                "status": "awaiting_offline_allowlist",
            }
        )

    return {
        "version": 1,
        "request_type": "offline_root_id_evidence",
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "migration_authorized": False,
        "default_action": "block",
        "executable_queries_present": False,
        "document_values_present": False,
        "collections": collections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument(
        "--filter-contract",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "database_isolation_filter_contract.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    request = build_root_evidence_request(
        load_inventory(args.inventory),
        load_filter_contract(args.filter_contract),
    )
    rendered = json.dumps(request, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
