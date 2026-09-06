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
    "external_name_collision",
    "external_exclusion_candidate",
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
        return (
            "external_name_collision"
            if has_runtime_references
            else "external_exclusion_candidate"
        )
    return (
        "document_filter_required"
        if has_runtime_references
        else "unreferenced_manual_review"
    )


def build_evidence(inventory: dict, repository_root: Path, rules: dict) -> dict:
    sources = {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in runtime_files(repository_root)
    }
    rows = []
    for collection in inventory["collections"]:
        name = str(collection["name"])
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        )
        references = [
            str(path.relative_to(repository_root))
            for path, content in sources.items()
            if pattern.search(content)
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
    return {
        "database_name": str(inventory.get("database_name") or ""),
        "collection_count": len(rows),
        "runtime_referenced_count": referenced,
        "unreferenced_count": len(rows) - referenced,
        "migration_authorized": False,
        "namespace_counts": namespace_counts,
        "strategy_counts": strategy_counts,
        "warning": (
            "Evidence only. A runtime reference does not prove exclusive "
            "Ross House ownership. Every collection requires explicit review."
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
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
