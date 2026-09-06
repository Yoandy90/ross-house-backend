import json

import pytest

from scripts.plan_database_isolation import (
    build_evidence,
    apply_filter_contract,
    has_collection_reference,
    load_inventory,
    load_filter_contract,
    load_rules,
    migration_strategy,
    namespace_evidence,
)


RULES = {
    "source_database": "taxportal",
    "target_database": "ross_house_production",
    "target_owner": "Ross House Rentals LLC",
    "exclusive_target": True,
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
        'result = await db.properties.find_one({})\n', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "tax_test.py").write_text(
        'COLLECTION = "admin_tax_returns"\n', encoding="utf-8"
    )

    report = build_evidence(load_inventory(inventory_path), tmp_path, RULES)

    assert report["collection_count"] == 2
    assert report["source_database"] == "taxportal"
    assert report["target_database"] == "ross_house_production"
    assert report["target_owner"] == "Ross House Rentals LLC"
    assert report["exclusive_target"] is True
    assert report["runtime_referenced_count"] == 1
    assert report["unreferenced_count"] == 1
    assert report["migration_authorized"] is False
    assert report["namespace_counts"] == {
        "rental_namespace_candidates": 1,
        "external_namespace_candidates": 1,
        "conflicting_namespace_evidence": 0,
        "no_namespace_evidence": 0,
    }
    assert report["strategy_counts"] == {
        "blocked_conflict": 0,
        "collection_copy_candidate": 1,
        "dormant_rental_candidate": 0,
        "prohibited_external_collection": 1,
        "document_filter_required": 0,
        "unreferenced_manual_review": 0,
    }
    assert report["external_runtime_dependencies"] == []
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


@pytest.mark.parametrize(
    "evidence,referenced,expected",
    [
        ("conflicting_namespace_evidence", True, "blocked_conflict"),
        ("rental_namespace_candidates", True, "collection_copy_candidate"),
        ("rental_namespace_candidates", False, "dormant_rental_candidate"),
        (
            "external_namespace_candidates",
            True,
            "prohibited_external_collection",
        ),
        (
            "external_namespace_candidates",
            False,
            "prohibited_external_collection",
        ),
        ("no_namespace_evidence", True, "document_filter_required"),
        ("no_namespace_evidence", False, "unreferenced_manual_review"),
    ],
)
def test_migration_strategy_only_prioritizes_manual_review(
    evidence, referenced, expected
):
    assert migration_strategy(evidence, referenced) == expected


def test_reference_detection_ignores_prose_and_local_variable_names():
    content = '''
async def notify(db):
    """Legacy loans format is not a collection access."""
    admin_users = await db.app_users.find({}).to_list(50)
    return admin_users
'''

    assert has_collection_reference(content, "admin_users") is False
    assert has_collection_reference(content, "loans") is False
    assert has_collection_reference(content, "app_users") is True


def test_reference_detection_supports_collection_constants_and_subscripts():
    content = '''
VAULT_AUDIT_COLL = "vault_audit_log"
OTHER_VALUE = "loans"
await db[VAULT_AUDIT_COLL].insert_one({})
await db["rental_payments"].find_one({})
'''

    assert has_collection_reference(content, "vault_audit_log") is True
    assert has_collection_reference(content, "rental_payments") is True
    assert has_collection_reference(content, "loans") is False


def test_filter_contract_requires_exact_coverage_and_blocks_every_row():
    rows = [
        {"name": "app_users", "migration_strategy": "document_filter_required"},
        {"name": "properties", "migration_strategy": "collection_copy_candidate"},
    ]
    contract = {
        "requirements": {
            "explicit_root_id_allowlist": {
                "evidence": "approved IDs",
                "collections": ["app_users"],
            }
        }
    }

    counts = apply_filter_contract(rows, contract)

    assert counts == {"explicit_root_id_allowlist": 1}
    assert rows[0]["filter_status"] == "blocked_pending_evidence"
    assert rows[0]["filter_requirement"] == "explicit_root_id_allowlist"
    assert rows[1]["filter_requirement"] is None


def test_filter_contract_rejects_missing_duplicate_and_extra_collections():
    rows = [
        {"name": "app_users", "migration_strategy": "document_filter_required"}
    ]
    base = {"evidence": "required", "collections": []}

    with pytest.raises(ValueError, match="filter_contract_missing:app_users"):
        apply_filter_contract(rows, {"requirements": {"manual": base}})

    with pytest.raises(ValueError, match="filter_collection_duplicate:app_users"):
        apply_filter_contract(
            rows,
            {
                "requirements": {
                    "one": {"evidence": "required", "collections": ["app_users"]},
                    "two": {"evidence": "required", "collections": ["app_users"]},
                }
            },
        )

    with pytest.raises(ValueError, match="filter_contract_extra:users"):
        apply_filter_contract(
            rows,
            {
                "requirements": {
                    "manual": {
                        "evidence": "required",
                        "collections": ["app_users", "users"],
                    }
                }
            },
        )


def test_filter_contract_file_is_fail_closed(tmp_path):
    path = tmp_path / "filter-contract.json"
    path.write_text(
        json.dumps(
            {
                "source_database": "taxportal",
                "target_database": "ross_house_production",
                "migration_authorized": True,
                "default_action": "block",
                "requirements": {"manual": {"evidence": "required", "collections": []}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="filter_contract_must_be_fail_closed"):
        load_filter_contract(path)


@pytest.mark.parametrize(
    "override,error",
    [
        ({"source_database": "other"}, "source_database_must_be_taxportal"),
        (
            {"target_database": "taxportal"},
            "target_database_must_be_ross_house_production",
        ),
        (
            {"target_owner": "Other LLC"},
            "target_owner_must_be_ross_house_rentals",
        ),
        ({"exclusive_target": False}, "target_database_must_be_exclusive"),
    ],
)
def test_rules_enforce_exclusive_ross_house_target(tmp_path, override, error):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps({**RULES, **override}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match=error):
        load_rules(rules_path)
