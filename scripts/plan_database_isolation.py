#!/usr/bin/env python3
"""Build a fail-closed, read-only collection ownership evidence report.

The script consumes the metadata-only JSON produced by
``production_database_inventory.py`` and scans this repository for exact
collection-name references. It never connects to MongoDB and never labels a
collection as safe to migrate automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from scripts.offline_evidence_json import load_offline_json, parse_offline_json
else:  # Preserve direct invocation: python scripts/plan_database_isolation.py.
    from offline_evidence_json import load_offline_json, parse_offline_json

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
FILTER_REQUIREMENTS = {
    "explicit_root_id_allowlist",
    "relationship_closure",
    "source_discriminator",
    "manual_schema_review",
}
FORBIDDEN_SOURCE_MARKERS = {
    "ross tax",
    "ross tax preparation",
    "ross lending",
    "tax portal",
    "taxportal",
    "loan",
    "loans",
    "lending",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELATIONSHIP_PATH_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
RENTALS_SOURCE_MARKERS = frozenset({
    "ross_house_rentals", "Ross House Rentals", "Ross House Rentals LLC",
})


def runtime_files(repository_root: Path) -> list[Path]:
    return sorted(
        path
        for path in repository_root.rglob("*")
        if path.is_file()
        and path.suffix in RUNTIME_SUFFIXES
        and not any(part in IGNORED_PARTS for part in path.parts)
    )


def load_inventory(path: Path) -> dict:
    raw = path.read_bytes()
    payload = parse_offline_json(raw)
    collections = payload.get("collections")
    expected = payload.get("collection_count")
    if not isinstance(collections, list) or type(expected) is not int or expected < 0:
        raise ValueError("inventory_schema_invalid")
    if any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
           for row in collections):
        raise ValueError("inventory_schema_invalid")
    names = [row["name"] for row in collections]
    if len(collections) != expected:
        raise ValueError("inventory_incomplete")
    if any(not name or name != name.strip() for name in names) or len(names) != len(set(names)):
        raise ValueError("inventory_duplicate_or_empty_collection")
    payload["_inventory_sha256"] = hashlib.sha256(raw).hexdigest()
    return payload


def load_rules(path: Path) -> dict:
    rules = load_offline_json(path)
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
    raw = path.read_bytes()
    contract = parse_offline_json(raw)
    if contract.get("source_database") != "taxportal":
        raise ValueError("filter_contract_source_invalid")
    if contract.get("target_database") != "ross_house_production":
        raise ValueError("filter_contract_target_invalid")
    if contract.get("migration_authorized") is not False:
        raise ValueError("filter_contract_must_be_fail_closed")
    if contract.get("default_action") != "block":
        raise ValueError("filter_contract_default_must_block")
    requirements = contract.get("requirements")
    if (
        not isinstance(requirements, dict)
        or set(requirements) != FILTER_REQUIREMENTS
    ):
        raise ValueError("filter_contract_requirements_invalid")
    contract["_contract_sha256"] = hashlib.sha256(raw).hexdigest()
    return contract


def apply_filter_contract(rows: list[dict], contract: dict) -> dict:
    assignments = {}
    for requirement, definition in contract["requirements"].items():
        collections = definition.get("collections")
        evidence = definition.get("evidence")
        if not isinstance(collections, list) or not evidence:
            raise ValueError(f"filter_requirement_invalid:{requirement}")
        for name in collections:
            if not isinstance(name, str) or not name:
                raise ValueError(f"filter_collection_invalid:{requirement}")
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


def _strict_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"filter_evidence_{field}_invalid")
    if any(
        not isinstance(item, str)
        or not item.strip()
        or item != item.strip()
        or "*" in item
        for item in value
    ):
        raise ValueError(f"filter_evidence_{field}_invalid")
    if len(value) != len(set(value)):
        raise ValueError(f"filter_evidence_{field}_duplicate")
    return value


def _validate_evidence_detail(requirement: str, detail: object) -> None:
    if not isinstance(detail, dict):
        raise ValueError("filter_evidence_detail_invalid")
    if requirement == "explicit_root_id_allowlist":
        if set(detail) != {"root_ids", "ownership_basis"}:
            raise ValueError("filter_evidence_allowlist_fields_invalid")
        _strict_strings(detail.get("root_ids"), "root_ids")
        basis = detail.get("ownership_basis")
        if not isinstance(basis, str) or not basis.strip() or "*" in basis:
            raise ValueError("filter_evidence_ownership_basis_invalid")
        if any(marker in basis.casefold() for marker in FORBIDDEN_SOURCE_MARKERS):
            raise ValueError("filter_evidence_external_source_prohibited")
    elif requirement == "relationship_closure":
        required = {"root_collection", "relationship_paths", "root_ids"}
        if set(detail) != required:
            raise ValueError("filter_evidence_relationship_fields_invalid")
        root = detail.get("root_collection")
        if not isinstance(root, str) or root not in {"app_users", "tenants"}:
            raise ValueError("filter_evidence_root_collection_invalid")
        paths = _strict_strings(
            detail.get("relationship_paths"), "relationship_paths"
        )
        if any(not RELATIONSHIP_PATH_RE.fullmatch(path) for path in paths):
            raise ValueError("filter_evidence_relationship_paths_invalid")
        _strict_strings(detail.get("root_ids"), "root_ids")
    elif requirement == "source_discriminator":
        if set(detail) != {"field_paths", "allowed_values"}:
            raise ValueError("filter_evidence_discriminator_fields_invalid")
        paths = _strict_strings(detail.get("field_paths"), "field_paths")
        if any(not RELATIONSHIP_PATH_RE.fullmatch(path) for path in paths):
            raise ValueError("filter_evidence_field_paths_invalid")
        values = _strict_strings(detail.get("allowed_values"), "allowed_values")
        normalized = {
            re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
            for value in values
        }
        if normalized & FORBIDDEN_SOURCE_MARKERS:
            raise ValueError("filter_evidence_external_source_prohibited")
        if not set(values).issubset(RENTALS_SOURCE_MARKERS):
            raise ValueError("filter_evidence_rentals_source_required")
    elif requirement == "manual_schema_review":
        required = {"field_paths", "ownership_basis", "reviewer", "reviewed_at"}
        if set(detail) != required:
            raise ValueError("filter_evidence_manual_review_fields_invalid")
        paths = _strict_strings(detail.get("field_paths"), "field_paths")
        if any(not RELATIONSHIP_PATH_RE.fullmatch(path) for path in paths):
            raise ValueError("filter_evidence_field_paths_invalid")
        for field in ("ownership_basis", "reviewer", "reviewed_at"):
            value = detail.get(field)
            if not isinstance(value, str) or not value.strip() or "*" in value:
                raise ValueError(f"filter_evidence_{field}_invalid")
        basis = detail["ownership_basis"].casefold()
        if any(marker in basis for marker in FORBIDDEN_SOURCE_MARKERS):
            raise ValueError("filter_evidence_external_source_prohibited")


def _validate_relationship_bindings(entries: list[dict]) -> None:
    """Cross-check already validated entries before approving any rows.

    A status label from a previous call is not root evidence. Requiring the
    root in this package binds both entries to the same hashes and provenance.
    """
    roots = {
        entry["name"]: set(entry["evidence"]["root_ids"])
        for entry in entries
        if entry["requirement"] == "explicit_root_id_allowlist"
        and entry["name"] in {"app_users", "tenants"}
    }
    for entry in entries:
        if entry["requirement"] != "relationship_closure":
            continue
        detail = entry["evidence"]
        allowed_ids = roots.get(detail["root_collection"])
        if allowed_ids is None:
            raise ValueError("filter_evidence_relationship_root_evidence_required")
        if not set(detail["root_ids"]).issubset(allowed_ids):
            # Do not echo document IDs or values into logs/errors.
            raise ValueError("filter_evidence_relationship_root_ids_not_allowlisted")


def _utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"filter_evidence_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"filter_evidence_{field}_invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"filter_evidence_{field}_invalid")
    return parsed


def _canonical_sha256(value: object) -> str:
    rendered = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _expected_evidence_id(
    inventory_sha256: str,
    contract_sha256: str,
    collections_sha256: str,
    provenance: dict,
) -> str:
    binding = ":".join(
        (
            inventory_sha256,
            contract_sha256,
            collections_sha256,
            provenance["prepared_by"],
            provenance["approved_by"],
            provenance["approved_at"],
        )
    )
    return "evd-" + hashlib.sha256(binding.encode("utf-8")).hexdigest()


def _reviewer_identity_key(value: object, field: str) -> str:
    """Compare declared identities; this does not authenticate a reviewer."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"filter_evidence_{field}_invalid")
    normalized = unicodedata.normalize("NFKC", value)
    if (
        not normalized.strip()
        or normalized != normalized.strip()
        or any(unicodedata.category(char).startswith("C") for char in value)
        or any(unicodedata.category(char).startswith("C") for char in normalized)
        or "*" in normalized
        or ":" in normalized
    ):
        # Colons would make the existing evidence-ID separator ambiguous.
        # Never include the submitted identity in the error.
        raise ValueError(f"filter_evidence_{field}_invalid")
    return " ".join(normalized.casefold().split())


