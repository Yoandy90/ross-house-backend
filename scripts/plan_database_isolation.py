#!/usr/bin/env python3
"""Build a fail-closed, read-only collection ownership evidence report.

The script consumes the metadata-only JSON produced by
``production_database_inventory.py`` and scans this repository for exact
collection-name references. It never connects to MongoDB and never labels a
collection as safe to migrate automatically.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

RUNTIME_SUFFIXES = {".py", ".js", ".ts", ".tsx"}
IGNORED_PARTS = {".git", ".venv", "node_modules", "tests", "scripts"}
EVIDENCE_GROUPS = (
    "rental_namespace_candidates",
    "external_namespace_candidates",
)
MIGRATION_STRATEGIES = (
    "blocked_conflict",
    "collection_copy_candidate",
    "dormant_rental_candidate",
    "prohibited_external_collection",
    "document_filter_required",
    "unreferenced_manual_review",
)


def runtime_files(repository_root: Path) -> list[Path]:
    return sorted(
        path
        for path in repository_root.rglob("*")
        if path.is_file()
        and path.suffix in RUNTIME_SUFFIXES
        and not any(part in IGNORED_PARTS for part in path.parts)
    )


def load_inventory(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    collections = payload.get("collections")
    expected = payload.get("collection_count")
    if not isinstance(collections, list) or not isinstance(expected, int):
        raise ValueError("inventory_schema_invalid")
    names = [str(row.get("name") or "") for row in collections]
    if len(collections) != expected:
        raise ValueError("inventory_incomplete")
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("inventory_duplicate_or_empty_collection")
    return payload


def load_rules(path: Path) -> dict:
    rules = json.loads(path.read_text(encoding="utf-8-sig"))
    if rules.get("source_database") != "taxportal":
        raise ValueError("source_database_must_be_taxportal")
    if rules.get("target_database") != "ross_house_production":
        raise ValueError("target_database_must_be_ross_house_production")
    if rules.get("target_owner") != "Ross House Rentals LLC":
        raise ValueError("target_owner_must_be_ross_house_rentals")
    if rules.get("exclusive_target") is not True:
        raise ValueError("target_database_must_be_exclusive")
    if rules.get("migration_authorized") is not False:
        raise ValueError("rules_must_be_fail_closed")
    for group in EVIDENCE_GROUPS:
        value = rules.get(group)
        if not isinstance(value, dict):
            raise ValueError(f"rules_group_invalid:{group}")
        for field in ("prefixes", "exact"):
            entries = value.get(field)
            if not isinstance(entries, list) or any(
                not isinstance(entry, str) or not entry for entry in entries
            ):
                raise ValueError(f"rules_entries_invalid:{group}:{field}")
    return rules


def load_filter_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8-sig"))
    if contract.get("source_database") != "taxportal":
        raise ValueError("filter_contract_source_invalid")
    if contract.get("target_database") != "ross_house_production":
        raise ValueError("filter_contract_target_invalid")
    if contract.get("migration_authorized") is not False:
        raise ValueError("filter_contract_must_be_fail_closed")
    if contract.get("default_action") != "block":
        raise ValueError("filter_contract_default_must_block")
    requirements = contract.get("requirements")
    if not isinstance(requirements, dict) or not requirements:
        raise ValueError("filter_contract_requirements_invalid")
    return contract


def apply_filter_contract(rows: list[dict], contract: dict) -> dict:
    assignments = {}
    for requirement, definition in contract["requirements"].items():
        collections = definition.get("collections")
        evidence = definition.get("evidence")
        if not isinstance(collections, list) or not evidence:
            raise ValueError(f"filter_requirement_invalid:{requirement}")
        for name in collections:
            if name in assignments:
                raise ValueError(f"filter_collection_duplicate:{name}")
            assignments[name] = requirement

    required = {
        row["name"]
        for row in rows
        if row["migration_strategy"] == "document_filter_required"
    }
    assigned = set(assignments)
    missing = sorted(required - assigned)
    extra = sorted(assigned - required)
    if missing:
        raise ValueError(f"filter_contract_missing:{','.join(missing)}")
    if extra:
        raise ValueError(f"filter_contract_extra:{','.join(extra)}")

    for row in rows:
        requirement = assignments.get(row["name"])
        row["filter_requirement"] = requirement
        if requirement:
            row["filter_status"] = "blocked_pending_evidence"

    return {
        requirement: len(definition["collections"])
        for requirement, definition in contract["requirements"].items()
    }


def namespace_evidence(name: str, rules: dict) -> str:
    matches = []
    for group in EVIDENCE_GROUPS:
        value = rules[group]
        if name in value["exact"] or any(
            name.startswith(prefix) for prefix in value["prefixes"]
        ):
            matches.append(group)
    if len(matches) > 1:
        return "conflicting_namespace_evidence"
    if matches:
        return matches[0]
    return "no_namespace_evidence"


def migration_strategy(evidence: str, has_runtime_references: bool) -> str:
    """Prioritize review without ever authorizing a migration operation."""
    if evidence == "conflicting_namespace_evidence":
        return "blocked_conflict"
    if evidence == "rental_namespace_candidates":
        return (
            "collection_copy_candidate"
            if has_runtime_references
            else "dormant_rental_candidate"
        )
    if evidence == "external_namespace_candidates":
        return "prohibited_external_collection"
    return (
        "document_filter_required"
        if has_runtime_references
        else "unreferenced_manual_review"
    )


def has_collection_reference(content: str, name: str) -> bool:
    """Match concrete database access, not prose or similarly named variables."""
    escaped = re.escape(name)
    database_expression = r"(?:db|[A-Za-z_][A-Za-z0-9_]*_db|get_db\(\))"
    direct_attribute = re.compile(
        rf"\b{database_expression}\s*\.\s*{escaped}\b"
    )
    direct_subscript = re.compile(
        rf"\b{database_expression}\s*\[\s*(['\"]){escaped}\1\s*\]"
    )
    if direct_attribute.search(content) or direct_subscript.search(content):
        return True

    constant_assignment = re.compile(
        rf"(?m)^\s*([A-Z][A-Z0-9_]*(?:COLL|COLLECTION)[A-Z0-9_]*)"
        rf"\s*=\s*(['\"]){escaped}\2\s*$"
    )
    for match in constant_assignment.finditer(content):
        constant = re.escape(match.group(1))
        indirect_subscript = re.compile(
            rf"\b{database_expression}\s*\[\s*{constant}\s*\]"
        )
        if indirect_subscript.search(content):
            return True
    return False


def build_evidence(
    inventory: dict,
    repository_root: Path,
    rules: dict,
    filter_contract: dict | None = None,
) -> dict:
    sources = {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in runtime_files(repository_root)
    }
    rows = []
    for collection in inventory["collections"]:
        name = str(collection["name"])
        references = [
            str(path.relative_to(repository_root))
            for path, content in sources.items()
            if has_collection_reference(content, name)
        ]
        evidence = namespace_evidence(name, rules)
        rows.append(
            {
                "name": name,
                "estimated_documents": int(
                    collection.get("estimated_documents") or 0
                ),
                "index_count": int(collection.get("index_count") or 0),
                "runtime_references": references,
                "namespace_evidence": evidence,
                "migration_strategy": migration_strategy(
                    evidence, bool(references)
                ),
                "review_status": "manual_review_required",
            }
        )

    referenced = sum(bool(row["runtime_references"]) for row in rows)
    namespace_counts = {
        status: sum(row["namespace_evidence"] == status for row in rows)
        for status in (
            *EVIDENCE_GROUPS,
            "conflicting_namespace_evidence",
            "no_namespace_evidence",
        )
    }
    strategy_counts = {
        strategy: sum(row["migration_strategy"] == strategy for row in rows)
        for strategy in MIGRATION_STRATEGIES
    }
    if sum(strategy_counts.values()) != len(rows):
        raise ValueError("migration_strategy_count_mismatch")
    filter_requirement_counts = None
    if filter_contract is not None:
        filter_requirement_counts = apply_filter_contract(rows, filter_contract)
    external_runtime_dependencies = [
        row["name"]
        for row in rows
        if row["namespace_evidence"] == "external_namespace_candidates"
        and row["runtime_references"]
    ]
    return {
        "source_database": str(inventory.get("database_name") or ""),
        "target_database": rules["target_database"],
        "target_owner": rules["target_owner"],
        "exclusive_target": True,
        "collection_count": len(rows),
        "runtime_referenced_count": referenced,
        "unreferenced_count": len(rows) - referenced,
        "migration_authorized": False,
        "namespace_counts": namespace_counts,
        "strategy_counts": strategy_counts,
        "filter_contract_complete": filter_requirement_counts is not None,
        "filter_requirement_counts": filter_requirement_counts,
        "external_runtime_dependencies": external_runtime_dependencies,
        "warning": (
            "Evidence only. External namespaces are prohibited from the target. "
            "Generic collections require document-level ownership filters."
        ),
        "collections": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--filter-contract",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "database_isolation_filter_contract.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "database_isolation_rules.json",
    )
    args = parser.parse_args()

    report = build_evidence(
        load_inventory(args.inventory),
        args.repository_root.resolve(),
        load_rules(args.rules),
        load_filter_contract(args.filter_contract),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
