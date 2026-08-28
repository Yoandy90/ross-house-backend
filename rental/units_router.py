"""Unidades por propiedad (multi-unit) — apartamentos, dúplex, complejos.

Colección: property_units
{ property_id, unit_name ("Apt 1"), bedrooms, bathrooms, square_feet,
  rent_amount, deposit_amount, status (available|rented|maintenance),
  current_tenant_id, current_contract_id, notes, created_at, updated_at }

- Una propiedad SIN unidades sigue funcionando como casa individual (sin cambios).
- Al crear unidades, la propiedad se marca is_multi_unit=True y su status se
  deriva de las unidades (rented si todas ocupadas, available si alguna libre),
  respetando status_manually_set.
- Los contratos pueden llevar unit_id opcional (ver contracts_router).
"""
import logging
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin, serialize

router = APIRouter()
logger = logging.getLogger(__name__)

UNIT_STATUSES = ("available", "rented", "maintenance")


async def sync_property_from_units(property_id: str):
    """Recalcula resumen y status de la propiedad a partir de sus unidades."""
    db = get_db()
    if not ObjectId.is_valid(property_id):
        logger.warning("Skipping unit/property sync for invalid property_id=%r", property_id)
        return
    units = await db.property_units.find({"property_id": property_id}).to_list(500)
    now = datetime.utcnow()
    if not units:
        await db.properties.update_one(
            {"_id": ObjectId(property_id)},
            {"$set": {"is_multi_unit": False, "units_count": 0,
                      "units_available": 0, "units_rented": 0, "updated_at": now}})
        return
    rented = sum(1 for u in units if u.get("status") == "rented")
    available = sum(1 for u in units if u.get("status") == "available")
    sets = {
        "is_multi_unit": True,
        "units_count": len(units),
        "units_available": available,
        "units_rented": rented,
        "updated_at": now,
    }
    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if prop and not prop.get("status_manually_set"):
        sets["status"] = "rented" if available == 0 and rented > 0 else "available"
    await db.properties.update_one({"_id": ObjectId(property_id)}, {"$set": sets})


