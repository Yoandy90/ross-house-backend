"""Compare local inventory snapshots without carrying evidence forward."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from scripts.build_root_evidence_package import write_private_package
from scripts.plan_database_isolation import _canonical_sha256, load_inventory


def compare_inventory_snapshots(previous: Path, current: Path) -> dict:
    snapshots = [load_inventory(path) for path in (previous, current)]
    if any(snapshot.get("database_name") != "taxportal" for snapshot in snapshots):
        raise ValueError("inventory_comparison_scope_invalid")
    before, after = snapshots
    old = {row["name"]: row for row in before["collections"]}
    new = {row["name"]: row for row in after["collections"]}
    common = old.keys() & new.keys()
    # Canonical comparison preserves JSON type differences (True != 1 here).
    modified = sorted(name for name in common
                      if _canonical_sha256(old[name]) != _canonical_sha256(new[name]))
    top = lambda snapshot: {key: value for key, value in snapshot.items()
                           if key not in {"collections", "_inventory_sha256"}}
    metadata_changed = _canonical_sha256(top(before)) != _canonical_sha256(top(after))
    changed = before["_inventory_sha256"] != after["_inventory_sha256"]
    added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
    return {
        "report_type": "offline_inventory_comparison",
        "source_database": "taxportal", "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC", "migration_authorized": False,
        "ownership_assessed": False, "evidence_validated": False,
        "status": "changed_revalidation_required" if changed else "identical_snapshot",
        "previous_inventory_sha256": before["_inventory_sha256"],
        "current_inventory_sha256": after["_inventory_sha256"],
        "inventory_bytes_changed": changed,
        "previous_evidence_binding_invalidated": changed,
        "previous_collection_count": len(old), "current_collection_count": len(new),
        "added_collections": added, "removed_collections": removed,
        "modified_collection_metadata": modified,
        "unchanged_collection_metadata_count": len(common) - len(modified),
        "snapshot_metadata_changed": metadata_changed,
        "serialization_only_change": changed and not (added or removed or modified or metadata_changed),
        "next_action": "rebuild_and_independently_review_evidence" if changed
                       else "validate_evidence_scope_provenance_and_expiry_separately",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = compare_inventory_snapshots(args.previous, args.current)
        if args.output:
            write_private_package(args.output, report, Path(__file__).resolve().parents[1])
    except (ValueError, OSError):
        print("inventory_comparison_failed", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if report["inventory_bytes_changed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
