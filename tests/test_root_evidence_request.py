import copy

import pytest

from scripts.prepare_root_evidence_request import build_root_evidence_request


def inventory():
    return {
        "database_name": "taxportal",
        "_inventory_sha256": "a" * 64,
        "collections": [
            {"name": "app_users", "estimated_documents": 18},
            {"name": "tenants", "estimated_documents": 1},
            {"name": "loans", "estimated_documents": 50},
        ],
    }


def contract():
    return {
        "source_database": "taxportal",
        "target_database": "ross_house_production",
        "migration_authorized": False,
        "default_action": "block",
        "_contract_sha256": "b" * 64,
        "requirements": {
            "explicit_root_id_allowlist": {
                "collections": ["app_users", "tenants"]
            }
        },
    }


def test_request_is_exact_hash_bound_and_non_executable():
    request = build_root_evidence_request(inventory(), contract())
    assert request["inventory_sha256"] == "a" * 64
    assert request["contract_sha256"] == "b" * 64
    assert request["migration_authorized"] is False
    assert request["default_action"] == "block"
    assert request["executable_queries_present"] is False
    assert request["document_values_present"] is False
    assert [row["name"] for row in request["collections"]] == [
        "app_users",
        "tenants",
    ]
    assert [row["estimated_documents"] for row in request["collections"]] == [
        18,
        1,
    ]
    rendered = str(request).casefold()
    assert "exact_root_ids" in rendered
    assert "$match" not in rendered
    assert "$in" not in rendered
    assert "loans" not in rendered


@pytest.mark.parametrize("extra", ["loans", "users", "admin_tax_returns"])
def test_external_or_unclassified_root_collection_is_rejected(extra):
    value = contract()
    value["requirements"]["explicit_root_id_allowlist"]["collections"].append(
        extra
    )
    with pytest.raises(ValueError, match="collections_must_be_exact"):
        build_root_evidence_request(inventory(), value)


def test_duplicate_or_missing_root_contract_assignment_is_rejected():
    duplicate = contract()
    duplicate["requirements"]["explicit_root_id_allowlist"]["collections"].append(
        "tenants"
    )
    with pytest.raises(ValueError, match="collections_must_be_exact"):
        build_root_evidence_request(inventory(), duplicate)

    missing = contract()
    missing["requirements"]["explicit_root_id_allowlist"]["collections"] = [
        "app_users"
    ]
    with pytest.raises(ValueError, match="collections_must_be_exact"):
        build_root_evidence_request(inventory(), missing)


@pytest.mark.parametrize("missing", ["app_users", "tenants"])
def test_inventory_drift_missing_a_required_root_is_rejected(missing):
    value = inventory()
    value["collections"] = [
        row for row in value["collections"] if row["name"] != missing
    ]
    with pytest.raises(ValueError, match=f"inventory_missing:{missing}"):
        build_root_evidence_request(value, contract())


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"database_name": "other"}, "source_database_invalid"),
        ({"_inventory_sha256": ""}, "inventory_sha256"),
    ],
)
def test_invalid_inventory_metadata_is_rejected(mutation, error):
    value = inventory()
    value.update(mutation)
    if error == "inventory_sha256":
        with pytest.raises(ValueError, match=error):
            build_root_evidence_request(value, contract())
    else:
        with pytest.raises(ValueError, match=error):
            build_root_evidence_request(value, contract())


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("target_database", "taxportal", "target_database_invalid"),
        ("migration_authorized", True, "migration_must_remain_unauthorized"),
        ("default_action", "allow", "default_must_block"),
    ],
)
def test_target_or_authorization_changes_are_rejected(field, value, error):
    modified = copy.deepcopy(contract())
    modified[field] = value
    with pytest.raises(ValueError, match=error):
        build_root_evidence_request(inventory(), modified)
