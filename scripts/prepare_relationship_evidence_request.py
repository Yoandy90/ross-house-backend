#!/usr/bin/env python3
"""Prepare a non-executable request for offline relationship evidence."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.offline_evidence_json import load_offline_json

from scripts.plan_database_isolation import (
    SHA256_RE,
    apply_offline_filter_evidence,
    load_filter_contract,
    load_inventory,
)


ROOT_REQUIREMENT = "explicit_root_id_allowlist"
RELATIONSHIP_REQUIREMENT = "relationship_closure"
ROOTS = {"app_users", "tenants"}
EXPECTED_RELATIONSHIP_COLLECTIONS = {
    "admin_2fa_settings",
    "admin_audit_logs",
    "admin_otp_challenges",
    "admin_signatures",
    "admin_trusted_devices",
    "app_adoption_invites",
    "auth_metrics_daily",
    "auth_sessions",
    "bank_transactions",
    "checklist_photos",
    "consent_records",
    "credit_builder_enrollments",
    "deleted_accounts",
    "document_requests",
    "expense_receipts",
    "investor_password_resets",
    "legal_documents",
    "password_resets",
    "payment_links",
    "payment_methods",
    "payment_methods_deleted",
    "phone_otps",
    "plaid_items",
    "provider_payments",
    "three_ds_evidence",
    "user_sessions",
    "vault_audit_log",
    "xcel_notifications",
    "xcel_oauth_states",
    "zelle_submissions",
}


def _validate_root_package(
    inventory: dict,
    contract: dict,
    root_package: dict,
    *,
    now: datetime | None,
) -> str:
    entries = root_package.get("collections")
    if not isinstance(entries, list):
        raise ValueError("relationship_request_root_package_invalid")
    names = [
        entry.get("name") for entry in entries if isinstance(entry, dict)
    ]
    if (
        len(names) != len(entries)
        or len(names) != len(set(names))
        or set(names) != ROOTS
    ):
        raise ValueError("relationship_request_roots_must_be_exact")
    rows = [
        {
            "name": name,
            "filter_requirement": ROOT_REQUIREMENT,
            "filter_status": "blocked_pending_evidence",
        }
        for name in sorted(ROOTS)
    ]
    result = apply_offline_filter_evidence(
        rows,
        root_package,
        inventory["_inventory_sha256"],
        contract["_contract_sha256"],
        now=now,
    )
    if result["approved"] != len(ROOTS) or result["still_blocked"]:
        raise ValueError("relationship_request_roots_not_approved")
    return result["evidence_id"]


def build_relationship_evidence_request(
    inventory: dict,
    contract: dict,
    root_package: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Describe relationship evidence needed without exposing root IDs."""
    if inventory.get("database_name") != "taxportal":
        raise ValueError("relationship_request_source_database_invalid")
    if not SHA256_RE.fullmatch(str(inventory.get("_inventory_sha256") or "")):
        raise ValueError("relationship_request_inventory_sha256_invalid")
    if contract.get("source_database") != "taxportal":
        raise ValueError("relationship_request_contract_source_invalid")
    if contract.get("target_database") != "ross_house_production":
        raise ValueError("relationship_request_target_database_invalid")
    if contract.get("migration_authorized") is not False:
        raise ValueError("relationship_request_migration_must_remain_unauthorized")
    if contract.get("default_action") != "block":
        raise ValueError("relationship_request_default_must_block")
    if not SHA256_RE.fullmatch(str(contract.get("_contract_sha256") or "")):
        raise ValueError("relationship_request_contract_sha256_invalid")

    definition = contract.get("requirements", {}).get(
        RELATIONSHIP_REQUIREMENT
    )
    configured = definition.get("collections") if isinstance(definition, dict) else None
    if (
        not isinstance(configured, list)
        or len(configured) != len(set(configured))
        or set(configured) != EXPECTED_RELATIONSHIP_COLLECTIONS
    ):
        raise ValueError("relationship_request_collections_must_be_exact")

    inventory_names = {
        row.get("name")
        for row in inventory.get("collections", [])
        if isinstance(row, dict)
    }
    missing = sorted(set(configured) - inventory_names)
    if missing:
        raise ValueError(
            f"relationship_request_inventory_missing:{','.join(missing)}"
        )

    evidence_id = _validate_root_package(
        inventory, contract, root_package, now=now
    )
    collections = [
        {
            "name": name,
            "requirement": RELATIONSHIP_REQUIREMENT,
            "allowed_root_collections": sorted(ROOTS),
            "required_submission_fields": [
                "root_collection",
                "relationship_paths",
                "exact_root_ids",
            ],
            "status": "awaiting_offline_relationship_evidence",
        }
        for name in sorted(configured)
    ]
    return {
        "version": 1,
        "request_type": "offline_relationship_evidence",
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "approved_root_evidence_id": evidence_id,
        "approved_root_collections": sorted(ROOTS),
        "migration_authorized": False,
        "default_action": "block",
        "executable_queries_present": False,
        "document_values_present": False,
        "collections": collections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("root_package", type=Path)
    parser.add_argument(
        "--filter-contract",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "database_isolation_filter_contract.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    request = build_relationship_evidence_request(
        load_inventory(args.inventory),
        load_filter_contract(args.filter_contract),
        load_offline_json(args.root_package),
        now=datetime.now(timezone.utc),
    )
    rendered = json.dumps(request, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
