import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import build_root_evidence_package as builder
from scripts.offline_evidence_json import load_offline_json, parse_offline_json
from scripts.plan_database_isolation import (
    load_filter_contract, load_inventory, load_rules,
)


@pytest.mark.parametrize("raw", [
    b'{"migration_authorized":true,"migration_authorized":false}',
    b'{"collections":[{"root_ids":["private-id"],"root_ids":[]}]}',
    br'{"name":"private-value","na\u006de":"replacement"}',
    b'{"number":NaN}', b'{"number":Infinity}', b'{"number":-Infinity}',
    b'{"nested":[{"number":1e999}]}', b'{"number":-1e999}',
    b'{"private-key":}', b'{} {}', b'{"value":"\xff"}',
])
def test_rejects_ambiguous_or_invalid_json_without_input_in_error(raw):
    with pytest.raises(ValueError, match="^offline_json_invalid$") as caught:
        parse_offline_json(raw)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("raw", [b'[]', b'null', b'true', b'12', b'"text"'])
def test_requires_object_at_boundary(raw):
    with pytest.raises(ValueError, match="^offline_json_object_required$"):
        parse_offline_json(raw)


def test_accepts_bom_unicode_and_same_key_in_distinct_objects(tmp_path):
    raw = '\ufeff{"rows":[{"name":"José"},{"name":"Ana"}],"n":1.25}'.encode()
    path = tmp_path / "valid.json"
    path.write_bytes(raw)
    assert load_offline_json(path) == {
        "rows": [{"name": "José"}, {"name": "Ana"}], "n": 1.25,
    }


@pytest.mark.parametrize("loader", [load_inventory, load_filter_contract, load_rules])
def test_shared_metadata_loaders_reject_duplicates_before_schema(loader, tmp_path):
    path = tmp_path / "metadata.json"
    path.write_bytes(b'{"migration_authorized":true,"migration_authorized":false}')
    with pytest.raises(ValueError, match="^offline_json_invalid$"):
        loader(path)


def test_inventory_hash_uses_original_bytes_including_bom(tmp_path):
    raw = b'\xef\xbb\xbf{ "collections": [], "collection_count": 0 }\n'
    path = tmp_path / "inventory.json"
    path.write_bytes(raw)
    assert load_inventory(path)["_inventory_sha256"] == hashlib.sha256(raw).hexdigest()


def test_contract_hash_uses_original_bytes(tmp_path):
    source = Path(__file__).resolve().parents[1] / "config/database_isolation_filter_contract.json"
    raw = b'\xef\xbb\xbf' + source.read_bytes() + b'\n  '
    path = tmp_path / "contract.json"
    path.write_bytes(raw)
    assert load_filter_contract(path)["_contract_sha256"] == hashlib.sha256(raw).hexdigest()


def test_root_cli_rejects_duplicate_submission_without_publishing(tmp_path, monkeypatch, capsys):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"collections": [], "collection_count": 0}))
    submission = tmp_path / "submission.json"
    submission.write_text('{"migration_authorized":true,"migration_authorized":false}')
    output = tmp_path / "package.json"
    monkeypatch.setattr(sys, "argv", ["builder", str(inventory), str(submission), "--output", str(output)])
    with pytest.raises(ValueError, match="^offline_json_invalid$"):
        builder.main()
    assert not output.exists()
    assert capsys.readouterr().out == ""


def test_planner_preserves_direct_script_invocation(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/plan_database_isolation.py"
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(script), "--help"], cwd=tmp_path,
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--filter-evidence" in result.stdout
