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


def build_evidence(inventory: dict, repository_root: Path) -> dict:
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
        rows.append(
            {
                "name": name,
                "estimated_documents": int(
                    collection.get("estimated_documents") or 0
                ),
                "index_count": int(collection.get("index_count") or 0),
                "runtime_references": references,
                "review_status": "manual_review_required",
            }
        )

    referenced = sum(bool(row["runtime_references"]) for row in rows)
    return {
        "database_name": str(inventory.get("database_name") or ""),
        "collection_count": len(rows),
        "runtime_referenced_count": referenced,
        "unreferenced_count": len(rows) - referenced,
        "migration_authorized": False,
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
    args = parser.parse_args()

    report = build_evidence(
        load_inventory(args.inventory), args.repository_root.resolve()
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
