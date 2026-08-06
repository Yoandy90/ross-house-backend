"""Tests del modelo multi-unidad: CRUD de unidades, bulk, sync de propiedad y contratos."""
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

from bson import ObjectId  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rental.shared import set_db, create_marketplace_token  # noqa: E402
from rental.units_router import router as units_router  # noqa: E402
from rental.contracts_router import router as contracts_router  # noqa: E402

TEST_TAG = "__units_pytest__"


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
    app.include_router(units_router, prefix="/api")
    app.include_router(contracts_router, prefix="/api")

    async def _setup():
        admin = await db.app_users.find_one({"role": "admin"})
        token = create_marketplace_token(str(admin["_id"]), admin["email"], "admin")
        prop = await db.properties.insert_one({
            "property_number": "PROP-TEST-UNITS", "name": f"Edificio {TEST_TAG}",
            "address": "500 Test Plaza", "city": "Dumas", "state": "TX",
            "bedrooms": 2, "bathrooms": 1.0, "rent_amount": 800.0,
            "deposit_amount": 800.0, "status": "available",
        })
        tenant = await db.tenants.insert_one({
            "name": f"Tenant {TEST_TAG}", "email": "units-pytest@example.com",
            "phone": "8060000000",
        })
        return token, str(prop.inserted_id), str(tenant.inserted_id)

    token, prop_id, tenant_id = loop.run_until_complete(_setup())
    yield {"app": app, "db": db, "token": token, "loop": loop,
           "prop_id": prop_id, "tenant_id": tenant_id}

    async def _teardown():
        await db.property_units.delete_many({"property_id": prop_id})
        await db.properties.delete_many({"name": {"$regex": TEST_TAG}})
        await db.tenants.delete_many({"name": {"$regex": TEST_TAG}})
        await db.rental_contracts.delete_many({"tenant_name": {"$regex": TEST_TAG}})

    loop.run_until_complete(_teardown())
    client.close()


def _req(ctx, method, path, json=None, auth=True):
    async def _do():
        transport = ASGITransport(app=ctx["app"])
        headers = {"Authorization": f"Bearer {ctx['token']}"} if auth else {}
        async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            return await c.request(method, path, json=json, headers=headers)
    return ctx["loop"].run_until_complete(_do())


def test_01_create_single_unit(ctx):
    r = _req(ctx, "POST", f"/api/admin/properties/{ctx['prop_id']}/units",
             json={"unit_name": "Apt 1", "rent_amount": 750})
    assert r.status_code == 200, r.text
    # duplicado rechazado
    r2 = _req(ctx, "POST", f"/api/admin/properties/{ctx['prop_id']}/units",
              json={"unit_name": "Apt 1"})
    assert r2.status_code == 400


def test_02_bulk_create(ctx):
    r = _req(ctx, "POST", f"/api/admin/properties/{ctx['prop_id']}/units",
             json={"bulk_count": 5, "prefix": "Apt", "start_number": 2, "rent_amount": 800})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 5

    r = _req(ctx, "GET", f"/api/admin/properties/{ctx['prop_id']}/units")
    d = r.json()
    assert d["summary"]["total"] == 6
    assert d["summary"]["available"] == 6
    assert d["summary"]["monthly_income_potential"] == 750 + 5 * 800


def test_03_property_marked_multi_unit(ctx):
    async def _get():
        return await ctx["db"].properties.find_one({"_id": ObjectId(ctx["prop_id"])})
    prop = ctx["loop"].run_until_complete(_get())
    assert prop["is_multi_unit"] is True
    assert prop["units_count"] == 6
    assert prop["status"] == "available"


def test_04_contract_with_unit_marks_rented(ctx):
    async def _unit():
        return await ctx["db"].property_units.find_one(
            {"property_id": ctx["prop_id"], "unit_name": "Apt 2"})
    unit = ctx["loop"].run_until_complete(_unit())
    r = _req(ctx, "POST", "/api/admin/rental-contracts", json={
        "property_id": ctx["prop_id"], "tenant_id": ctx["tenant_id"],
        "unit_id": str(unit["_id"]),
        "tenant_name": f"Tenant {TEST_TAG}",
        "start_date": "2026-08-01", "end_date": "2027-07-31",
        "rent_amount": 800, "status": "active",
    })
    assert r.status_code == 200, r.text
    ctx["contract_id"] = r.json()["contract_id"]

    unit = ctx["loop"].run_until_complete(_unit())
    assert unit["status"] == "rented"
    assert unit["current_tenant_id"] == ctx["tenant_id"]

    async def _prop():
        return await ctx["db"].properties.find_one({"_id": ObjectId(ctx["prop_id"])})
    prop = ctx["loop"].run_until_complete(_prop())
    assert prop["units_rented"] == 1
    assert prop["status"] == "available"  # aún hay 5 libres


def test_05_unit_already_rented_rejected(ctx):
    async def _unit():
        return await ctx["db"].property_units.find_one(
            {"property_id": ctx["prop_id"], "unit_name": "Apt 2"})
    unit = ctx["loop"].run_until_complete(_unit())
    r = _req(ctx, "POST", "/api/admin/rental-contracts", json={
        "property_id": ctx["prop_id"], "tenant_id": ctx["tenant_id"],
        "unit_id": str(unit["_id"]), "tenant_name": f"Tenant {TEST_TAG}",
        "start_date": "2026-08-01", "end_date": "2027-07-31",
        "rent_amount": 800, "status": "active",
    })
    assert r.status_code == 400


def test_06_delete_rented_unit_blocked(ctx):
    async def _unit():
        return await ctx["db"].property_units.find_one(
            {"property_id": ctx["prop_id"], "unit_name": "Apt 2"})
    unit = ctx["loop"].run_until_complete(_unit())
    r = _req(ctx, "DELETE", f"/api/admin/units/{unit['_id']}")
    assert r.status_code == 400


def test_07_terminate_contract_frees_unit(ctx):
    r = _req(ctx, "PATCH", f"/api/admin/rental-contracts/{ctx['contract_id']}/status",
             json={"status": "terminated"})
    assert r.status_code == 200, r.text

    async def _unit():
        return await ctx["db"].property_units.find_one(
            {"property_id": ctx["prop_id"], "unit_name": "Apt 2"})
    unit = ctx["loop"].run_until_complete(_unit())
    assert unit["status"] == "available"
    assert unit["current_tenant_id"] is None


def test_08_update_and_delete_unit(ctx):
    async def _unit():
        return await ctx["db"].property_units.find_one(
            {"property_id": ctx["prop_id"], "unit_name": "Apt 6"})
    unit = ctx["loop"].run_until_complete(_unit())
    r = _req(ctx, "PUT", f"/api/admin/units/{unit['_id']}",
             json={"rent_amount": 900, "status": "maintenance"})
    assert r.status_code == 200
    r = _req(ctx, "PUT", f"/api/admin/units/{unit['_id']}", json={"status": "bad"})
    assert r.status_code == 400
    r = _req(ctx, "DELETE", f"/api/admin/units/{unit['_id']}")
    assert r.status_code == 200

    r = _req(ctx, "GET", f"/api/admin/properties/{ctx['prop_id']}/units")
    assert r.json()["summary"]["total"] == 5


def test_09_unauthorized(ctx):
    assert _req(ctx, "GET", f"/api/admin/properties/{ctx['prop_id']}/units",
                auth=False).status_code == 401
