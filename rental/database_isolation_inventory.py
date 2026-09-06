"""Metadata-only inventory used to plan database isolation safely."""
from __future__ import annotations

from typing import Any

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100


async def build_database_isolation_inventory(
    db,
    *,
    after: str = "",
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Return one bounded metadata page without reading collection documents."""
    database_name = str(getattr(db, "name", "") or "")
    names = sorted(
        name
        for name in await db.list_collection_names()
        if not str(name).startswith("system.")
    )
    cursor = str(after or "")
    page_size = max(1, min(int(limit), MAX_PAGE_SIZE))
    remaining = [name for name in names if name > cursor]
    selected = remaining[:page_size]

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

    has_more = len(remaining) > len(selected)
    next_cursor = selected[-1] if has_more and selected else None
    return {
        "database_name": database_name,
        "shared_database_detected": database_name.strip().lower() == "taxportal",
        "migration_required": database_name.strip().lower() == "taxportal",
        "collection_count": len(names),
        "page_size": len(selected),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "collections": collections,
        "page_contains_documents": any(
            row["estimated_documents"] > 0 for row in collections
        ),
    }
