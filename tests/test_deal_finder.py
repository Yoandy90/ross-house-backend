"""Tests Deal Finder (Radar de Oportunidades): parsers + scraping en vivo del portal
del condado de Moore + endpoints CRUD/scan (ASGI in-process)."""
import asyncio
import os
import sys
import time

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

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental.shared import set_db, create_marketplace_token  # noqa: E402
from rental.deal_finder_router import (  # noqa: E402
    COUNTIES, UA, router as df_router,
    _open_search_session, _search_page,
    parse_property_detail, compute_signals, _situs_city,
)


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
    app.include_router(df_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        return create_marketplace_token(str(admin["_id"]), admin["email"], "admin")

    token = loop.run_until_complete(_setup())
    state = {"app": app, "db": db, "token": token, "loop": loop}
    yield state

    async def _teardown():
        # solo limpiar lo creado por este test (calle maddox)
        await db.deal_finder_leads.delete_many(
            {"county": "moore", "address": {"$regex": "MADDOX", "$options": "i"}})
        await db.deal_finder_scans.delete_many({"keywords": {"$regex": "maddox", "$options": "i"}})

    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=180) as c:
            return await c.request(method, path, json=json, headers=headers)
    return ctx["loop"].run_until_complete(_do())


# ─── Unit: parsers ────────────────────────────────────────────

SAMPLE_DETAIL = """
<tr><th>Mailing Address:</th><td colspan="3">% PAK A SAK, INC<br />PO BOX 7827<br />
AMARILLO         , TX 79114-7827 </td></tr>
<table><tr><th>Market Value:</th><td class="table-number">$261,710</td></tr>
<tr><th>Improvement Homesite Value:</th><td class="table-number">$10,000</td></tr>
<tr><th>Land Homesite Value:</th><td class="table-number">$50,000</td></tr></table>
"""


def test_01_parse_detail():
    d = parse_property_detail(SAMPLE_DETAIL)
    assert d["mailing_city"] == "AMARILLO"
    assert d["mailing_state"] == "TX"
    assert d["mailing_zip"] == "79114-7827"
    assert "PO BOX 7827" in d["mailing_lines"]
    assert d["values"]["Market Value"] == 261710
    assert d["values"]["Land Homesite Value"] == 50000


def test_02_situs_city():
    assert _situs_city("620 S MADDOX, DUMAS TX 79029") == "DUMAS"
    assert _situs_city("") == ""


def test_03_signals():
    lead = {
        "tax_due_total": 1500,
        "mailing_state": "CA", "mailing_city": "LOS ANGELES",
        "address": "100 MAIN, DUMAS TX 79029",
        "property_type": "R",
        "values": {"Market Value": 40000, "Improvement Homesite Value": 5000,
                   "Land Homesite Value": 35000},
        "appraised_value": 40000,
    }
    s = compute_signals(lead)
    assert "tax_delinquent" in s
    assert "out_of_state_owner" in s and "absentee_owner" in s
    assert "low_improvement" in s
    assert "low_value" in s

    vacant = {"property_type": "R", "values": {"Land Homesite Value": 9000}, "address": ""}
    assert "vacant_land" in compute_signals(vacant)


# ─── Live: scraping del portal del condado ────────────────────

def test_04_live_county_search(loop):
    async def _run():
        base = COUNTIES["moore"]["base"]
        async with httpx.AsyncClient(timeout=40, headers=UA, follow_redirects=True) as client:
            token = await _open_search_session(client, base, "StreetName:maddox")
            data = await _search_page(client, base, "StreetName:maddox", token, 1, 10)
            return data
    data = loop.run_until_complete(_run())
    assert (data.get("totalResults") or 0) > 0, data
    first = data["resultsList"][0]
    assert first.get("propertyId")
    assert first.get("ownerName")


# ─── API E2E ──────────────────────────────────────────────────

def test_05_counties_endpoint(ctx):
    r = _req(ctx, "GET", "/api/admin/deal-finder/counties")
    assert r.status_code == 200
    keys = {c["key"] for c in r.json()["counties"]}
    assert "moore" in keys


def test_06_scan_validation(ctx):
    r = _req(ctx, "POST", "/api/admin/deal-finder/scan",
             json={"county": "potter", "search_type": "street", "query": "x"})
    assert r.status_code == 400
    r = _req(ctx, "POST", "/api/admin/deal-finder/scan",
             json={"county": "moore", "search_type": "bad", "query": "x"})
    assert r.status_code == 400


