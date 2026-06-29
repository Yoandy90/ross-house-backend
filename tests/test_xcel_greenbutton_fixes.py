"""
Backend unit tests for the Xcel Green Button Connect refactor (Jun 2026).

Validates the 4 critical fixes against the GBC Vendor Startup Guide:
  1. New route GET /admin/xcel/read-service-status exists.
  2. _get_client_credentials_token uses BasicAuth + grant_type=client_credentials
     + scope=FB=34_35, and caches the token across calls.
  3. xcel_sync_connection now POSTs (not GETs) to /Batch/Subscription/{sub_id}
     and treats HTTP 202 as success.
  4. _ingest_espi_xml exists and persists interval readings + summaries.
  5. xcel_notify extracts URLs from <resources> tags and downloads them.

All HTTP and DB calls are mocked. No real services required.
"""
import asyncio
import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from rental import xcel_energy_router as xer  # noqa: E402
from rental import shared as rshared  # noqa: E402


# ───────────────────────────── Helpers / fakes ─────────────────────────────

class FakeCollection:
    """Async-iterable / async-method-capable Mongo collection stub."""
    def __init__(self):
        self.docs = []
        self.updates = []
        self.inserts = []

    async def find_one(self, query=None, *args, **kwargs):
        return None

    async def update_one(self, filt, update, upsert=False):
        self.updates.append({"filter": filt, "update": update, "upsert": upsert})
        return MagicMock(matched_count=1, modified_count=1, upserted_id=None)

    async def insert_one(self, doc):
        self.inserts.append(doc)
        return MagicMock(inserted_id="fake-id")

    async def delete_one(self, filt):
        return MagicMock(deleted_count=1)

    async def count_documents(self, q=None):
        return 0

    def find(self, q=None, *a, **k):
        coll = self

        class _Cursor:
            def __init__(self):
                self._items = list(coll.docs)

            def sort(self, *a, **k):
                return self

            def limit(self, n):
                self._items = self._items[:n]
                return self

            def __aiter__(self):
                self._iter = iter(self._items)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        return _Cursor()


class FakeDB:
    def __init__(self):
        self.xcel_connections = FakeCollection()
        self.xcel_oauth_states = FakeCollection()
        self.xcel_audit_log = FakeCollection()
        self.xcel_notifications = FakeCollection()
        self.xcel_usage_daily = FakeCollection()
        self.xcel_usage_summaries = FakeCollection()
        self.xcel_saving_tips_cache = FakeCollection()
        self.properties = FakeCollection()
        self.tenants = FakeCollection()
        self.rental_contracts = FakeCollection()
        self.app_users = FakeCollection()


@pytest.fixture(autouse=True)
def patch_db():
    fake = FakeDB()
    rshared.set_db(fake)
    # also clear token cache between tests
    xer._CLIENT_TOKEN_CACHE["access_token"] = None
    xer._CLIENT_TOKEN_CACHE["expires_at"] = 0
    yield fake