async def mark_unit_rented(unit_id: str, tenant_id: str, contract_id: str):
    """Claim a unit for one exact contract, fail-closed on conflicts.

    Contract creation may insert an already-active contract, while the status
    transition endpoint claims the unit immediately before persisting the new
    active status.  Therefore this helper validates relationship identity and
    occupancy atomically, but deliberately does not treat the contract's
    current status field as authority.
    """
    if not all(ObjectId.is_valid(value) for value in (unit_id, tenant_id, contract_id)):
        raise HTTPException(status_code=400, detail="unit_occupancy_invalid_id")

    db = get_db()
    unit_oid = ObjectId(unit_id)
    contract_oid = ObjectId(contract_id)
    tenant_oid = ObjectId(tenant_id)

    unit = await db.property_units.find_one({"_id": unit_oid})
    if not unit:
        raise HTTPException(status_code=404, detail="unit_not_found")

    contract = await db.rental_contracts.find_one({"_id": contract_oid})
    if not contract:
        raise HTTPException(status_code=404, detail="unit_contract_not_found")

    if str(contract.get("unit_id") or "") != unit_id:
        raise HTTPException(status_code=409, detail="unit_contract_mismatch")
    if str(contract.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=409, detail="unit_tenant_mismatch")
    if str(contract.get("property_id") or "") != str(unit.get("property_id") or ""):
        raise HTTPException(status_code=409, detail="unit_property_mismatch")

    tenant = await db.tenants.find_one({"_id": tenant_oid})
    if not tenant:
        raise HTTPException(status_code=404, detail="unit_tenant_not_found")

    current_contract = str(unit.get("current_contract_id") or "")
    current_tenant = str(unit.get("current_tenant_id") or "")
    if current_contract and current_contract != contract_id:
        raise HTTPException(status_code=409, detail="unit_already_claimed")
    if current_tenant and current_tenant != tenant_id:
        raise HTTPException(status_code=409, detail="unit_tenant_already_claimed")
    if unit.get("status") == "maintenance" and current_contract != contract_id:
        raise HTTPException(status_code=409, detail="unit_in_maintenance")

    claim_filter = {
        "_id": unit_oid,
        "$and": [
            {"$or": [
                {"current_contract_id": {"$exists": False}},
                {"current_contract_id": None},
                {"current_contract_id": ""},
                {"current_contract_id": contract_id},
            ]},
            {"$or": [
                {"current_tenant_id": {"$exists": False}},
                {"current_tenant_id": None},
                {"current_tenant_id": ""},
                {"current_tenant_id": tenant_id},
            ]},
            {"status": {"$in": ["available", "rented"]}},
        ],
    }
    result = await db.property_units.update_one(
        claim_filter,
        {"$set": {"status": "rented", "current_tenant_id": tenant_id,
                  "current_contract_id": contract_id, "updated_at": datetime.utcnow()}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="unit_occupancy_changed")

    await sync_property_from_units(str(unit["property_id"]))


async def free_unit(unit_id: str):
    """Libera una unidad (contrato terminado/expirado/revertido a borrador)."""
    if not ObjectId.is_valid(unit_id):
        raise HTTPException(status_code=400, detail="unit_occupancy_invalid_id")
    db = get_db()
    unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
    if not unit:
        return
    await db.property_units.update_one(
        {"_id": unit["_id"]},
        {"$set": {"status": "available", "current_tenant_id": None,
                  "current_contract_id": None, "updated_at": datetime.utcnow()}})
    await sync_property_from_units(unit["property_id"])


def _unit_fields(data: dict, prop: dict) -> dict:
    return {
        "unit_name": str(data.get("unit_name", "")).strip(),
        "bedrooms": int(data.get("bedrooms") or prop.get("bedrooms") or 0),
        "bathrooms": float(data.get("bathrooms") or prop.get("bathrooms") or 0),
        "square_feet": int(data.get("square_feet") or 0),
        "rent_amount": float(data.get("rent_amount") or prop.get("rent_amount") or 0),
        "deposit_amount": float(data.get("deposit_amount") or prop.get("deposit_amount") or 0),
        "notes": str(data.get("notes", "")),
    }


@router.get('/admin/properties/{property_id}/units')
async def list_units(property_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    units = await (db.property_units.find({"property_id": property_id})
                   .sort("unit_name", 1).to_list(500))
    tenant_ids = [u["current_tenant_id"] for u in units if u.get("current_tenant_id")]
    names = {}
    if tenant_ids:
        oids = [ObjectId(t) for t in tenant_ids if ObjectId.is_valid(t)]
        async for t in db.tenants.find({"_id": {"$in": oids}}, {"name": 1}):
            names[str(t["_id"])] = t.get("name", "")
    out = []
    for u in units:
        d = serialize(u)
        d["tenant_name"] = names.get(u.get("current_tenant_id") or "", "")
        out.append(d)
    rented = sum(1 for u in units if u.get("status") == "rented")
    return {"success": True, "units": out,
            "summary": {"total": len(units), "rented": rented,
                        "available": sum(1 for u in units if u.get("status") == "available"),
                        "maintenance": sum(1 for u in units if u.get("status") == "maintenance"),
                        "monthly_income_potential": sum(float(u.get("rent_amount") or 0) for u in units),
                        "monthly_income_current": sum(float(u.get("rent_amount") or 0)
                                                      for u in units if u.get("status") == "rented")}}


@router.post('/admin/properties/{property_id}/units')
async def create_units(property_id: str, request: Request):
    """Crea unidades. Body:
    - Una: {unit_name, bedrooms?, bathrooms?, square_feet?, rent_amount?, deposit_amount?, notes?}
    - En masa: {bulk_count: N, prefix?: "Apt", start_number?: 1, bedrooms?, ...}  (máx 200)
    """
    await auth_admin(request)
    db = get_db()
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    data = await request.json()
    now = datetime.utcnow()

    docs = []
    bulk_count = int(data.get("bulk_count") or 0)
    if bulk_count:
        if bulk_count > 200:
            raise HTTPException(status_code=400, detail="Máximo 200 unidades por lote")
        prefix = str(data.get("prefix") or "Apt").strip()
        start = int(data.get("start_number") or 1)
        existing = {u["unit_name"] async for u in
                    db.property_units.find({"property_id": property_id}, {"unit_name": 1})}
        for i in range(start, start + bulk_count):
            name = f"{prefix} {i}"
            if name in existing:
                continue
            base = _unit_fields({**data, "unit_name": name}, prop)
            docs.append(base)
    else:
        base = _unit_fields(data, prop)
        if not base["unit_name"]:
            raise HTTPException(status_code=400, detail="unit_name es requerido")
        dup = await db.property_units.find_one(
            {"property_id": property_id, "unit_name": base["unit_name"]})
        if dup:
            raise HTTPException(status_code=400,
                                detail=f"Ya existe la unidad '{base['unit_name']}'")
        docs.append(base)

    if not docs:
        raise HTTPException(status_code=400, detail="No hay unidades nuevas que crear")
    for d in docs:
        d.update({"property_id": property_id, "status": "available",
                  "current_tenant_id": None, "current_contract_id": None,
                  "created_at": now, "updated_at": now})
    await db.property_units.insert_many(docs)
    await sync_property_from_units(property_id)
    return {"success": True, "created": len(docs),
            "message": f"{len(docs)} unidad(es) creada(s)"}


@router.put('/admin/units/{unit_id}')
async def update_unit(unit_id: str, request: Request):
    """Edita una unidad sin permitir bypass manual del ciclo contractual."""
    await auth_admin(request)
    db = get_db()
    if not ObjectId.is_valid(unit_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
    if not unit:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")
    data = await request.json()
    sets = {}
    for f in ("unit_name", "notes"):
        if f in data:
            sets[f] = str(data[f]).strip()
    for f in ("bedrooms", "square_feet"):
        if f in data:
            sets[f] = int(data[f] or 0)
    for f in ("bathrooms", "rent_amount", "deposit_amount"):
        if f in data:
            sets[f] = float(data[f] or 0)
    if "status" in data:
        requested_status = data["status"]
        if requested_status not in UNIT_STATUSES:
            raise HTTPException(status_code=400,
                                detail=f"status debe ser: {', '.join(UNIT_STATUSES)}")
        if requested_status == "rented" and unit.get("status") != "rented":
            raise HTTPException(status_code=409, detail="unit_rented_requires_active_contract")
        if unit.get("current_contract_id") and requested_status != "rented":
            raise HTTPException(status_code=409, detail="unit_status_requires_contract_release")
        sets["status"] = requested_status
        if requested_status != "rented":
            sets["current_tenant_id"] = None
            sets["current_contract_id"] = None
    if sets.get("unit_name"):
        dup = await db.property_units.find_one({
            "property_id": unit["property_id"], "unit_name": sets["unit_name"],
            "_id": {"$ne": unit["_id"]}})
        if dup:
            raise HTTPException(status_code=400, detail="Nombre de unidad duplicado")
    if not sets:
        raise HTTPException(status_code=400, detail="Sin cambios")
    sets["updated_at"] = datetime.utcnow()
    await db.property_units.update_one({"_id": unit["_id"]}, {"$set": sets})
    await sync_property_from_units(unit["property_id"])
    return {"success": True}


@router.delete('/admin/units/{unit_id}')
async def delete_unit(unit_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    if not ObjectId.is_valid(unit_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
    if not unit:
        raise HTTPException(status_code=404, detail="Unidad no encontrada")
    if unit.get("status") == "rented" or unit.get("current_contract_id"):
        raise HTTPException(status_code=400,
                            detail="No se puede eliminar una unidad rentada — termina el contrato primero")
    await db.property_units.delete_one({"_id": unit["_id"]})
    await sync_property_from_units(unit["property_id"])
    return {"success": True, "message": "Unidad eliminada"}
