"""Verify preservation and review separation across an offline evidence chain."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from scripts.build_complete_evidence_package import DEFAULT_CONTRACT, evidence_coverage_report
from scripts.build_root_evidence_package import write_private_package
from scripts.offline_evidence_json import load_offline_json
from scripts.plan_database_isolation import (
    _canonical_sha256, _reviewer_identity_key, _utc_timestamp,
    load_filter_contract, load_inventory,
)


STAGES = ("root", "relationship", "complete")
EXPECTED_COUNTS = {"root": 2, "relationship": 32, "complete": 82}


def _entries(package):
    return {entry["name"]: entry for entry in package["collections"]}


def verify_evidence_chain(inventory, contract, root, relationship, complete, *, now=None):
    current = now if now is not None else datetime.now(timezone.utc)
    packages = {"root": root, "relationship": relationship, "complete": complete}
    reports = {stage: evidence_coverage_report(inventory, contract, package, now=current)
               for stage, package in packages.items()}
    for stage, expected in EXPECTED_COUNTS.items():
        if reports[stage]["contract_collections_validated"] != expected:
            raise ValueError(f"evidence_chain_{stage}_coverage_invalid")
    entries = {stage: _entries(package) for stage, package in packages.items()}
    if any(relationship_entry != entries["relationship"].get(name)
           for name, relationship_entry in entries["root"].items()):
        raise ValueError("evidence_chain_root_entries_changed")
    if any(relationship_entry != entries["complete"].get(name)
           for name, relationship_entry in entries["relationship"].items()):
        raise ValueError("evidence_chain_relationship_entries_changed")
    timestamps = {stage: {field: _utc_timestamp(package["provenance"][field], field)
                          for field in ("prepared_at", "approved_at", "expires_at")}
                  for stage, package in packages.items()}
    if not (timestamps["root"]["approved_at"] <= timestamps["relationship"]["prepared_at"]
            and timestamps["relationship"]["approved_at"] <= timestamps["complete"]["prepared_at"]):
        raise ValueError("evidence_chain_stage_timeline_invalid")
    if not (timestamps["complete"]["expires_at"] <= timestamps["relationship"]["expires_at"]
            <= timestamps["root"]["expires_at"]):
        raise ValueError("evidence_chain_expiry_widened")
    identities = [_reviewer_identity_key(package["provenance"][field], field)
                  for package in packages.values() for field in ("prepared_by", "approved_by")]
    identity_reuse = len(identities) - len(set(identities))
    outside = reports["complete"]["inventory_outside_contract_count"]
    blockers = []
    if identity_reuse:
        blockers.append("reviewer_identity_reused_across_stages")
    if outside:
        blockers.append("outside_contract_ownership_review_required")
    return {
        "report_type": "offline_evidence_chain_verification",
        "status": "review_required" if blockers else "offline_chain_checks_passed",
        "migration_authorized": False, "production_readiness_assessed": False,
        "ownership_independently_verified": False,
        "verified_at": current.astimezone(timezone.utc).isoformat(),
        "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "stages": {stage: {"package_sha256": _canonical_sha256(packages[stage]),
                            "collections_validated": EXPECTED_COUNTS[stage]}
                   for stage in STAGES},
        "root_entries_preserved": True, "relationship_entries_preserved": True,
        "stage_timeline_valid": True, "expiry_narrows_or_matches": True,
        "reviewer_identity_reuse_count": identity_reuse,
        "inventory_outside_contract_count": outside,
        "review_blockers": blockers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("root_package", type=Path)
    parser.add_argument("relationship_package", type=Path)
    parser.add_argument("complete_package", type=Path)
    parser.add_argument("--filter-contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = verify_evidence_chain(
            load_inventory(args.inventory), load_filter_contract(args.filter_contract),
            *(load_offline_json(path) for path in
              (args.root_package, args.relationship_package, args.complete_package)))
        if args.output:
            write_private_package(args.output, report, Path(__file__).resolve().parents[1])
    except (ValueError, OSError):
        print("offline_evidence_chain_verification_failed", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if report["review_blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
