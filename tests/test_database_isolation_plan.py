import json

import pytest

from scripts.plan_database_isolation import (
    build_evidence,
    load_inventory,
    load_rules,
    namespace_evidence,
)


RULES = {
    "migration_authorized": False,
    "rental_namespace_candidates": {
        "prefixes": ["rental_", "property_"],
        "exact": ["properties"],
    },
    "external_namespace_candidates": {
        "prefixes": ["tax_"],
        "exact": ["admin_tax_returns"],
    },
}


def write_inventory(path, *, count=2, collections=None):
    rows = collections or [
        {"name": "properties", "estimated_documents": 2, "index_count": 1},
        {"name": "admin_tax_returns", "estimated_documents": 8, "index_count": 2},
    ]
    path.write_text(
        json.dumps(
            {
                "database_name": "taxportal",
                "collection_count": count,
                "collections": rows,
            }
        ),
        encoding="utf-8",
    )


def test_plan_reports_runtime_evidence_without_authorizing_migration(tmp_path):
    inventory_path = tmp_path / "inventory.json"
    write_inventory(inventory_path)
    (tmp_path / "rental").mkdir()
    (tmp_path / "rental" / "properties_router.py").write_text(
        'COLLECTION = "properties"\n', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "tax_test.py").write_text(
        'COLLECTION = "admin_tax_returns"\n', encoding="utf-8"
    )

    report = build_evidence(load_inventory(inventory_path), tmp_path, RULES)

    assert report["collection_count"] == 2
    assert report["runtime_referenced_count"] == 1
    assert report["unreferenced_count"] == 1
    assert report["migration_authorized"] is False
    assert report["namespace_counts"] == {
        "rental_namespace_candidates": 1,
        "external_namespace_candidates": 1,
        "conflicting_namespace_evidence": 0,
        "no_namespace_evidence": 0,
    }
    assert report["collections"][0]["runtime_references"] == [
        "rental/properties_router.py"
    ]
    assert all(
        row["review_status"] == "manual_review_required"
        for row in report["collections"]
    )


@pytest.mark.parametrize(
    "count,collections,error",
    [
        (2, [{"name": "properties"}], "inventory_incomplete"),
        (
            2,
            [{"name": "properties"}, {"name": "properties"}],
            "inventory_duplicate_or_empty_collection",
        ),
    ],
)
def test_plan_rejects_incomplete_or_duplicate_inventory(
    tmp_path, count, collections, error
):
    inventory_path = tmp_path / "inventory.json"
    write_inventory(inventory_path, count=count, collections=collections)

    with pytest.raises(ValueError, match=error):
        load_inventory(inventory_path)


def test_namespace_conflicts_remain_blocked_for_manual_review():
    rules = {
        **RULES,
        "external_namespace_candidates": {
            "prefixes": ["property_"],
            "exact": [],
        },
    }

    assert (
        namespace_evidence("property_documents", rules)
        == "conflicting_namespace_evidence"
    )


def test_rules_file_must_never_authorize_migration(tmp_path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps({**RULES, "migration_authorized": True}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="rules_must_be_fail_closed"):
        load_rules(rules_path)