def apply_offline_filter_evidence(
    rows: list[dict],
    evidence: dict,
    inventory_sha256: str,
    contract_sha256: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Approve bound offline evidence only; never build queries or authorize migration."""
    fixed = {
        "version": 2,
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "target_owner": "Ross House Rentals LLC",
        "migration_authorized": False,
        "default_action": "block",
    }
    allowed_fields = {
        *fixed,
        "inventory_sha256",
        "contract_sha256",
        "collections_sha256",
        "provenance",
        "collections",
    }
    if not isinstance(evidence, dict) or set(evidence) != allowed_fields:
        raise ValueError("filter_evidence_package_fields_invalid")
    for field, expected in fixed.items():
        if type(evidence.get(field)) is not type(expected) or evidence.get(field) != expected:
            raise ValueError(f"filter_evidence_{field}_invalid")
    for field, expected in (
        ("inventory_sha256", inventory_sha256),
        ("contract_sha256", contract_sha256),
    ):
        digest = evidence.get(field)
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"filter_evidence_{field}_invalid")
        if digest != expected:
            raise ValueError(f"filter_evidence_{field}_mismatch")
    entries = evidence.get("collections")
    if not isinstance(entries, list):
        raise ValueError("filter_evidence_collections_invalid")
    collections_sha256 = evidence.get("collections_sha256")
    if (
        not isinstance(collections_sha256, str)
        or not SHA256_RE.fullmatch(collections_sha256)
        or collections_sha256 != _canonical_sha256(entries)
    ):
        raise ValueError("filter_evidence_collections_hash_mismatch")
    provenance = evidence.get("provenance")
    provenance_fields = {
        "evidence_id",
        "prepared_by",
        "approved_by",
        "prepared_at",
        "approved_at",
        "expires_at",
    }
    if not isinstance(provenance, dict) or set(provenance) != provenance_fields:
        raise ValueError("filter_evidence_provenance_invalid")
    reviewer_keys = {
        field: _reviewer_identity_key(provenance.get(field), field)
        for field in ("prepared_by", "approved_by")
    }
    if reviewer_keys["prepared_by"] == reviewer_keys["approved_by"]:
        raise ValueError("filter_evidence_separation_of_duties_required")
    prepared_at = _utc_timestamp(provenance.get("prepared_at"), "prepared_at")
    approved_at = _utc_timestamp(provenance.get("approved_at"), "approved_at")
    expires_at = _utc_timestamp(provenance.get("expires_at"), "expires_at")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("filter_evidence_now_must_be_timezone_aware")
    current = current.astimezone(timezone.utc)
    if not prepared_at <= approved_at <= expires_at:
        raise ValueError("filter_evidence_timeline_invalid")
    if expires_at - approved_at > timedelta(days=30):
        raise ValueError("filter_evidence_validity_window_too_long")
    if current < approved_at:
        raise ValueError("filter_evidence_approval_not_yet_effective")
    if current >= expires_at:
        raise ValueError("filter_evidence_expired")
    expected_id = _expected_evidence_id(
        inventory_sha256, contract_sha256, collections_sha256, provenance
    )
    if provenance.get("evidence_id") != expected_id:
        raise ValueError("filter_evidence_id_binding_invalid")

    row_map = {
        row["name"]: row for row in rows if row.get("filter_requirement")
    }
    seen = set()
    approved_names = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"name", "requirement", "evidence"}
        ):
            raise ValueError("filter_evidence_entry_invalid")
        name, requirement = entry["name"], entry["requirement"]
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError(
                f"filter_evidence_collection_duplicate_or_invalid:{name}"
            )
        seen.add(name)
        row = row_map.get(name)
        if row is None:
            raise ValueError(f"filter_evidence_unknown_collection:{name}")
        if requirement != row["filter_requirement"]:
            raise ValueError(f"filter_evidence_requirement_mismatch:{name}")
        _validate_evidence_detail(requirement, entry["evidence"])
        if requirement == "manual_schema_review":
            detail = entry["evidence"]
            _reviewer_identity_key(detail["reviewer"], "reviewer")
            reviewed_at = _utc_timestamp(detail["reviewed_at"], "reviewed_at")
            if not approved_at - timedelta(days=30) <= reviewed_at <= approved_at:
                raise ValueError("filter_evidence_manual_review_timeline_invalid")
        approved_names.append(name)
    _validate_relationship_bindings(entries)
    for name in approved_names:
        row_map[name]["filter_status"] = "offline_evidence_approved"
        row_map[name]["evidence_id"] = provenance["evidence_id"]
    return {
        "submitted": len(entries),
        "approved": len(entries),
        "still_blocked": len(row_map) - len(entries),
        "evidence_id": provenance["evidence_id"],
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
    filter_evidence: dict | None = None,
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
    filter_evidence_counts = None
    if filter_evidence is not None:
        if filter_contract is None:
            raise ValueError("filter_evidence_requires_contract")
        filter_evidence_counts = apply_offline_filter_evidence(
            rows,
            filter_evidence,
            inventory["_inventory_sha256"],
            filter_contract["_contract_sha256"],
        )
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
        "inventory_sha256": inventory["_inventory_sha256"],
        "filter_contract_sha256": (
            filter_contract["_contract_sha256"] if filter_contract else None
        ),
        "collection_count": len(rows),
        "runtime_referenced_count": referenced,
        "unreferenced_count": len(rows) - referenced,
        "migration_authorized": False,
        "namespace_counts": namespace_counts,
        "strategy_counts": strategy_counts,
        "filter_contract_complete": filter_requirement_counts is not None,
        "filter_requirement_counts": filter_requirement_counts,
        "filter_evidence_counts": filter_evidence_counts,
        "external_runtime_dependencies": external_runtime_dependencies,
        "warning": (
            "Evidence only. External namespaces are prohibited from the target. "
            "No executable filters or migration authorization are produced."
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
    parser.add_argument("--filter-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "config"
        / "database_isolation_rules.json",
    )
    args = parser.parse_args()

    inventory = load_inventory(args.inventory)
    filter_evidence = (
        load_offline_json(args.filter_evidence)
        if args.filter_evidence
        else None
    )
    report = build_evidence(
        inventory,
        args.repository_root.resolve(),
        load_rules(args.rules),
        load_filter_contract(args.filter_contract),
        filter_evidence,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
