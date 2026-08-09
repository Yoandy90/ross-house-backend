"""
Title Companies Router — Casas de título para cierres de compra
================================================================
CRUD de casas de título (title companies) usadas en los contratos de
compra del Deal Finder. Se siembra automáticamente Chicago Title of
Texas (Amarillo) — la casa de título con la que ya trabaja el negocio.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter()

SEED_ID = "chicago-title-amarillo"
SEED_COMPANY = {
    "_id": SEED_ID,
    "name": "Chicago Title of Texas, LLC",
    "escrow_officer": "Shalmarie Permenter",
    "phone": "(806) 358-0893",
    "fax": "(806) 993-3757",
    "email": "Val.Bedoy@ctt.com",
    "address": "4211 I-40 West, Suite 100, Amarillo, TX 79106",
    "bank_name": "Amarillo National Bank",
    "routing_number": "111300958",
    "account_number": "203793",
    "wire_notes": ("SOLO transferencias WIRE (no ACH). Verificar instrucciones "
                   "verbalmente al (806) 358-0893 antes de enviar fondos. "
                   "Las instrucciones de wire NO cambian — sospechar fraude si "
                   "alguien pide cambiarlas."),
    "is_default": True,
}


class TitleCompanyBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    escrow_officer: Optional[str] = Field(default="", max_length=100)
    phone: Optional[str] = Field(default="", max_length=30)
    fax: Optional[str] = Field(default="", max_length=30)
    email: Optional[str] = Field(default="", max_length=120)
    address: Optional[str] = Field(default="", max_length=200)
    bank_name: Optional[str] = Field(default="", max_length=120)
    routing_number: Optional[str] = Field(default="", max_length=20)
    account_number: Optional[str] = Field(default="", max_length=30)
    wire_notes: Optional[str] = Field(default="", max_length=1000)
    is_default: bool = False


def _out(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = d.pop("_id")
    return d


async def _ensure_seed(db):
    if await db.title_companies.count_documents({}) == 0:
        seed = {**SEED_COMPANY,
                "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}
        await db.title_companies.insert_one(seed)
        logger.info("[title_companies] seeded Chicago Title of Texas")


@router.get("/admin/title-companies")
async def list_title_companies(request: Request):
    await auth_admin(request)
    db = get_db()
    await _ensure_seed(db)
    cur = db.title_companies.find({}).sort([("is_default", -1), ("name", 1)])
    items = [_out(doc) async for doc in cur]
    return {"items": items, "total": len(items)}


@router.post("/admin/title-companies")
async def create_title_company(request: Request, body: TitleCompanyBody):
    await auth_admin(request)
    db = get_db()
    doc = body.dict()
    doc["_id"] = str(uuid.uuid4())
    doc["created_at"] = datetime.utcnow()
    doc["updated_at"] = datetime.utcnow()
    if doc.get("is_default"):
        await db.title_companies.update_many({}, {"$set": {"is_default": False}})
    await db.title_companies.insert_one(doc)
    return {"success": True, "item": _out(doc)}


@router.put("/admin/title-companies/{company_id}")
async def update_title_company(request: Request, company_id: str, body: TitleCompanyBody):
    await auth_admin(request)
    db = get_db()
    updates = body.dict()
    updates["updated_at"] = datetime.utcnow()
    if updates.get("is_default"):
        await db.title_companies.update_many(
            {"_id": {"$ne": company_id}}, {"$set": {"is_default": False}})
    result = await db.title_companies.update_one({"_id": company_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Casa de título no encontrada")
    doc = await db.title_companies.find_one({"_id": company_id})
    return {"success": True, "item": _out(doc)}


@router.delete("/admin/title-companies/{company_id}")
async def delete_title_company(request: Request, company_id: str):
    await auth_admin(request)
    db = get_db()
    result = await db.title_companies.delete_one({"_id": company_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Casa de título no encontrada")
    return {"success": True, "deleted": company_id}
