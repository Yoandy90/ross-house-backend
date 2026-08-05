"""
Tests for the Moore County property-tax sync module.

- Unit-tests the HTML parser with a real captured table.
- Integration-tests GET /api/admin/property-taxes and POST /sync against the
  LIVE county portal (esearch.co.moore.tx.us) using the production DB.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental.shared import set_db, create_marketplace_token  # noqa: E402
from rental.property_taxes_router import (  # noqa: E402
    parse_tax_due_html, router as taxes_router,
)

SAMPLE_HTML = """
<table class="table">
    <tr><th>Year</th></tr>
    <tr>
        <td><span class="modal-pay-btn" data-property-id="13572" data-property-year="2025">Pay</span></td>
        <td>2025</td>
        <td class="table-number PreviousYearsModalView_TaxableValue">$134740.00</td>
        <td class="table-number PreviousYearsModalView_BaseTax">$3,316.80</td>
        <td class="table-number PreviousYearsModalView_BaseTaxPaid">$0.00</td>
        <td class="table-number PreviousYearsModalView_BaseTaxDue">$3,316.80</td>
        <td class="table-number PreviousYearsModalView_DiscountPenaltyAndInterest">$630.18</td>
        <td class="table-number PreviousYearsModalView_AttorneyFees">$789.40</td>
        <td class="table-number PreviousYearsModalView_AmountDue">$4,736.38</td>
    </tr>
</table>
"""


def test_parser_extracts_year_row():
    rows = parse_tax_due_html(SAMPLE_HTML)
    assert len(rows) == 1
    r = rows[0]
    assert r["year"] == 2025
    assert r["taxable_value"] == 134740.00
    assert r["base_tax"] == 3316.80
    assert r["base_paid"] == 0.0
    assert r["base_due"] == 3316.80
    assert r["penalty_interest"] == 630.18
    assert r["attorney_fees"] == 789.40
    assert r["amount_due"] == 4736.38


def test_parser_empty_table_means_current():
    assert parse_tax_due_html("<table><tr><th>Year</th></tr></table>") == []


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def ctx(loop):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], io_loop=loop)
    db = client[os.environ.get("DB_NAME", "taxportal")]
    set_db(db)
    app = FastAPI()
    app.include_router(taxes_router, prefix="/api")

    async def _tok():
        admin = await db.app_users.find_one({"role": "admin"})
        return create_marketplace_token(str(admin["_id"]), admin["email"], "admin")

    token = loop.run_until_complete(_tok())
    yield {"app": app, "db": db, "token": token, "loop": loop}
    client.close()


def _call(ctx, method, path, json=None):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
            return await c.request(method, path, json=json,
                                   headers={"Authorization": f"Bearer {ctx['token']}"})
    return ctx["loop"].run_until_complete(_do())


def test_live_sync_and_list(ctx):
    """Sync against the real county portal, then list."""
    r = _call(ctx, "POST", "/api/admin/property-taxes/sync")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["success"] is True
    assert d["synced_count"] >= 2, d  # 13572 + 12973
    assert d["error_count"] == 0, d

    r = _call(ctx, "GET", "/api/admin/property-taxes")
    assert r.status_code == 200, r.text
    d = r.json()
    by_acct = {p["account_id"]: p for p in d["properties"] if p["account_id"]}
    # 121 Oak (13572) is delinquent for 2025 as of Aug 2026
    oak = by_acct["13572"]["tax_status"]
    assert oak["status"] == "delinquent"
    assert oak["total_due"] > 4000
    assert oak["years_due"][0]["year"] == 2025
    assert oak["portal_url"].endswith("/Property/View/13572")
    # 812 NE 2nd (12973) is current
    ne2 = by_acct["12973"]["tax_status"]
    assert ne2["status"] == "current"
    assert ne2["total_due"] == 0
    assert d["total_due"] == oak["total_due"]


def test_unauthorized(ctx):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.get("/api/admin/property-taxes")
    r = ctx["loop"].run_until_complete(_do())
    assert r.status_code == 401
