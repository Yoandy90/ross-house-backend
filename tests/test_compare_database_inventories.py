import json
import sys

import pytest

from scripts import compare_database_inventories as comparison
from scripts.plan_database_isolation import load_inventory


def snapshot():
    return {"database_name": "taxportal", "collection_count": 2,
            "collections": [{"name": "app_users", "estimated_documents": 2},
                            {"name": "tenants", "estimated_documents": 3}]}


def paths(tmp_path, before=None, after=None):
    old, new = tmp_path / "previous.json", tmp_path / "current.json"
    old.write_text(json.dumps(snapshot() if before is None else before))
    new.write_text(json.dumps(snapshot() if after is None else after))
    return old, new


def test_identical_snapshot_does_not_validate_evidence(tmp_path):
    report = comparison.compare_inventory_snapshots(*paths(tmp_path))
    assert report["status"] == "identical_snapshot"
    assert report["previous_evidence_binding_invalidated"] is False
    assert report["evidence_validated"] is False
    assert report["ownership_assessed"] is False
    assert report["migration_authorized"] is False


def test_added_removed_and_modified_collections_remain_unapproved(tmp_path):
    after = snapshot()
    after["collections"] = [{"name": "tenants", "estimated_documents": 9},
                            {"name": "new_collection", "private_metadata": "never-print-me"}]
    report = comparison.compare_inventory_snapshots(*paths(tmp_path, after=after))
    assert report["added_collections"] == ["new_collection"]
    assert report["removed_collections"] == ["app_users"]
    assert report["modified_collection_metadata"] == ["tenants"]
    assert report["previous_evidence_binding_invalidated"] is True
    assert "never-print-me" not in json.dumps(report)
    assert "private_metadata" not in json.dumps(report)


@pytest.mark.parametrize("change", ["whitespace", "bom", "keys", "rows"])
def test_nonsemantic_changes_still_invalidate_binding(tmp_path, change):
    old, new = paths(tmp_path)
    if change == "whitespace":
        new.write_bytes(old.read_bytes() + b'\n ')
    elif change == "bom":
        new.write_bytes(b'\xef\xbb\xbf' + old.read_bytes())
    elif change == "keys":
        new.write_text(json.dumps(snapshot(), sort_keys=True))
    else:
        value = snapshot()
        value["collections"].reverse()
        new.write_text(json.dumps(value))
    report = comparison.compare_inventory_snapshots(old, new)
    assert report["serialization_only_change"] is True
    assert report["previous_evidence_binding_invalidated"] is True


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_type_changes_are_visible(tmp_path, value):
    before, after = snapshot(), snapshot()
    before["collections"][0]["estimated_documents"] = 1
    after["collections"][0]["estimated_documents"] = value
    report = comparison.compare_inventory_snapshots(*paths(tmp_path, before, after))
    assert report["modified_collection_metadata"] == ["app_users"]


def test_snapshot_timestamp_change_invalidation(tmp_path):
    after = snapshot()
    after["generated_at"] = "synthetic-timestamp"
    report = comparison.compare_inventory_snapshots(*paths(tmp_path, after=after))
    assert report["snapshot_metadata_changed"] is True
    assert report["serialization_only_change"] is False
    assert report["previous_evidence_binding_invalidated"] is True


@pytest.mark.parametrize("rows,count", [([None], 1), ([{"name": 123}], 1),
    ([{"name": " tenants"}], 1), ([{"name": ""}], 1), ([], True), ([], -1),
    ([{"name": "tenants"}, {"name": "tenants"}], 2), ([], 1)])
def test_malformed_inventory_is_rejected_by_shared_loader(tmp_path, rows, count):
    value = snapshot()
    value.update(collections=rows, collection_count=count)
    old, _ = paths(tmp_path, before=value)
    with pytest.raises(ValueError):
        load_inventory(old)


@pytest.mark.parametrize("side", [0, 1])
def test_rejects_wrong_source_on_either_side(tmp_path, side):
    values = [snapshot(), snapshot()]
    values[side]["database_name"] = "ross_house_production"
    with pytest.raises(ValueError, match="inventory_comparison_scope_invalid"):
        comparison.compare_inventory_snapshots(*paths(tmp_path, *values))


@pytest.mark.parametrize("changed", [False, True])
def test_cli_publishes_private_report_and_meaningful_exit_code(tmp_path, monkeypatch, capsys, changed):
    old, new = paths(tmp_path)
    if changed:
        new.write_bytes(new.read_bytes() + b'\n')
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["compare", str(old), str(new), "--output", str(output)])
    assert comparison.main() == (2 if changed else 0)
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(capsys.readouterr().out) == json.loads(output.read_text())


@pytest.mark.parametrize("failure", ["duplicate", "missing", "existing_output"])
def test_cli_failure_does_not_publish_or_leak_inputs(tmp_path, monkeypatch, capsys, failure):
    old, new = paths(tmp_path)
    output = tmp_path / "report.json"
    if failure == "duplicate":
        new.write_text('{"private-key":"private-value","private-key":false}')
    elif failure == "missing":
        new.unlink()
    else:
        output.write_text("preserve-existing")
    monkeypatch.setattr(sys, "argv", ["compare", str(old), str(new), "--output", str(output)])
    assert comparison.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "inventory_comparison_failed\n"
    if failure == "existing_output":
        assert output.read_text() == "preserve-existing"
    else:
        assert not output.exists()
