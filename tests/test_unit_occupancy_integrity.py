import pytest
from bson import ObjectId
from fastapi import HTTPException

from rental import units_router


class _Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class _Collection:
    def __init__(self, doc=None, matched_count=1):
        self.doc = doc
        self.matched_count = matched_count
        self.last_filter = None
        self.last_update = None

    async def find_one(self, query):
        return self.doc

    async def update_one(self, query, update):
        self.last_filter = query
        self.last_update = update
        return _Result(self.matched_count)


class _DB:
    def __init__(self, unit, contract, tenant, matched_count=1):
        self.property_units = _Collection(unit, matched_count=matched_count)
        self.rental_contracts = _Collection(contract)
        self.tenants = _Collection(tenant)


def _ids():
    return tuple(str(ObjectId()) for _ in range(6))


def _docs(*, occupied_by=None, occupied_tenant=None, maintenance=False,
          mismatch_unit=False, mismatch_tenant=False, mismatch_property=False):
    unit_id, tenant_id, contract_id, property_id, other_contract, other_tenant = _ids()
    unit = {
        "_id": ObjectId(unit_id),
        "property_id": property_id,
        "status": "maintenance" if maintenance else ("rented" if occupied_by else "available"),
        "current_contract_id": occupied_by,
        "current_tenant_id": occupied_tenant,
    }
    contract = {
        "_id": ObjectId(contract_id),
        "unit_id": str(ObjectId()) if mismatch_unit else unit_id,
        "tenant_id": other_tenant if mismatch_tenant else tenant_id,
        "property_id": str(ObjectId()) if mismatch_property else property_id,
        "status": "pending_signature",
    }
    tenant = {"_id": ObjectId(tenant_id), "name": "Tenant"}
    return (unit_id, tenant_id, contract_id, property_id, other_contract, other_tenant,
            unit, contract, tenant)


async def _disable_sync(monkeypatch):
    async def _noop(_property_id):
        return None
    monkeypatch.setattr(units_router, "sync_property_from_units", _noop)


@pytest.mark.asyncio
async def test_claim_binds_contract_unit_tenant_and_property(monkeypatch):
    data = _docs()
    unit_id, tenant_id, contract_id = data[:3]
    unit, contract, tenant = data[6:]
    db = _DB(unit, contract, tenant)
    monkeypatch.setattr(units_router, "get_db", lambda: db)
    await _disable_sync(monkeypatch)

    await units_router.mark_unit_rented(unit_id, tenant_id, contract_id)

    assert db.property_units.last_update["$set"]["status"] == "rented"
    assert db.property_units.last_update["$set"]["current_contract_id"] == contract_id
    assert db.property_units.last_update["$set"]["current_tenant_id"] == tenant_id
    claim_filter = db.property_units.last_filter
    assert claim_filter["_id"] == ObjectId(unit_id)
    assert claim_filter["$and"][2] == {"status": {"$in": ["available", "rented"]}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags,detail",
    [
        ({"mismatch_unit": True}, "unit_contract_mismatch"),
        ({"mismatch_tenant": True}, "unit_tenant_mismatch"),
        ({"mismatch_property": True}, "unit_property_mismatch"),
    ],
)
async def test_claim_rejects_relationship_mismatch(monkeypatch, flags, detail):
    data = _docs(**flags)
    unit_id, tenant_id, contract_id = data[:3]
    unit, contract, tenant = data[6:]
    db = _DB(unit, contract, tenant)
    monkeypatch.setattr(units_router, "get_db", lambda: db)
    await _disable_sync(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await units_router.mark_unit_rented(unit_id, tenant_id, contract_id)
    assert exc.value.status_code == 409
    assert exc.value.detail == detail
    assert db.property_units.last_update is None


@pytest.mark.asyncio
async def test_claim_rejects_unit_owned_by_other_contract(monkeypatch):
    seed = _docs()
    other_contract = seed[4]
    other_tenant = seed[5]
    data = _docs(occupied_by=other_contract, occupied_tenant=other_tenant)
    unit_id, tenant_id, contract_id = data[:3]
    unit, contract, tenant = data[6:]
    db = _DB(unit, contract, tenant)
    monkeypatch.setattr(units_router, "get_db", lambda: db)
    await _disable_sync(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await units_router.mark_unit_rented(unit_id, tenant_id, contract_id)
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_already_claimed"
    assert db.property_units.last_update is None


@pytest.mark.asyncio
async def test_claim_rejects_maintenance_unit(monkeypatch):
    data = _docs(maintenance=True)
    unit_id, tenant_id, contract_id = data[:3]
    unit, contract, tenant = data[6:]
    db = _DB(unit, contract, tenant)
    monkeypatch.setattr(units_router, "get_db", lambda: db)
    await _disable_sync(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await units_router.mark_unit_rented(unit_id, tenant_id, contract_id)
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_in_maintenance"


@pytest.mark.asyncio
async def test_claim_fails_closed_when_cas_loses_race(monkeypatch):
    data = _docs()
    unit_id, tenant_id, contract_id = data[:3]
    unit, contract, tenant = data[6:]
    db = _DB(unit, contract, tenant, matched_count=0)
    monkeypatch.setattr(units_router, "get_db", lambda: db)
    await _disable_sync(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await units_router.mark_unit_rented(unit_id, tenant_id, contract_id)
    assert exc.value.status_code == 409
    assert exc.value.detail == "unit_occupancy_changed"


@pytest.mark.asyncio
async def test_claim_rejects_invalid_ids_before_db_access(monkeypatch):
    monkeypatch.setattr(
        units_router,
        "get_db",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched")),
    )
    with pytest.raises(HTTPException) as exc:
        await units_router.mark_unit_rented("bad", "bad", "bad")
    assert exc.value.status_code == 400
    assert exc.value.detail == "unit_occupancy_invalid_id"


def test_admin_unit_status_cannot_bypass_contract_lifecycle():
    from pathlib import Path
    source = Path("rental/units_router.py").read_text()
    assert 'detail="unit_rented_requires_active_contract"' in source
    assert 'detail="unit_status_requires_contract_release"' in source
    assert 'current_contract_id' in source
