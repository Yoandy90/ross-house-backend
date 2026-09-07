#!/usr/bin/env python3
"""Complete and report on the offline ownership evidence contract."""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.offline_evidence_json import load_offline_json

from scripts.build_root_evidence_package import PROVENANCE_FIELDS, write_private_package
from scripts.plan_database_isolation import (
    _canonical_sha256, _expected_evidence_id,
    _reviewer_identity_key, _utc_timestamp, apply_offline_filter_evidence,
    load_filter_contract, load_inventory,
)

DEFAULT_CONTRACT = Path(__file__).resolve().parents[1] / "config/database_isolation_filter_contract.json"
SUPPLEMENTAL = {"source_discriminator", "manual_schema_review"}


def _context(inventory, contract):
    reference = load_filter_contract(DEFAULT_CONTRACT)
    for field in ("source_database", "target_database", "migration_authorized", "default_action"):
        if type(contract.get(field)) is not type(reference[field]) or contract.get(field) != reference[field]:
            raise ValueError("complete_evidence_contract_scope_invalid")
    if inventory.get("database_name") != "taxportal":
        raise ValueError("complete_evidence_inventory_scope_invalid")
    definitions = contract.get("requirements")
    if not isinstance(definitions, dict) or set(definitions) != set(reference["requirements"]):
        raise ValueError("complete_evidence_contract_requirements_invalid")
    assignments = {}
    for requirement, expected in reference["requirements"].items():
        definition = definitions[requirement]
        names = definition.get("collections") if isinstance(definition, dict) else None
        if (not isinstance(names, list) or any(not isinstance(n, str) for n in names)
                or len(names) != len(set(names)) or set(names) != set(expected["collections"])):
            raise ValueError("complete_evidence_contract_collections_invalid")
        assignments.update({name: requirement for name in names})
    inventory_rows = inventory.get("collections")
    if not isinstance(inventory_rows, list):
        raise ValueError("complete_evidence_inventory_invalid")
    names = [row.get("name") if isinstance(row, dict) else None for row in inventory_rows]
    if any(not isinstance(n, str) or not n for n in names) or len(names) != len(set(names)):
        raise ValueError("complete_evidence_inventory_invalid")
    if not set(assignments).issubset(names):
        raise ValueError("complete_evidence_inventory_missing_contract_collections")
    return assignments, set(names)


def _validate(inventory, contract, package, *, now=None):
    assignments, inventory_names = _context(inventory, contract)
    if (not isinstance(package, dict) or type(package.get("version")) is not int
            or package.get("version") != 2 or package.get("migration_authorized") is not False):
        raise ValueError("complete_evidence_package_scope_invalid")
    rows = [{"name": n, "filter_requirement": requirement,
             "filter_status": "blocked_pending_evidence"}
            for n, requirement in sorted(assignments.items())]
    apply_offline_filter_evidence(rows, package, inventory["_inventory_sha256"],
                                  contract["_contract_sha256"], now=now)
    entries = {row["name"]: row for row in package["collections"]}
    if not {"app_users", "tenants"}.issubset(entries):
        raise ValueError("complete_evidence_both_roots_required")
    # Supplemental source/path/review checks live in the shared validator so
    # direct planner inputs receive the same checks as this builder/report.
    return assignments, inventory_names, entries


def evidence_coverage_report(inventory, contract, package, *, now=None):
    assignments, inventory_names, entries = _validate(inventory, contract, package, now=now)
    groups = {}
    for requirement in sorted(set(assignments.values())):
        names = {n for n, r in assignments.items() if r == requirement}
        groups[requirement] = {"total": len(names), "validated": len(names & entries.keys()),
                               "pending_collections": sorted(names - entries.keys())}
    return {
        "scope": "offline_filter_contract_only", "migration_authorized": False,
        "executable_queries_present": False, "document_values_present": False,
        "package_sha256": _canonical_sha256(package),
        "inventory_sha256": inventory["_inventory_sha256"],
        "contract_sha256": contract["_contract_sha256"],
        "evidence_id": package["provenance"]["evidence_id"],
        "contract_collections_total": len(assignments),
        "contract_collections_validated": len(entries),
        "contract_evidence_complete": len(entries) == len(assignments),
        "requirements": groups,
        "inventory_outside_contract_count": len(inventory_names - assignments.keys()),
        "outside_contract_ownership_review_required": bool(inventory_names - assignments.keys()),
    }


