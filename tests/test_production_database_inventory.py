import pytest

from scripts import production_database_inventory as exporter


def test_collects_all_cursor_pages_once(monkeypatch):
    pages = {
        "": {
            "success": True,
            "database_name": "taxportal",
            "collection_count": 3,
            "has_more": True,
            "next_cursor": "beta",
            "collections": [
                {"name": "alpha", "estimated_documents": 1, "index_count": 1},
                {"name": "beta", "estimated_documents": 2, "index_count": 1},
            ],
        },
        "beta": {
            "success": True,
            "database_name": "taxportal",
            "collection_count": 3,
            "has_more": False,
            "next_cursor": None,
            "collections": [
                {"name": "gamma", "estimated_documents": 3, "index_count": 2},
            ],
        },
    }
    calls = []
    monkeypatch.setattr(exporter, "BASE_URL", "https://api.rosshouserentals.com")
    monkeypatch.setattr(exporter, "TOKEN", "opaque-token")
    monkeypatch.setattr(
        exporter,
        "get_page",
        lambda after="": calls.append(after) or pages[after],
    )

    report = exporter.collect_inventory()

    assert calls == ["", "beta"]
    assert report["collection_count"] == 3
    assert [row["name"] for row in report["collections"]] == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_rejects_staging_before_network(monkeypatch):
    monkeypatch.setattr(
        exporter, "BASE_URL", "https://ross-house-staging.up.railway.app"
    )
    monkeypatch.setattr(exporter, "TOKEN", "opaque-token")

    with pytest.raises(RuntimeError, match="production_url_not_fail_closed"):
        exporter.collect_inventory()


def test_fails_if_page_sequence_is_incomplete(monkeypatch):
    monkeypatch.setattr(exporter, "BASE_URL", "https://api.example.com")
    monkeypatch.setattr(exporter, "TOKEN", "opaque-token")
    monkeypatch.setattr(
        exporter,
        "get_page",
        lambda after="": {
            "success": True,
            "database_name": "taxportal",
            "collection_count": 2,
            "has_more": False,
            "collections": [
                {"name": "only-one", "estimated_documents": 1, "index_count": 1}
            ],
        },
    )

    with pytest.raises(RuntimeError, match="database_inventory_incomplete"):
        exporter.collect_inventory()
