"""Produce a value-free offline evidence review summary, never migration approval."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from scripts.build_complete_evidence_package import DEFAULT_CONTRACT, evidence_coverage_report
from scripts.build_root_evidence_package import write_private_package
from scripts.offline_evidence_json import load_offline_json
from scripts.plan_database_isolation import load_filter_contract, load_inventory, _utc_timestamp


def review_evidence(inventory, contract, package, *, now=None):
    current = now if now is not None else datetime.now(timezone.utc)
    coverage = evidence_coverage_report(inventory, contract, package, now=current)
    expires = _utc_timestamp(package["provenance"]["expires_at"], "expires_at")
    remaining = (expires - current).total_seconds()
    pending = coverage["contract_collections_total"] - coverage["contract_collections_validated"]
    outside = coverage["inventory_outside_contract_count"]
    blockers = []
    if pending:
        blockers.append("contract_evidence_incomplete")
    if outside:
        blockers.append("outside_contract_ownership_review_required")
    if remaining <= 24 * 60 * 60:
        blockers.append("evidence_expires_within_24_hours")
    # Explicit projection: never copy reviewer identities, evidence IDs,
    # collection names, paths, root IDs or submitted evidence values.
    return {
        "report_type": "offline_evidence_review",
        "status": "review_required" if blockers else "offline_contract_checks_passed",
        "migration_authorized": False, "production_readiness_assessed": False,
        "ownership_independently_verified": False,
        "reviewed_at": current.astimezone(timezone.utc).isoformat(),
        "expires_at": expires.isoformat(),
        "remaining_validity_seconds": remaining,
        "inventory_sha256": coverage["inventory_sha256"],
        "contract_sha256": coverage["contract_sha256"],
        "package_sha256": coverage["package_sha256"],
        "contract_collections_total": coverage["contract_collections_total"],
        "contract_collections_validated": coverage["contract_collections_validated"],
        "contract_collections_pending": pending,
        "inventory_outside_contract_count": outside,
        "requirements": {name: {"total": group["total"], "validated": group["validated"],
                                 "pending": len(group["pending_collections"])}
                         for name, group in coverage["requirements"].items()},
        "review_blockers": blockers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--filter-contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = review_evidence(load_inventory(args.inventory),
                                 load_filter_contract(args.filter_contract),
                                 load_offline_json(args.package))
        if args.output:
            write_private_package(args.output, report, Path(__file__).resolve().parents[1])
    except (ValueError, OSError):
        print("offline_evidence_review_failed", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if report["review_blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
