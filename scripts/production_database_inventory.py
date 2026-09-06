#!/usr/bin/env python3
"""Collect every metadata-only database inventory page using GET requests."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("PRODUCTION_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("PRODUCTION_ADMIN_TOKEN", "")
MAX_PAGES = 20


def fail(code: str) -> None:
    raise RuntimeError(code)


def get_page(after: str = "") -> dict:
    query = urllib.parse.urlencode({"limit": 100, "after": after})
    request = urllib.request.Request(
        f"{BASE_URL}/api/admin/operations/database-isolation-inventory?{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        fail(f"http_{exc.code}:database_inventory")


def validate_target() -> None:
    parsed = urllib.parse.urlparse(BASE_URL)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or "staging" in hostname
        or hostname in {"localhost", "127.0.0.1"}
    ):
        fail("production_url_not_fail_closed")
    if not TOKEN:
        fail("production_admin_token_missing")


def collect_inventory() -> dict:
    validate_target()
    collections = []
    seen = set()
    after = ""
    database_name = None
    total = None

    for _ in range(MAX_PAGES):
        page = get_page(after)
        if page.get("success") is not True:
            fail("database_inventory_page_invalid")
        page_database = str(page.get("database_name") or "")
        if database_name is None:
            database_name = page_database
            total = int(page.get("collection_count") or 0)
        elif page_database != database_name:
            fail("database_inventory_changed_database")

        for row in page.get("collections") or []:
            name = str(row.get("name") or "")
            if not name or name in seen:
                fail("database_inventory_duplicate_or_empty_collection")
            seen.add(name)
            collections.append(row)

        if not page.get("has_more"):
            break
        next_cursor = str(page.get("next_cursor") or "")
        if not next_cursor or next_cursor == after:
            fail("database_inventory_cursor_invalid")
        after = next_cursor
    else:
        fail("database_inventory_page_limit_exceeded")

    if len(collections) != total:
        fail(f"database_inventory_incomplete:{len(collections)}:{total}")
    return {
        "database_name": database_name,
        "collection_count": total,
        "collections": collections,
    }


def main() -> int:
    print(json.dumps(collect_inventory(), indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"PRODUCTION_DATABASE_INVENTORY_FAILED:{exc}", file=sys.stderr)
        sys.exit(1)
