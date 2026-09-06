"""Metadata-only inventory used to plan database isolation safely."""
from __future__ import annotations

from typing import Any

MAX_COLLECTIONS = 250


async def build_database_isolation_inventory(db) -> dict[str, Any]:
    """Return collection names and aggregate counts without reading documents."""
    database_name = str(getattr(db, "name", "") or "")
    names = sorted(
        name
        for name in await db.list_collection_names()
        if not str(name).startswith("system.")
    )
    selected = names[:MAX_COLLECTIONS]
    collections = []
    for name in selected:
        collection = db[name]
        document_count = int(await collection.estimated_document_count())
        indexes = await collection.index_information()
        collections.append(
            {
                "name": name,
                "estimated_documents": document_count,
                "index_count": len(indexes),
            }
        )

    return {
        "database_name": database_name,
        "shared_database_detected": database_name.strip().lower() == "taxportal",
        "migration_required": database_name.strip().lower() == "taxportal",
        "collection_count": len(names),
        "collections_truncated": len(names) > MAX_COLLECTIONS,
        "collections": collections,
        "contains_documents": any(
            row["estimated_documents"] > 0 for row in collections
        ),
    }
