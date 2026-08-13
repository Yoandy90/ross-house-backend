"""Registro DNC — Programa de asistencia gratuita para inscribir a
inquilinos/clientes en el National Do Not Call Registry (donotcall.gov).

Servicio 100% GRATIS (la FTC prohíbe cobrar por esto). Se usa como
herramienta de fidelización y generación de confianza/leads.
"""
import os
import re
from datetime import datetime, timezone

import httpx
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from rental.shared import get_db, auth_admin, serialize

router = APIRouter(tags=["dnc-registry"])

STATUSES = ["pendiente_email", "inscrito", "verificado", "rechazado"]


class DncRegistrationBody(BaseModel):
    name: str
    email: str = ""
    phones: list[str] = []
    source: str = "inquilino"      # inquilino | cliente | lead | otro
    notes: str = ""


@router.get("/admin/dnc-registrations")
async def list_dnc_registrations(request: Request, q: str = ""):
    await auth_admin(request)
    db = get_db()
    filt: dict = {}
    if q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        filt = {"$or": [{"name": rx}, {"email": rx}, {"phones": rx}]}
    items = await db.dnc_registrations.find(filt).sort("created_at", -1).to_list(500)
    stats = {"total": 0, "verificado": 0, "inscrito": 0, "pendiente_email": 0}
    async for row in db.dnc_registrations.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        stats[row["_id"] or "inscrito"] = row["n"]
        stats["total"] += row["n"]
    return {"success": True, "items": [serialize(x) for x in items], "stats": stats}


@router.post("/admin/dnc-registrations")
async def create_dnc_registration(request: Request, body: DncRegistrationBody):
    admin = await auth_admin(request)
    if not body.name.strip():
        raise HTTPException(422, "Indica el nombre")
    phones = [re.sub(r"\D", "", p)[-10:] for p in body.phones if re.sub(r"\D", "", p)]
    if not phones:
        raise HTTPException(422, "Indica al menos un teléfono")
    if len(phones) > 3:
        raise HTTPException(422, "Máximo 3 teléfonos por sesión (límite de donotcall.gov)")
    db = get_db()
    doc = {
        "name": body.name.strip().title(),
        "email": body.email.strip().lower(),
        "phones": phones,
        "source": body.source,
        "notes": body.notes.strip(),
        "status": "pendiente_email",
        "phone_status": {},           # {phone: {national_dnc, checked_at}}
        "registered_by": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    r = await db.dnc_registrations.insert_one(doc)
    doc["_id"] = r.inserted_id
    return {"success": True, "item": serialize(doc)}


class StatusBody(BaseModel):
    status: str
    notes: str | None = None


@router.put("/admin/dnc-registrations/{reg_id}")
async def update_dnc_registration(request: Request, reg_id: str, body: StatusBody):
    await auth_admin(request)
    if body.status not in STATUSES:
        raise HTTPException(422, f"status debe ser uno de {STATUSES}")
    upd = {"status": body.status, "updated_at": datetime.now(timezone.utc)}
    if body.notes is not None:
        upd["notes"] = body.notes
    r = await get_db().dnc_registrations.update_one({"_id": ObjectId(reg_id)}, {"$set": upd})
    if r.matched_count == 0:
        raise HTTPException(404, "Registro no encontrado")
    return {"success": True}


@router.delete("/admin/dnc-registrations/{reg_id}")
async def delete_dnc_registration(request: Request, reg_id: str):
    await auth_admin(request)
    await get_db().dnc_registrations.delete_one({"_id": ObjectId(reg_id)})
    return {"success": True}


@router.post("/admin/dnc-registrations/{reg_id}/verify")
async def verify_dnc_registration(request: Request, reg_id: str):
    """Verifica vía Tracerfy si los teléfonos ya aparecen en el registro DNC
    (5 créditos por teléfono). Si todos aparecen → status 'verificado'."""
    await auth_admin(request)
    api_key = os.environ.get("TRACERFY_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "Configura TRACERFY_API_KEY en Configuración → API Keys")
    db = get_db()
    doc = await db.dnc_registrations.find_one({"_id": ObjectId(reg_id)})
    if not doc:
        raise HTTPException(404, "Registro no encontrado")
    results, all_in = {}, True
    async with httpx.AsyncClient(timeout=25) as client:
        for phone in (doc.get("phones") or [])[:3]:
            try:
                r = await client.post("https://tracerfy.com/v2/api/dnc/lookup/",
                                      json={"phone": phone},
                                      headers={"Authorization": f"Bearer {api_key}",
                                               "Content-Type": "application/json"})
                d = r.json() if r.status_code < 400 else {}
            except httpx.HTTPError:
                d = {}
            in_dnc = bool(d.get("national_dnc"))
            results[phone] = {"national_dnc": in_dnc,
                              "checked_at": datetime.now(timezone.utc).isoformat()}
            if not in_dnc:
                all_in = False
    upd: dict = {"phone_status": results, "updated_at": datetime.now(timezone.utc)}
    if all_in and results:
        upd["status"] = "verificado"
    await db.dnc_registrations.update_one({"_id": doc["_id"]}, {"$set": upd})
    return {"success": True, "phone_status": results, "all_registered": all_in}