def test_07_scan_e2e(ctx):
    """Escaneo real pequeño (5 propiedades) contra el portal del condado."""
    r = _req(ctx, "POST", "/api/admin/deal-finder/scan",
             json={"county": "moore", "search_type": "street",
                   "query": "maddox", "max_results": 5})
    assert r.status_code == 200, r.text
    scan_id = r.json()["scan_id"]

    # el scan corre como asyncio task en el mismo loop — darle tiempo
    async def _wait():
        doc = None
        for _ in range(60):
            await asyncio.sleep(3)
            doc = await ctx["db"].deal_finder_scans.find_one({"keywords": "StreetName:maddox"})
            if doc and doc["status"] in ("done", "error"):
                return doc
        return doc
    doc = ctx["loop"].run_until_complete(_wait())
    assert doc is not None
    assert doc["status"] == "done", doc.get("error")
    assert doc["processed"] >= 1

    r = _req(ctx, "GET", f"/api/admin/deal-finder/scan/{scan_id}")
    assert r.status_code == 200
    assert r.json()["scan"]["status"] == "done"


def test_08_leads_and_pipeline(ctx):
    r = _req(ctx, "GET", "/api/admin/deal-finder/leads?county=moore")
    assert r.status_code == 200
    leads = r.json()["leads"]
    assert len(leads) >= 1
    lead = leads[0]
    assert lead["property_id"] and lead["owner_name"]

    # actualizar estado
    r = _req(ctx, "PATCH", f"/api/admin/deal-finder/leads/{lead['id']}",
             json={"status": "contacted", "notes": "prueba pytest"})
    assert r.status_code == 200
    assert r.json()["lead"]["status"] == "contacted"

    r = _req(ctx, "PATCH", f"/api/admin/deal-finder/leads/{lead['id']}",
             json={"status": "estado_invalido"})
    assert r.status_code == 400

    # stats
    r = _req(ctx, "GET", "/api/admin/deal-finder/stats")
    assert r.status_code == 200
    assert r.json()["stats"]["total"] >= 1


def test_09_auth_required(ctx):
    r = _req(ctx, "GET", "/api/admin/deal-finder/leads", auth=False)
    assert r.status_code == 401


# ─── Cron: radar automático ───────────────────────────────────

def test_10_cron_config_endpoints(ctx):
    r = _req(ctx, "GET", "/api/admin/deal-finder/cron-config")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "enabled" in d["config"] and "next_letter" in d["state"]

    r = _req(ctx, "PATCH", "/api/admin/deal-finder/cron-config",
             json={"enabled": False, "max_per_run": 5})  # 5 se clampa a 20
    assert r.status_code == 200
    r = _req(ctx, "GET", "/api/admin/deal-finder/cron-config")
    assert r.json()["config"]["enabled"] is False
    assert r.json()["config"]["max_per_run"] == 20

    r = _req(ctx, "PATCH", "/api/admin/deal-finder/cron-config",
             json={"enabled": True, "max_per_run": 200})
    assert r.status_code == 200

    r = _req(ctx, "PATCH", "/api/admin/deal-finder/cron-config", json={})
    assert r.status_code == 400


def test_11_cron_batch_live(ctx):
    """Lote pequeño real del recorrido a-z (4 propiedades) con email mockeado."""
    import rental.deal_finder_cron as dfc

    async def _run():
        db = ctx["db"]
        # snapshot del estado para restaurar
        prev_state = await db.app_settings.find_one({"_id": "deal_finder_cron_state"})

        sent = {"called": False}
        orig = dfc.send_alert_email

        async def fake_send(db_, new_opps, became):
            sent["called"] = True
            return True
        dfc.send_alert_email = fake_send
        try:
            res = await dfc.run_auto_scan_batch(db, max_props=4)
        finally:
            dfc.send_alert_email = orig
            # restaurar estado previo (no interferir con el cron real)
            if prev_state:
                await db.app_settings.replace_one(
                    {"_id": "deal_finder_cron_state"}, prev_state, upsert=True)
        return res, sent

    res, sent = ctx["loop"].run_until_complete(_run())
    assert res["processed"] == 4, res
    assert res["new"] + res["updated"] == 4
    # el email solo se manda si hubo hallazgos fuertes — coherencia:
    if res["strong_new"] or res["became_delinquent"]:
        assert sent["called"]
