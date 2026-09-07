#!/usr/bin/env python3
"""Run a deterministic synthetic-only rehearsal of the offline evidence workflow."""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_complete_evidence_package import (
    DEFAULT_CONTRACT, build_complete_evidence_package,
    evidence_coverage_report, prepare_complete_submission,
)
from scripts.build_relationship_evidence_package import (
    build_relationship_evidence_package, prepare_relationship_submission,
)
from scripts.build_root_evidence_package import build_root_evidence_package, write_private_package
from scripts.plan_database_isolation import load_filter_contract, load_inventory

# An explicit simulation clock makes local and CI runs independent of wall time.
SIMULATION_TIME = datetime(2030, 1, 15, 12, tzinfo=timezone.utc)


def _provenance(stage):
    hours = {"root": (8, 9), "relationship": (9, 10), "complete": (10, 11)}
    prepared, approved = hours[stage]
    return {"prepared_by": "synthetic-preparer", "approved_by": "synthetic-approver",
            "prepared_at": f"2030-01-15T{prepared:02d}:00:00Z",
            "approved_at": f"2030-01-15T{approved:02d}:00:00Z",
            "expires_at": "2030-01-20T12:00:00Z"}


def _rejection_check(name, expected_error, action):
    try:
        action()
    except ValueError as error:
        # Only an expected validation failure proves the intended check ran.
        return {"name": name, "passed": expected_error in str(error)}
    return {"name": name, "passed": False}


