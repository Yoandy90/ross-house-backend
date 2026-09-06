import pytest

from rental.database_isolation_inventory import (
    MAX_PAGE_SIZE,
    build_database_isolation_inventory,
)


class FakeCollection:
    def __init__(self, count, indexes):
        self.count = count
        self.indexes = indexes
        self.document_reads = 0

    async def estimated_document_count(self):
        return self.count

    async def index_information(self):
        return {name: {} for name in self.indexes}

    def find(self, *args, **kwargs):
        self.document_reads += 1
        raise AssertionError("inventory must never read documents")


class FakeDatabase:
    def __init__(self, name, collections):
        self.name = name
        self._collections = collections

    async def list_collection_names(self):
        return list(self._collections)

    def __getitem__(self, name):
        return self._collections[name]


@pytest.mark.asyncio
async def test_inventory_returns_only_metadata_and_detects_shared_database():
    collections = {
        "app_users": FakeCollection(12, ["_id_", "email_1"]),
        "properties": FakeCollection(4, ["_id_"]),
        "system.views": FakeCollection(99, ["_id_"]),
    }
    report = await build_database_isolation_inventory(
        FakeDatabase("taxportal", collections)
    )

    assert report["shared_database_detected"] is True
    assert report["migration_required"] is True
    assert report["collection_count"] == 2
    assert report["has_more"] is False
    assert report["next_cursor"] is None
    assert report["collections"] == [
        {"name": "app_users", "estimated_documents": 12, "index_count": 2},
        {"name": "properties", "estimated_documents": 4, "index_count": 1},
    ]
    assert all(item.document_reads == 0 for item in collections.values())


@pytest.mark.asyncio
async def test_dedicated_database_is_not_marked_for_migration():
    report = await build_database_isolation_inventory(
        FakeDatabase(
            "ross_house_production",
            {"properties": FakeCollection(0, ["_id_"])},
        )
    )

    assert report["shared_database_detected"] is False
    assert report["migration_required"] is False
    assert report["page_contains_documents"] is False


@pytest.mark.asyncio
async def test_inventory_cursor_pages_every_collection_once():
    collections = {
        f"collection_{index:03d}": FakeCollection(index, ["_id_"])
        for index in range(MAX_PAGE_SIZE + 3)
    }
    database = FakeDatabase("taxportal", collections)

    first = await build_database_isolation_inventory(database)
    second = await build_database_isolation_inventory(
        database,
        after=first["next_cursor"],
    )

    first_names = [row["name"] for row in first["collections"]]
    second_names = [row["name"] for row in second["collections"]]
    assert len(first_names) == MAX_PAGE_SIZE
    assert first["has_more"] is True
    assert first["next_cursor"] == first_names[-1]
    assert second_names == [
        f"collection_{index:03d}"
        for index in range(MAX_PAGE_SIZE, MAX_PAGE_SIZE + 3)
    ]
    assert second["has_more"] is False
    assert second["next_cursor"] is None
    assert len(set(first_names + second_names)) == MAX_PAGE_SIZE + 3


@pytest.mark.asyncio
async def test_requested_page_size_is_bounded():
    collections = {
        f"collection_{index:03d}": FakeCollection(index, ["_id_"])
        for index in range(MAX_PAGE_SIZE + 2)
    }
    report = await build_database_isolation_inventory(
        FakeDatabase("taxportal", collections),
        limit=MAX_PAGE_SIZE + 1000,
    )

    assert report["page_size"] == MAX_PAGE_SIZE
    assert report["has_more"] is True