def prepare_complete_submission(inventory, contract, base, *, now=None):
    assignments, _, entries = _validate(inventory, contract, base, now=now)
    pending = []
    for name, requirement in sorted(assignments.items()):
        if requirement not in SUPPLEMENTAL or name in entries:
            continue
        detail = ({"field_paths": [], "allowed_values": []} if requirement == "source_discriminator"
                  else {"field_paths": [], "ownership_basis": "", "reviewer": "", "reviewed_at": ""})
        pending.append({"name": name, "requirement": requirement, "evidence": detail})
    return {"version": 1, "source_database": "taxportal",
            "target_database": "ross_house_production", "target_owner": "Ross House Rentals LLC",
            "migration_authorized": False, "inventory_sha256": inventory["_inventory_sha256"],
            "contract_sha256": contract["_contract_sha256"], "base_package_sha256": _canonical_sha256(base),
            "base_evidence_id": base["provenance"]["evidence_id"],
            "provenance": {field: "" for field in sorted(PROVENANCE_FIELDS)}, "collections": pending}


def build_complete_evidence_package(inventory, contract, base, submission, *, now=None, require_complete=False):
    template = prepare_complete_submission(inventory, contract, base, now=now)
    if not isinstance(submission, dict) or set(submission) != set(template):
        raise ValueError("complete_submission_fields_invalid")
    for field, expected in template.items():
        if field in {"provenance", "collections"}:
            continue
        if type(submission[field]) is not type(expected) or submission[field] != expected:
            raise ValueError(f"complete_submission_{field}_mismatch")
    submitted = submission["collections"]
    if not isinstance(submitted, list) or not submitted:
        raise ValueError("complete_submission_collections_required")
    available = {row["name"]: row["requirement"] for row in template["collections"]}
    seen = set()
    for row in submitted:
        if not isinstance(row, dict) or set(row) != {"name", "requirement", "evidence"}:
            raise ValueError("complete_submission_entry_invalid")
        name = row["name"]
        if (not isinstance(name, str) or name not in available or name in seen
                or row["requirement"] != available[name]):
            raise ValueError("complete_submission_collection_invalid")
        seen.add(name)
    provenance = submission["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_FIELDS:
        raise ValueError("complete_submission_provenance_invalid")
    for field in ("prepared_by", "approved_by"):
        _reviewer_identity_key(provenance[field], field)
    prepared = _utc_timestamp(provenance["prepared_at"], "prepared_at")
    expires = _utc_timestamp(provenance["expires_at"], "expires_at")
    if (prepared < _utc_timestamp(base["provenance"]["approved_at"], "approved_at")
            or expires > _utc_timestamp(base["provenance"]["expires_at"], "expires_at")):
        raise ValueError("complete_submission_outside_base_validity")
    package = copy.deepcopy(base)
    package["collections"] = sorted(copy.deepcopy(base["collections"] + submitted), key=lambda row: row["name"])
    package["collections_sha256"] = _canonical_sha256(package["collections"])
    package["provenance"] = {**provenance, "evidence_id": ""}
    package["provenance"]["evidence_id"] = _expected_evidence_id(
        inventory["_inventory_sha256"], contract["_contract_sha256"],
        package["collections_sha256"], package["provenance"],
    )
    report = evidence_coverage_report(inventory, contract, package, now=now)
    if require_complete and not report["contract_evidence_complete"]:
        raise ValueError("complete_submission_incomplete_contract")
    report["base_package_sha256"] = template["base_package_sha256"]
    report["base_evidence_id"] = template["base_evidence_id"]
    return package, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build", "report"))
    parser.add_argument("inventory", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--filter-contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    if args.action in {"prepare", "build"} and args.output is None:
        parser.error("prepare/build require --output")
    if args.action == "build" and args.submission is None:
        parser.error("build requires --submission")
    if args.action != "build" and (args.submission or args.require_complete):
        parser.error("--submission and --require-complete are build-only")
    inventory, contract = load_inventory(args.inventory), load_filter_contract(args.filter_contract)
    package = load_offline_json(args.package)
    now = datetime.now(timezone.utc)
    if args.action == "prepare":
        output = prepare_complete_submission(inventory, contract, package, now=now)
        report = {"status": "template_requires_independent_review", "migration_authorized": False,
                  "pending_supplemental_collections": len(output["collections"])}
    elif args.action == "build":
        submission = load_offline_json(args.submission)
        output, report = build_complete_evidence_package(inventory, contract, package, submission,
                              now=now, require_complete=args.require_complete)
    else:
        report = evidence_coverage_report(inventory, contract, package, now=now)
        output = report
    if args.output is not None:
        write_private_package(args.output, output, Path(__file__).resolve().parents[1])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
