import asyncio
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException

import rental.listing_feed_router as feed
import rental.property_visibility_security_router as visibility
from rental.auth_metrics import router as security_router
from rental.properties_router import router as historical_properties_router


def run(coro):
    return asyncio.run(coro)


class AsyncCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, _limit):
        return list(self.docs)


class Properties:
    def __init__(self, docs=None, archived=None):
        self.docs = list(docs or [])
        self.archived = archived
        self.find_queries = []

    def find(self, query, *_args, **_kwargs):
        self.find_queries.append(query)
        active = [d for d in self.docs if not d.get("archived_at")]
        return AsyncCursor(active)

    async def find_one(self, query, *_args, **_kwargs):
        if self.archived and query.get("_id") == self.archived.get("_id"):
            return self.archived
        return None


class Units:
    def find(self, _query, *_args, **_kwargs):
        return AsyncCursor([])

    async def find_one(self, _query, *_args, **_kwargs):
        return None


class Photos:
    def find(self, _query, *_args, **_kwargs):
        return AsyncCursor([])


class Result:
    matched_count = 1


class DB:
    def __init__(self, props=None, archived=None):
        self.properties = Properties(props, archived)
        self.property_units = Units()
        self.property_photos = Photos()


async def no_photos(_property_id):
    return []


def test_available_listings_queries_only_unarchived_inventory(monkeypatch):
    active = {
        "_id": ObjectId(),
        "address": "101 Active St",
        "status": "available",
        "rent_amount": 1000,
        "deposit_amount": 1000,
    }
    archived = {
        "_id": ObjectId(),
        "address": "102 Archived St",
        "status": "available",
        "archived_at": "2026-08-29T00:00:00Z",
        "rent_amount": 900,
        "deposit_amount": 900,
    }
    db = DB([active, archived])
    monkeypatch.setattr(feed, "get_db", lambda: db)
    monkeypatch.setattr(feed, "_photo_urls", no_photos)

    listings = run(feed._available_listings())

    assert [row["property_id"] for row in listings] == [str(active["_id"])]
    query = db.properties.find_queries[0]
    assert query == {
        "$or": [
            {"archived_at": {"$exists": False}},
            {"archived_at": None},
        ]
    }


def test_archived_photo_catalog_fails_closed_before_historical_delegate(monkeypatch):
    archived = {"_id": ObjectId(), "archived_at": "2026-08-29T00:00:00Z"}
    db = DB(archived=archived)
    called = {"value": False}

    async def historical(_property_id):
        called["value"] = True
        return {"success": True, "photos": ["should-not-leak"]}

    monkeypatch.setattr(visibility, "get_db", lambda: db)
    monkeypatch.setattr(visibility, "historical_public_list_property_photos", historical)

    with pytest.raises(HTTPException) as exc:
        run(visibility.secure_public_list_property_photos(str(archived["_id"])))
    assert exc.value.status_code == 404
    assert called["value"] is False


def test_active_photo_catalog_delegates(monkeypatch):
    property_id = str(ObjectId())
    db = DB()

    async def historical(value):
        assert value == property_id
        return {"success": True, "photos": ["ok"]}

    monkeypatch.setattr(visibility, "get_db", lambda: db)
    monkeypatch.setattr(visibility, "historical_public_list_property_photos", historical)

    assert run(visibility.secure_public_list_property_photos(property_id))["photos"] == ["ok"]


def test_photo_visibility_route_is_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_properties_router, prefix="/api")
    matches = [
        r for r in app.routes
        if getattr(r, "path", None) == "/api/public/property-photos/{property_id}"
        and "GET" in getattr(r, "methods", set())
    ]
    assert len(matches) == 2
    assert matches[0].name == "secure_public_list_property_photos"
    assert matches[1].name == "public_list_property_photos"


def test_listing_publication_guards_archival_and_unit_ownership():
    source = Path("rental/listing_feed_router.py").read_text(encoding="utf-8")
    assert "_ACTIVE_PROPERTY_FILTER" in source
    assert 'if prop.get("archived_at"):' in source
    assert 'detail="property_archived"' in source
    assert 'detail="unit_property_mismatch"' in source
    assert '"$or": [{"archived_at": {"$exists": False}}, {"archived_at": None}]' in source


def test_legacy_application_is_not_property_id_authority():
    source = Path("rental/properties_router.py").read_text(encoding="utf-8")
    start = source.index("async def submit_rental_application")
    end = source.find("\n@router.", start + 1)
    application_source = source[start:] if end == -1 else source[start:end]
    assert 'property_interest = (data.get("property_interest") or "").strip()' in application_source
    assert 'data.get("property_id")' not in application_source