def run_rehearsal():
    """Use only generated fixtures and the shipped contract; return no evidence values."""
    contract = load_filter_contract(DEFAULT_CONTRACT)
    names = sorted(n for d in contract["requirements"].values() for n in d["collections"])
    with tempfile.TemporaryDirectory(prefix="ross-house-evidence-rehearsal-") as folder:
        inventory_path = Path(folder) / "synthetic-inventory.json"
        inventory_path.write_text(json.dumps({"database_name": "taxportal",
            "collection_count": len(names) + 1,
            "collections": [{"name": n} for n in [*names, "synthetic_unclassified"]]}, sort_keys=True))
        inventory = load_inventory(inventory_path)
    submission = {"version": 1, "source_database": "taxportal",
        "target_database": "ross_house_production", "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False, "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"], "provenance": _provenance("root"),
        "collections": [{"name": n, "root_ids": [f"synthetic-only-{n}-001"],
                         "ownership_basis": "Synthetic Ross House Rentals rehearsal fixture"}
                        for n in ("app_users", "tenants")]}
    roots = build_root_evidence_package(inventory, contract, submission, now=SIMULATION_TIME)
    roots_report = evidence_coverage_report(inventory, contract, roots, now=SIMULATION_TIME)
    relation = prepare_relationship_submission(inventory, contract, roots, now=SIMULATION_TIME)
    relation["provenance"] = _provenance("relationship")
    for index, row in enumerate(relation["collections"]):
        root = ("app_users", "tenants")[index % 2]
        row.update(root_collection=root, relationship_paths=["owner.root_id"],
                   exact_root_ids=[f"synthetic-only-{root}-001"])
    relationships, _ = build_relationship_evidence_package(
        inventory, contract, roots, relation, now=SIMULATION_TIME, require_complete=True)
    relationship_report = evidence_coverage_report(inventory, contract, relationships, now=SIMULATION_TIME)
    supplemental = prepare_complete_submission(inventory, contract, relationships, now=SIMULATION_TIME)
    supplemental["provenance"] = _provenance("complete")
    for row in supplemental["collections"]:
        row["evidence"] = ({"field_paths": ["application.source"], "allowed_values": ["ross_house_rentals"]}
            if row["requirement"] == "source_discriminator" else {
                "field_paths": ["owner.root_id"], "ownership_basis": "Synthetic Ross House Rentals schema fixture",
                "reviewer": "synthetic-schema-reviewer", "reviewed_at": "2030-01-15T10:30:00Z"})
    complete, complete_report = build_complete_evidence_package(
        inventory, contract, relationships, supplemental, now=SIMULATION_TIME, require_complete=True)
    checks = [
        {"name": "root_stage", "passed": roots_report["contract_collections_validated"] == 2},
        {"name": "relationship_stage", "passed": relationship_report["contract_collections_validated"] == 32},
        {"name": "complete_stage", "passed": complete_report["contract_collections_validated"] == 82
            and complete_report["contract_evidence_complete"]},
        {"name": "unclassified_inventory_remains_unresolved", "passed":
            complete_report["inventory_outside_contract_count"] == 1
            and complete_report["outside_contract_ownership_review_required"]},
        {"name": "migration_remains_unauthorized", "passed": all(
            value["migration_authorized"] is False for value in (roots, relationships, complete, complete_report))},
    ]
    bad_relation = copy.deepcopy(relation)
    bad_relation["collections"][0]["exact_root_ids"] = ["synthetic-only-unrelated-001"]
    checks.append(_rejection_check("reject_foreign_root_id", "root_ids_not_allowlisted", lambda:
        build_relationship_evidence_package(inventory, contract, roots, bad_relation, now=SIMULATION_TIME)))
    bad_source = copy.deepcopy(supplemental)
    source = next(r for r in bad_source["collections"] if r["requirement"] == "source_discriminator")
    source["evidence"]["allowed_values"] = ["ross_tax"]
    checks.append(_rejection_check("reject_other_company", "external_source_prohibited", lambda:
        build_complete_evidence_package(inventory, contract, relationships, bad_source, now=SIMULATION_TIME)))
    bad_review = copy.deepcopy(supplemental)
    bad_review["provenance"]["approved_by"] = "SYNTHETIC-PREPARER"
    checks.append(_rejection_check("reject_self_approval", "separation_of_duties_required", lambda:
        build_complete_evidence_package(inventory, contract, relationships, bad_review, now=SIMULATION_TIME)))
    bad_binding = copy.deepcopy(supplemental)
    bad_binding["base_package_sha256"] = "0" * 64
    checks.append(_rejection_check("reject_stale_base", "base_package_sha256_mismatch", lambda:
        build_complete_evidence_package(inventory, contract, relationships, bad_binding, now=SIMULATION_TIME)))
    tampered = copy.deepcopy(complete)
    tampered["collections"][0]["evidence"]["unexpected"] = "synthetic-tampering"
    checks.append(_rejection_check("reject_tampering", "collections_hash_mismatch", lambda:
        evidence_coverage_report(inventory, contract, tampered, now=SIMULATION_TIME)))
    checks.append(_rejection_check("reject_expired_package", "expired", lambda:
        evidence_coverage_report(inventory, contract, complete,
                                 now=datetime(2030, 1, 21, tzinfo=timezone.utc))))
    partial = copy.deepcopy(supplemental)
    partial["collections"] = partial["collections"][:1]
    _, partial_report = build_complete_evidence_package(
        inventory, contract, relationships, partial, now=SIMULATION_TIME)
    checks.append({"name": "partial_batch_remains_pending", "passed":
        partial_report["contract_collections_validated"] == 33
        and partial_report["contract_evidence_complete"] is False})
    checks.append(_rejection_check("reject_incomplete_final_package", "incomplete_contract", lambda:
        build_complete_evidence_package(inventory, contract, relationships, partial,
                                         now=SIMULATION_TIME, require_complete=True)))
    return {
        "report_type": "synthetic_offline_evidence_rehearsal",
        "synthetic_only": True, "real_evidence_validated": False,
        "migration_authorized": False, "production_readiness_assessed": False,
        "simulation_time": SIMULATION_TIME.isoformat(),
        "contract_sha256": contract["_contract_sha256"],
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "checks_passed": sum(check["passed"] for check in checks),
        "checks_total": len(checks), "checks": checks,
        "stages": [{"stage": name, "synthetic_collections_validated": report["contract_collections_validated"]}
                   for name, report in (("roots", roots_report), ("relationships", relationship_report),
                                        ("partial_supplement", partial_report), ("complete_contract", complete_report))],
        "remaining_real_work": ["independent_ownership_evidence", "outside_contract_inventory_review",
                                "operational_readiness_review", "separate_migration_authorization"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional new private metadata report outside the repository")
    args = parser.parse_args()
    report = run_rehearsal()
    if args.output is not None:
        write_private_package(args.output, report, Path(__file__).resolve().parents[1])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
