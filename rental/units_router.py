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
    """Marca una unidad como rentada (usado al activar un contrato con unit_id)."""
    db = get_db()
    unit = await db.property_units.find_one({"_id": ObjectId(unit_id)})
    if not unit:
        return
    await db.property_units.update_one(
        {"_id": unit["_id"]},
        {"$set": {"status": "rented", "current_tenant_id": tenant_id,
                  "current_contract_id": contract_id, "updated_at": datetime.utcnow()}})
    await sync_property_from_units(unit["property_id"])


async def free_unit(unit_id: str):
    """Libera una unidad (contrato terminado/expirado/revertido a borrador)."""
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
    # nombre del inquilino por unidad ocupada
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
    """Edita una unidad. Body: campos de unidad y/o status (available|rented|maintenance)."""
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
        if data["status"] not in UNIT_STATUSES:
            raise HTTPException(status_code=400,
                                detail=f"status debe ser: {', '.join(UNIT_STATUSES)}")
        sets["status"] = data["status"]
        if data["status"] != "rented":
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
