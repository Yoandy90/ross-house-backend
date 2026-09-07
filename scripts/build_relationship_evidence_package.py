#!/usr/bin/env python3
"""Prepare and validate private relationship evidence entirely offline."""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_root_evidence_package import PROVENANCE_FIELDS, write_private_package
from scripts.plan_database_isolation import (
    _canonical_sha256, _expected_evidence_id, _utc_timestamp, _reviewer_identity_key,
    apply_offline_filter_evidence, load_filter_contract, load_inventory,
)
from scripts.prepare_relationship_evidence_request import (
    ROOTS, ROOT_REQUIREMENT, RELATIONSHIP_REQUIREMENT,
    EXPECTED_RELATIONSHIP_COLLECTIONS, build_relationship_evidence_request,
)


def prepare_relationship_submission(inventory, contract, roots, *, now=None):
    """Produce placeholders bound to this inventory, contract and root package."""
    if not isinstance(roots, dict):
        raise ValueError("relationship_submission_roots_invalid")
    definitions = contract.get("requirements", {}).get(ROOT_REQUIREMENT, {})
    if definitions.get("collections") not in (["app_users", "tenants"], ["tenants", "app_users"]):
        raise ValueError("relationship_submission_contract_roots_invalid")
    inventory_names = {
        row.get("name") for row in inventory.get("collections", [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    if not ROOTS.issubset(inventory_names):
        raise ValueError("relationship_submission_inventory_roots_missing")
    request = build_relationship_evidence_request(inventory, contract, roots, now=now)
    return {
        "version": 1,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
        "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "request_sha256": _canonical_sha256(request),
        "root_package_sha256": _canonical_sha256(roots),
        "approved_root_evidence_id": request["approved_root_evidence_id"],
        "provenance": {field: "" for field in sorted(PROVENANCE_FIELDS)},
        "collections": [
            {"name": row["name"], "root_collection": None,
             "relationship_paths": [], "exact_root_ids": []}
            for row in request["collections"]
        ],
    }


def build_relationship_evidence_package(
    inventory, contract, roots, submission, *, now=None, require_complete=False
):
    """Return a validated v2 package and a summary without submitted IDs."""
    template = prepare_relationship_submission(inventory, contract, roots, now=now)
    if not isinstance(submission, dict) or set(submission) != set(template):
        raise ValueError("relationship_submission_fields_invalid")
    for field, expected in template.items():
        if field in {"provenance", "collections"}:
            continue
        value = submission[field]
        if type(value) is not type(expected) or value != expected:
            raise ValueError(f"relationship_submission_{field}_mismatch")
    submitted = submission["collections"]
    if not isinstance(submitted, list) or not submitted:
        raise ValueError("relationship_submission_collections_required")
    seen = set()
    entries = copy.deepcopy(roots["collections"])
    for row in submitted:
        if not isinstance(row, dict) or set(row) != {
            "name", "root_collection", "relationship_paths", "exact_root_ids"
        }:
            raise ValueError("relationship_submission_collection_fields_invalid")
        name = row["name"]
        if not isinstance(name, str) or name not in EXPECTED_RELATIONSHIP_COLLECTIONS:
            raise ValueError("relationship_submission_collection_not_allowed")
        if name in seen:
            raise ValueError("relationship_submission_collection_duplicate")
        seen.add(name)
        entries.append({
            "name": name, "requirement": RELATIONSHIP_REQUIREMENT,
            "evidence": {"root_collection": row["root_collection"],
                         "relationship_paths": copy.deepcopy(row["relationship_paths"]),
                         "root_ids": copy.deepcopy(row["exact_root_ids"])},
        })
    if require_complete and seen != EXPECTED_RELATIONSHIP_COLLECTIONS:
        raise ValueError("relationship_submission_incomplete")
    provenance = submission["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_FIELDS:
        raise ValueError("relationship_submission_provenance_invalid")
    # Bound the combined review to the source root approval's validity.
    prepared = _utc_timestamp(provenance.get("prepared_at"), "prepared_at")
    expires = _utc_timestamp(provenance.get("expires_at"), "expires_at")
    root_approved = _utc_timestamp(roots["provenance"]["approved_at"], "approved_at")
    root_expires = _utc_timestamp(roots["provenance"]["expires_at"], "expires_at")
    if prepared < root_approved or expires > root_expires:
        raise ValueError("relationship_submission_outside_root_validity")
    package = copy.deepcopy(roots)
    package["collections"] = sorted(entries, key=lambda row: row["name"])
    package["collections_sha256"] = _canonical_sha256(package["collections"])
    package["provenance"] = {**provenance, "evidence_id": ""}
    # Validate reviewer types before computing the legacy string binding.
    for field in ("prepared_by", "approved_by"):
        _reviewer_identity_key(provenance.get(field), field)
    package["provenance"]["evidence_id"] = _expected_evidence_id(
        inventory["_inventory_sha256"], contract["_contract_sha256"],
        package["collections_sha256"], package["provenance"],
    )
    rows = [
        {"name": name, "filter_requirement": requirement,
         "filter_status": "blocked_pending_evidence"}
        for requirement, names in (
            (ROOT_REQUIREMENT, ROOTS),
            (RELATIONSHIP_REQUIREMENT, EXPECTED_RELATIONSHIP_COLLECTIONS),
        ) for name in sorted(names)
    ]
    counts = apply_offline_filter_evidence(
        rows, package, inventory["_inventory_sha256"],
        contract["_contract_sha256"], now=now,
    )
    summary = {
        "scope": "root_and_relationship_evidence_only",
        "migration_authorized": False,
        "executable_queries_present": False,
        "document_values_present": False,
        "evidence_id": counts["evidence_id"],
        "package_sha256": _canonical_sha256(package),
        "request_sha256": template["request_sha256"],
        "root_package_sha256": template["root_package_sha256"],
        "approved_root_evidence_id": template["approved_root_evidence_id"],
        "root_collections_validated": len(ROOTS),
        "relationship_collections_validated": len(seen),
        "relationship_collections_total": len(EXPECTED_RELATIONSHIP_COLLECTIONS),
        "relationship_evidence_complete": seen == EXPECTED_RELATIONSHIP_COLLECTIONS,
        "pending_collections": sorted(EXPECTED_RELATIONSHIP_COLLECTIONS - seen),
    }
    return package, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build"))
    parser.add_argument("inventory", type=Path)
    parser.add_argument("root_package", type=Path)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--filter-contract", type=Path, default=(
        Path(__file__).resolve().parents[1] / "config/database_isolation_filter_contract.json"
    ))
    args = parser.parse_args()
    if args.action == "build" and args.submission is None:
        parser.error("build requires --submission")
    if args.action == "prepare" and (args.submission or args.require_complete):
        parser.error("prepare does not accept --submission or --require-complete")
    inventory = load_inventory(args.inventory)
    contract = load_filter_contract(args.filter_contract)
    roots = json.loads(args.root_package.read_text(encoding="utf-8-sig"))
    now = datetime.now(timezone.utc)
    if args.action == "prepare":
        output = prepare_relationship_submission(inventory, contract, roots, now=now)
        summary = {"status": "template_requires_independent_review",
                   "migration_authorized": False, "collections": len(output["collections"])}
    else:
        submission = json.loads(args.submission.read_text(encoding="utf-8-sig"))
        output, summary = build_relationship_evidence_package(
            inventory, contract, roots, submission, now=now,
            require_complete=args.require_complete,
        )
    write_private_package(args.output, output, Path(__file__).resolve().parents[1])
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