def _async_response(status_code=200, text="", headers=None, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.headers = headers or {}
    if json_body is not None:
        r.json = MagicMock(return_value=json_body)
    return r


class _FakeAsyncClient:
    """Drop-in replacement for httpx.AsyncClient that records calls."""
    instances = []

    def __init__(self, *a, **kw):
        self.calls = []
        self.post_resp = _async_response(200, "", {})
        self.get_resp = _async_response(200, "", {})
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.post_resp

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.get_resp


# ───────────────────────────── 1. Import + routes ─────────────────────────────

def test_module_imports_cleanly():
    assert hasattr(xer, "router")
    assert hasattr(xer, "_get_client_credentials_token")
    assert hasattr(xer, "_ingest_espi_xml")
    assert hasattr(xer, "parse_espi_feed")


def test_router_exposes_19_routes_including_read_service_status():
    paths = {r.path for r in xer.router.routes}
    # Route count grows as new endpoints are added; assert a sensible floor
    # plus the presence of the diagnostic endpoint introduced in this refactor.
    assert len(xer.router.routes) >= 19, f"Expected ≥19 routes, got {len(xer.router.routes)}"
    assert "/admin/xcel/read-service-status" in paths
    # Ensure sync endpoint is POST
    sync_routes = [r for r in xer.router.routes if r.path == "/admin/xcel/connections/{conn_id}/sync"]
    assert sync_routes, "sync route missing"
    assert "POST" in sync_routes[0].methods


def test_admin_scope_default_is_fb_34_35():
    assert xer.XCEL_ADMIN_SCOPE == "FB=34_35"


# ───────────────────────────── 2. client_credentials token ─────────────────────────────

@pytest.mark.asyncio
async def test_client_credentials_token_uses_basic_auth_scope_and_caches():
    xer.XCEL_CLIENT_ID = "test-id"
    xer.XCEL_CLIENT_SECRET = "test-secret"

    _FakeAsyncClient.instances.clear()
    fake_resp = _async_response(
        200, "", {}, json_body={"access_token": "T-ABC", "expires_in": 3600}
    )

    with patch.object(xer.httpx, "AsyncClient", _FakeAsyncClient):
        # Pre-set the response on the next-created instance via class-level hook
        def _factory(*a, **kw):
            c = _FakeAsyncClient(*a, **kw)
            c.post_resp = fake_resp
            return c
        with patch.object(xer.httpx, "AsyncClient", _factory):
            t1 = await xer._get_client_credentials_token()
            t2 = await xer._get_client_credentials_token()  # should be cached

    assert t1 == "T-ABC"
    assert t2 == "T-ABC"
    # Only ONE client instance created → second call hit the cache
    assert len(_FakeAsyncClient.instances) == 1, "second call should use cache"

    call = _FakeAsyncClient.instances[0].calls[0]
    method, url, kw = call
    assert method == "POST"
    assert url == xer.XCEL_TOKEN_URL
    # BasicAuth attached
    assert isinstance(kw.get("auth"), xer.httpx.BasicAuth)
    # grant_type + scope correct
    data = kw.get("data") or {}
    assert data.get("grant_type") == "client_credentials"
    assert data.get("scope") == "FB=34_35"


# ───────────────────────────── 3. sync uses POST and accepts 202 ─────────────────────────────

@pytest.mark.asyncio
async def test_xcel_sync_connection_posts_and_accepts_202(patch_db):
    # Pre-seed a fake connection doc
    fake_conn = {
        "_id": "conn1",
        "property_id": "prop1",
        "subscription_id": "SUB-1",
        "access_token": "AT",
        "access_token_expires_at": datetime.now(timezone.utc).timestamp() + 9999,
        "refresh_token": "RT",
        "status": "active",
    }

    async def _find_one(filt, *a, **kw):
        return fake_conn

    patch_db.xcel_connections.find_one = _find_one

    # Bypass admin auth
    with patch.object(xer, "auth_admin", AsyncMock(return_value={"role": "admin"})):
        # Bypass ObjectId cast
        with patch.object(xer, "ObjectId", lambda x: x):
            _FakeAsyncClient.instances.clear()

            def _factory(*a, **kw):
                c = _FakeAsyncClient(*a, **kw)
                c.post_resp = _async_response(202, "", {"Location": "/Batch/file/123"})
                return c

            with patch.object(xer.httpx, "AsyncClient", _factory):
                fake_req = MagicMock()
                result = await xer.xcel_sync_connection(fake_req, "conn1")

    assert result["success"] is True
    assert result["status_code"] == 202
    assert result["mode"] == "asynchronous"
    assert result["location"] == "/Batch/file/123"

    method, url, _ = _FakeAsyncClient.instances[0].calls[0]
    assert method == "POST", "Batch must be POST per GBC spec"
    assert url.endswith("/Batch/Subscription/SUB-1")


# ───────────────────────────── 4. _ingest_espi_xml parses + persists ─────────────────────────────

SAMPLE_ESPI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:interval>
          <espi:duration>86400</espi:duration>
          <espi:start>1717200000</espi:start>
        </espi:interval>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:duration>86400</espi:duration>
            <espi:start>1717200000</espi:start>
          </espi:timePeriod>
          <espi:value>25000</espi:value>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:duration>86400</espi:duration>
            <espi:start>1717286400</espi:start>
          </espi:timePeriod>
          <espi:value>30000</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
  <entry>
    <content>
      <espi:UsageSummary>
        <espi:billingPeriod>
          <espi:duration>2592000</espi:duration>
          <espi:start>1714521600</espi:start>
        </espi:billingPeriod>
        <espi:overallConsumptionLastPeriod>
          <espi:value>800000</espi:value>
          <espi:powerOfTenMultiplier>0</espi:powerOfTenMultiplier>
        </espi:overallConsumptionLastPeriod>
        <espi:billLastPeriod>12345678</espi:billLastPeriod>
        <espi:currency>USD</espi:currency>
      </espi:UsageSummary>
    </content>
  </entry>
</feed>
"""


def test_parse_espi_feed_extracts_readings_and_summaries():
    parsed = xer.parse_espi_feed(SAMPLE_ESPI_XML)
    assert len(parsed["interval_readings"]) == 2
    # 25000 Wh -> 25 kWh
    assert parsed["interval_readings"][0]["value_kwh"] == 25.0
    assert parsed["interval_readings"][1]["value_kwh"] == 30.0
    assert len(parsed["usage_summaries"]) == 1
    s = parsed["usage_summaries"][0]
    assert s["total_kwh"] == 800.0
    assert s["currency"] == "USD"
    # 12345678 / 100000 = 123.45678 dollars
    assert round(s["cost"], 2) == 123.46


@pytest.mark.asyncio
async def test_ingest_espi_xml_persists_to_daily_and_summaries(patch_db):
    result = await xer._ingest_espi_xml("prop-XYZ", SAMPLE_ESPI_XML, source="unit_test")
    assert result["interval_readings"] == 2
    assert result["days_updated"] == 2
    assert result["summaries"] == 1
    # xcel_usage_daily got 2 upserts
    assert len(patch_db.xcel_usage_daily.updates) == 2
    for u in patch_db.xcel_usage_daily.updates:
        assert u["upsert"] is True
        assert u["filter"]["property_id"] == "prop-XYZ"
    # xcel_usage_summaries got 1 upsert
    assert len(patch_db.xcel_usage_summaries.updates) == 1
    # xcel_connections got last_sync update
    assert len(patch_db.xcel_connections.updates) == 1


# ───────────────────────────── 5. notify webhook parses <resources> ─────────────────────────────

NOTIFICATION_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<ns1:BatchList xmlns:ns1="http://naesb.org/espi">
  <ns1:resources>https://api.xcelenergy.com/.../Batch/Subscription/SUB-1?file_id=FILE-42</ns1:resources>
</ns1:BatchList>
"""


@pytest.mark.asyncio
async def test_xcel_notify_extracts_resources_and_ingests(patch_db):
    xer.XCEL_CLIENT_ID = "id"
    xer.XCEL_CLIENT_SECRET = "secret"

    # Seed a connection so property lookup succeeds
    async def _find_one(query, *a, **kw):
        if query.get("subscription_id") == "SUB-1":
            return {"_id": "c1", "property_id": "prop-NOTIF", "subscription_id": "SUB-1"}
        return None
    patch_db.xcel_connections.find_one = _find_one

    # Mock Request.body()
    fake_req = MagicMock()
    async def _body():
        return NOTIFICATION_BODY.encode("utf-8")
    fake_req.body = _body

    _FakeAsyncClient.instances.clear()
    call_index = {"i": 0}

    def _factory(*a, **kw):
        c = _FakeAsyncClient(*a, **kw)
        # First instance = token exchange; second = download
        if call_index["i"] == 0:
            c.post_resp = _async_response(
                200, "", {}, json_body={"access_token": "TKN", "expires_in": 3600}
            )
        c.get_resp = _async_response(200, SAMPLE_ESPI_XML, {})
        call_index["i"] += 1
        return c

    with patch.object(xer.httpx, "AsyncClient", _factory):
        result = await xer.xcel_notify(fake_req)

    assert result["status"] == "ok"
    assert result["resources_found"] == 1
    assert result["ingested"] == 1
    assert result["errors"] == 0

    # Notification persisted with resources_found populated
    assert len(patch_db.xcel_notifications.inserts) == 1
    persisted = patch_db.xcel_notifications.inserts[0]
    assert persisted["processed"] is True
    assert len(persisted["resources_found"]) == 1
    assert "file_id=FILE-42" in persisted["resources_found"][0]

    # The download instance was called with bearer header
    download_client = _FakeAsyncClient.instances[1]
    assert download_client.calls[0][0] == "GET"
    assert "Subscription/SUB-1" in download_client.calls[0][1]
    headers = download_client.calls[0][2].get("headers", {})
    assert headers.get("Authorization") == "Bearer TKN"

    # Daily + summaries persisted via _ingest_espi_xml
    assert len(patch_db.xcel_usage_daily.updates) == 2
    assert len(patch_db.xcel_usage_summaries.updates) == 1


@pytest.mark.asyncio
async def test_xcel_notify_no_resources_returns_zero_and_logs(patch_db):
    fake_req = MagicMock()
    async def _body():
        return b"<empty/>"
    fake_req.body = _body

    result = await xer.xcel_notify(fake_req)
    assert result["status"] == "ok"
    assert result["resources_found"] == 0
    assert len(patch_db.xcel_notifications.inserts) == 1
    assert patch_db.xcel_notifications.inserts[0]["processed"] is False
