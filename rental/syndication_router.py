"""
Real Estate Syndication Router
==============================
LP / GP capital raise platform for Ross House Rentals.

Concepts:
 - Deal (offering): a real-estate investment opportunity LPs can join
 - Investment (cap table entry): one LP's position in a deal
 - Distribution: payout from the deal to LPs (pref return / profit / refund)
 - Investor account: an LP login (uses app_users collection with role="investor")

Collections used:
 - syndication_deals
 - syndication_investments
 - syndication_distributions
 - app_users (role="investor")
"""
import logging
import os
import re
import hashlib
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import (
    get_db, auth_admin, auth_marketplace, serialize,
    create_marketplace_token, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

DEAL_STATUSES = {"draft", "open", "funded", "closed", "exited"}
INVESTMENT_STATUSES = {"pending", "active", "redeemed", "cancelled"}
DISTRIBUTION_TYPES = {"pref_return", "profit", "return_of_capital", "refund", "other"}
DISTRIBUTION_STATUSES = {"scheduled", "paid", "failed", "cancelled"}
DOC_TYPES = {"ppm", "subscription_agreement", "operating_agreement", "k1", "financial_report", "exit_summary", "other"}


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s[:80] or f"deal-{int(datetime.utcnow().timestamp())}"


def _hash_password(pw: str) -> str:
    return hashlib.sha256((pw + "ross-investor-salt-2026").encode()).hexdigest()


async def auth_investor(request: Request):
    """Authenticate an investor LP (uses app_users with role='investor')"""
    user = await auth_marketplace(request)
    if user.get("role") not in ("investor", "admin"):
        raise HTTPException(status_code=403, detail="Acceso restringido a inversionistas")
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════════════

def _serialize_deal(d: dict, include_documents: bool = True) -> dict:
    docs = d.get("documents", []) or []
    if not include_documents:
        # Keep only metadata, strip base64 file content
        docs = [{k: v for k, v in doc.items() if k != "data"} for doc in docs]

    return {
        "id": str(d["_id"]),
        "name": d.get("name", ""),
        "slug": d.get("slug", ""),
        "property_address": d.get("property_address", ""),
        "property_type": d.get("property_type", ""),
        "units": d.get("units", 0),
        "status": d.get("status", "draft"),
        "target_raise": d.get("target_raise", 0),
        "min_investment": d.get("min_investment", 0),
        "max_investment": d.get("max_investment", 0),
        "total_raised": d.get("total_raised", 0),
        "num_investors": d.get("num_investors", 0),
        "equity_split": d.get("equity_split", {"lp_percent": 80, "gp_percent": 20}),
        "preferred_return": d.get("preferred_return", 8.0),
        "projected_irr": d.get("projected_irr", 0),
        "projected_cash_on_cash": d.get("projected_cash_on_cash", 0),
        "hold_period_months": d.get("hold_period_months", 60),
        "description": d.get("description", ""),
        "highlights": d.get("highlights", []) or [],
        "open_date": d.get("open_date", "").isoformat() if d.get("open_date") else "",
        "close_date": d.get("close_date", "").isoformat() if d.get("close_date") else "",
        "exit_date": d.get("exit_date", "").isoformat() if d.get("exit_date") else "",
        "cover_image": d.get("cover_image", ""),
        "documents": docs,
        "created_at": d.get("created_at", "").isoformat() if d.get("created_at") else "",
        "updated_at": d.get("updated_at", "").isoformat() if d.get("updated_at") else "",
    }


def _serialize_investment(i: dict) -> dict:
    return {
        "id": str(i["_id"]),
        "deal_id": str(i["deal_id"]) if i.get("deal_id") else "",
        "deal_name": i.get("deal_name", ""),
        "investor_id": str(i["investor_id"]) if i.get("investor_id") else "",
        "investor_name": i.get("investor_name", ""),
        "investor_email": i.get("investor_email", ""),
        "investor_phone": i.get("investor_phone", ""),
        "amount": i.get("amount", 0),
        "equity_percent": round(i.get("equity_percent", 0), 4),
        "status": i.get("status", "pending"),
        "subscription_date": i.get("subscription_date", "").isoformat() if i.get("subscription_date") else "",
        "funding_date": i.get("funding_date", "").isoformat() if i.get("funding_date") else "",
        "documents_signed": bool(i.get("documents_signed", False)),
        "notes": i.get("notes", ""),
        "total_distributions_received": i.get("total_distributions_received", 0),
        "created_at": i.get("created_at", "").isoformat() if i.get("created_at") else "",
    }


def _serialize_distribution(d: dict) -> dict:
    return {
        "id": str(d["_id"]),
        "deal_id": str(d["deal_id"]) if d.get("deal_id") else "",
        "deal_name": d.get("deal_name", ""),
        "distribution_type": d.get("distribution_type", "profit"),
        "period": d.get("period", ""),
        "total_amount": d.get("total_amount", 0),
        "per_investment": d.get("per_investment", []) or [],
        "status": d.get("status", "scheduled"),
        "paid_date": d.get("paid_date", "").isoformat() if d.get("paid_date") else "",
        "scheduled_date": d.get("scheduled_date", "").isoformat() if d.get("scheduled_date") else "",
        "notes": d.get("notes", ""),
        "created_at": d.get("created_at", "").isoformat() if d.get("created_at") else "",
    }


async def _recompute_deal_totals(deal_id_obj: ObjectId):
    """Recompute total_raised, num_investors, equity_percent for all investments"""
    db = get_db()
    pipeline = [
        {"$match": {"deal_id": deal_id_obj, "status": {"$in": ["pending", "active"]}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    agg = await db.syndication_investments.aggregate(pipeline).to_list(length=1)
    total = agg[0]["total"] if agg else 0
    count = agg[0]["count"] if agg else 0
    await db.syndication_deals.update_one(
        {"_id": deal_id_obj},
        {"$set": {"total_raised": total, "num_investors": count, "updated_at": datetime.utcnow()}},
    )
    # Recompute equity_percent per investment (based on % of total_raised)
    if total > 0:
        async for inv in db.syndication_investments.find({"deal_id": deal_id_obj}):
            pct = (inv.get("amount", 0) / total) * 100
            await db.syndication_investments.update_one(
                {"_id": inv["_id"]},
                {"$set": {"equity_percent": pct}},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: DEALS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/syndication/deals')
async def create_deal(request: Request):
    """Admin: create a new syndication deal (offering)"""
    user = await auth_admin(request)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nombre requerido")
    try:
        target = float(data.get("target_raise") or 0)
        min_inv = float(data.get("min_investment") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Montos inválidos")
    if target <= 0 or min_inv <= 0:
        raise HTTPException(status_code=400, detail="target_raise y min_investment deben ser > 0")

    deal = {
        "name": name,
        "slug": _slugify(name),
        "property_address": (data.get("property_address") or "").strip(),
        "property_type": (data.get("property_type") or "multifamily").strip(),
        "units": int(data.get("units") or 0),
        "status": data.get("status") or "draft",
        "target_raise": target,
        "min_investment": min_inv,
        "max_investment": float(data.get("max_investment") or target),
        "total_raised": 0,
        "num_investors": 0,
        "equity_split": {
            "lp_percent": float(data.get("lp_percent") or 80),
            "gp_percent": float(data.get("gp_percent") or 20),
        },
        "preferred_return": float(data.get("preferred_return") or 8.0),
        "projected_irr": float(data.get("projected_irr") or 0),
        "projected_cash_on_cash": float(data.get("projected_cash_on_cash") or 0),
        "hold_period_months": int(data.get("hold_period_months") or 60),
        "description": (data.get("description") or "").strip(),
        "highlights": data.get("highlights") or [],
        "cover_image": data.get("cover_image") or "",
        "documents": [],
        "open_date": _parse_date(data.get("open_date")),
        "close_date": _parse_date(data.get("close_date")),
        "exit_date": _parse_date(data.get("exit_date")),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "created_by": user.get("email", ""),
    }
    if deal["status"] not in DEAL_STATUSES:
        deal["status"] = "draft"

    result = await get_db().syndication_deals.insert_one(deal)
    deal["_id"] = result.inserted_id
    return {"success": True, "deal": _serialize_deal(deal)}


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


@router.get('/admin/syndication/deals')
async def list_deals(
    request: Request,
    status: str = "",
    search: str = "",
    page: int = 1,
    limit: int = 50,
):
    """Admin: list all syndication deals with stats"""
    await auth_admin(request)
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 50), 200))

    query: dict = {}
    if status and status != "all":
        query["status"] = status
    if search:
        rg = {"$regex": re.escape(search), "$options": "i"}
        query["$or"] = [{"name": rg}, {"property_address": rg}, {"description": rg}]

    db = get_db()
    total = await db.syndication_deals.count_documents(query)
    skip = (page - 1) * limit
    cursor = db.syndication_deals.find(query).sort("created_at", -1).skip(skip).limit(limit)
    deals = [_serialize_deal(d, include_documents=False) async for d in cursor]

    # Stats
    stats = {s: 0 for s in DEAL_STATUSES}
    stats["total"] = 0
    stats["total_raised_all"] = 0
    stats["total_target_all"] = 0
    stats["total_investors_all"] = 0
    async for d in db.syndication_deals.aggregate([
        {"$group": {
            "_id": "$status", "count": {"$sum": 1},
            "raised": {"$sum": "$total_raised"}, "target": {"$sum": "$target_raise"},
            "investors": {"$sum": "$num_investors"},
        }},
    ]):
        k = (d.get("_id") or "draft")
        if k in stats:
            stats[k] = d.get("count", 0)
        stats["total"] += d.get("count", 0)
        stats["total_raised_all"] += d.get("raised", 0) or 0
        stats["total_target_all"] += d.get("target", 0) or 0
        stats["total_investors_all"] += d.get("investors", 0) or 0

    return {
        "success": True,
        "deals": deals,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
        "stats": stats,
    }


@router.get('/admin/syndication/deals/{deal_id}')
async def get_deal(deal_id: str, request: Request):
    """Admin: deal detail with full cap table + distributions"""
    await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")

    db = get_db()
    deal = await db.syndication_deals.find_one({"_id": ObjectId(deal_id)})
    if not deal:
        raise HTTPException(status_code=404, detail="Deal no encontrado")

    inv_cursor = db.syndication_investments.find({"deal_id": ObjectId(deal_id)}).sort("created_at", -1)
    investments = [_serialize_investment(i) async for i in inv_cursor]

    dist_cursor = db.syndication_distributions.find({"deal_id": ObjectId(deal_id)}).sort("created_at", -1)
    distributions = [_serialize_distribution(d) async for d in dist_cursor]

    return {
        "success": True,
        "deal": _serialize_deal(deal),
        "investments": investments,
        "distributions": distributions,
    }


@router.patch('/admin/syndication/deals/{deal_id}')
async def update_deal(deal_id: str, request: Request):
    """Admin: update a deal"""
    await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    data = await request.json()

    updates: dict = {"updated_at": datetime.utcnow()}
    # Whitelist fields
    text_fields = ["name", "property_address", "property_type", "description", "cover_image"]
    num_fields = ["target_raise", "min_investment", "max_investment", "preferred_return",
                  "projected_irr", "projected_cash_on_cash", "hold_period_months", "units"]
    for f in text_fields:
        if f in data:
            updates[f] = (data[f] or "").strip()
            if f == "name":
                updates["slug"] = _slugify(updates[f])
    for f in num_fields:
        if f in data:
            try:
                updates[f] = float(data[f]) if data[f] is not None else 0
            except (TypeError, ValueError):
                pass
    if "status" in data:
        s = (data["status"] or "draft").lower()
        if s in DEAL_STATUSES:
            updates["status"] = s
    if "highlights" in data and isinstance(data["highlights"], list):
        updates["highlights"] = [str(h).strip() for h in data["highlights"] if str(h).strip()]
    if "equity_split" in data and isinstance(data["equity_split"], dict):
        try:
            lp = float(data["equity_split"].get("lp_percent", 80))
            gp = float(data["equity_split"].get("gp_percent", 20))
            updates["equity_split"] = {"lp_percent": lp, "gp_percent": gp}
        except (TypeError, ValueError):
            pass
    for f in ["open_date", "close_date", "exit_date"]:
        if f in data:
            updates[f] = _parse_date(data[f])

    result = await get_db().syndication_deals.update_one(
        {"_id": ObjectId(deal_id)}, {"$set": updates}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    deal = await get_db().syndication_deals.find_one({"_id": ObjectId(deal_id)})
    return {"success": True, "deal": _serialize_deal(deal)}


@router.delete('/admin/syndication/deals/{deal_id}')
async def delete_deal(deal_id: str, request: Request):
    """Admin: delete a deal (only allowed if no investments are committed)"""
    await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    has_inv = await db.syndication_investments.find_one({"deal_id": ObjectId(deal_id)})
    if has_inv:
        raise HTTPException(status_code=400, detail="No se puede eliminar: ya tiene inversionistas. Cierra el deal en su lugar.")
    result = await db.syndication_deals.delete_one({"_id": ObjectId(deal_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    await db.syndication_distributions.delete_many({"deal_id": ObjectId(deal_id)})
    return {"success": True, "message": "Deal eliminado"}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: DEAL DOCUMENTS (PPM, K-1, etc.)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/syndication/deals/{deal_id}/documents')
async def add_deal_document(deal_id: str, request: Request):
    """Admin: attach a document (PPM, subscription agreement, K-1, etc.).
    Expects: { name, doc_type, data (base64) }
    """
    user = await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    data = await request.json()
    name = (data.get("name") or "").strip()
    doc_type = (data.get("doc_type") or "other").lower()
    file_data = data.get("data") or ""
    if not name or not file_data:
        raise HTTPException(status_code=400, detail="name y data (base64) requeridos")
    if doc_type not in DOC_TYPES:
        doc_type = "other"

    doc = {
        "id": str(ObjectId()),
        "name": name,
        "doc_type": doc_type,
        "data": file_data,
        "mime_type": data.get("mime_type") or "application/pdf",
        "size_kb": len(file_data) // 1024,
        "uploaded_by": user.get("email", ""),
        "uploaded_at": datetime.utcnow().isoformat(),
        "investor_id": (data.get("investor_id") or "") or None,  # if set, only that LP sees it
    }
    result = await get_db().syndication_deals.update_one(
        {"_id": ObjectId(deal_id)},
        {"$push": {"documents": doc}, "$set": {"updated_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    doc_meta = {k: v for k, v in doc.items() if k != "data"}
    return {"success": True, "document": doc_meta}


@router.delete('/admin/syndication/deals/{deal_id}/documents/{doc_id}')
async def delete_deal_document(deal_id: str, doc_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    result = await get_db().syndication_deals.update_one(
        {"_id": ObjectId(deal_id)},
        {"$pull": {"documents": {"id": doc_id}}, "$set": {"updated_at": datetime.utcnow()}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"success": True, "message": "Documento eliminado"}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: INVESTMENTS (CAP TABLE)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/syndication/deals/{deal_id}/investments')
async def add_investment(deal_id: str, request: Request):
    """Admin: add an LP investment to a deal (creates investor account if needed).
    Expects: { investor_name, investor_email, investor_phone, amount, status?, notes? }
    """
    user = await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")

    db = get_db()
    deal = await db.syndication_deals.find_one({"_id": ObjectId(deal_id)})
    if not deal:
        raise HTTPException(status_code=404, detail="Deal no encontrado")

    data = await request.json()
    inv_name = (data.get("investor_name") or "").strip()
    inv_email = (data.get("investor_email") or "").strip().lower()
    inv_phone = (data.get("investor_phone") or "").strip()
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Monto inválido")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Monto debe ser > 0")
    if not inv_name or not (inv_email or inv_phone):
        raise HTTPException(status_code=400, detail="Nombre y email/teléfono requeridos")

    # Find or create investor account
    investor_id = None
    if inv_email:
        existing = await db.app_users.find_one({"email": inv_email})
        if existing:
            investor_id = existing["_id"]
            # Upgrade role if needed
            if existing.get("role") not in ("investor", "admin"):
                # Don't downgrade admins
                await db.app_users.update_one({"_id": existing["_id"]}, {"$set": {"role": "investor"}})
        else:
            # Create new investor account with temp password
            temp_pw = f"Inv{datetime.utcnow().strftime('%y%m%d%H%M')}"
            new_user = {
                "email": inv_email,
                "name": inv_name,
                "phone": inv_phone,
                "role": "investor",
                "password_hash": _hash_password(temp_pw),
                "_temp_password": temp_pw,  # will be cleared after first login or sent to user
                "created_at": datetime.utcnow(),
                "created_by": user.get("email", ""),
                "auto_created_for_deal": str(deal["_id"]),
            }
            ins = await db.app_users.insert_one(new_user)
            investor_id = ins.inserted_id

    status_val = (data.get("status") or "pending").lower()
    if status_val not in INVESTMENT_STATUSES:
        status_val = "pending"

    investment = {
        "deal_id": ObjectId(deal_id),
        "deal_name": deal.get("name", ""),
        "investor_id": investor_id,
        "investor_name": inv_name,
        "investor_email": inv_email,
        "investor_phone": inv_phone,
        "amount": amount,
        "equity_percent": 0,  # recomputed below
        "status": status_val,
        "subscription_date": _parse_date(data.get("subscription_date")) or datetime.utcnow(),
        "funding_date": _parse_date(data.get("funding_date")),
        "documents_signed": bool(data.get("documents_signed", False)),
        "notes": (data.get("notes") or "").strip(),
        "total_distributions_received": 0,
        "created_at": datetime.utcnow(),
        "created_by": user.get("email", ""),
    }
    result = await db.syndication_investments.insert_one(investment)
    investment["_id"] = result.inserted_id

    await _recompute_deal_totals(ObjectId(deal_id))

    return {"success": True, "investment": _serialize_investment(investment)}


@router.patch('/admin/syndication/investments/{inv_id}')
async def update_investment(inv_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(inv_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    inv = await db.syndication_investments.find_one({"_id": ObjectId(inv_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")
    data = await request.json()
    updates: dict = {}
    if "amount" in data:
        try:
            amt = float(data["amount"])
            if amt <= 0:
                raise ValueError
            updates["amount"] = amt
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Monto inválido")
    if "status" in data:
        s = (data["status"] or "").lower()
        if s in INVESTMENT_STATUSES:
            updates["status"] = s
    if "notes" in data:
        updates["notes"] = (data["notes"] or "").strip()
    if "documents_signed" in data:
        updates["documents_signed"] = bool(data["documents_signed"])
    if "funding_date" in data:
        updates["funding_date"] = _parse_date(data["funding_date"])
    if not updates:
        raise HTTPException(status_code=400, detail="Sin cambios")
    await db.syndication_investments.update_one({"_id": ObjectId(inv_id)}, {"$set": updates})
    await _recompute_deal_totals(inv["deal_id"])
    inv = await db.syndication_investments.find_one({"_id": ObjectId(inv_id)})
    return {"success": True, "investment": _serialize_investment(inv)}


@router.delete('/admin/syndication/investments/{inv_id}')
async def delete_investment(inv_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(inv_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    inv = await db.syndication_investments.find_one({"_id": ObjectId(inv_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Inversión no encontrada")
    await db.syndication_investments.delete_one({"_id": ObjectId(inv_id)})
    await _recompute_deal_totals(inv["deal_id"])
    return {"success": True, "message": "Inversión eliminada"}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: DISTRIBUTIONS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/syndication/deals/{deal_id}/distributions')
async def create_distribution(deal_id: str, request: Request):
    """Admin: create a distribution to all LPs in a deal.
    Expects: { distribution_type, period, total_amount, notes?, status? }
    Automatically computes per-LP amounts based on current equity_percent.
    """
    user = await auth_admin(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    deal = await db.syndication_deals.find_one({"_id": ObjectId(deal_id)})
    if not deal:
        raise HTTPException(status_code=404, detail="Deal no encontrado")

    data = await request.json()
    dtype = (data.get("distribution_type") or "profit").lower()
    if dtype not in DISTRIBUTION_TYPES:
        dtype = "profit"
    period = (data.get("period") or "").strip()
    try:
        total = float(data.get("total_amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Monto inválido")
    if total <= 0:
        raise HTTPException(status_code=400, detail="Monto debe ser > 0")
    if not period:
        period = datetime.utcnow().strftime("%Y-%m")

    # Compute per-investment payouts (pro-rata by equity_percent of active investments)
    per_investment: List[Dict[str, Any]] = []
    inv_cursor = db.syndication_investments.find({
        "deal_id": ObjectId(deal_id),
        "status": {"$in": ["pending", "active"]},
    })
    investments = [i async for i in inv_cursor]
    total_pct = sum(i.get("equity_percent", 0) for i in investments)
    if total_pct == 0:
        raise HTTPException(status_code=400, detail="El deal no tiene inversiones activas")
    for inv in investments:
        share = (inv.get("equity_percent", 0) / total_pct) * total
        per_investment.append({
            "investment_id": str(inv["_id"]),
            "investor_id": str(inv.get("investor_id")) if inv.get("investor_id") else "",
            "investor_name": inv.get("investor_name", ""),
            "investor_email": inv.get("investor_email", ""),
            "amount": round(share, 2),
        })

    status_val = (data.get("status") or "scheduled").lower()
    if status_val not in DISTRIBUTION_STATUSES:
        status_val = "scheduled"

    dist = {
        "deal_id": ObjectId(deal_id),
        "deal_name": deal.get("name", ""),
        "distribution_type": dtype,
        "period": period,
        "total_amount": total,
        "per_investment": per_investment,
        "status": status_val,
        "scheduled_date": _parse_date(data.get("scheduled_date")) or datetime.utcnow(),
        "paid_date": _parse_date(data.get("paid_date")),
        "notes": (data.get("notes") or "").strip(),
        "created_at": datetime.utcnow(),
        "created_by": user.get("email", ""),
    }
    result = await db.syndication_distributions.insert_one(dist)
    dist["_id"] = result.inserted_id

    # If paid, update each investment's total_distributions_received and notify
    if status_val == "paid":
        await _apply_distribution_to_investments(dist)
        await _notify_distribution(dist, deal)

    return {"success": True, "distribution": _serialize_distribution(dist)}


async def _apply_distribution_to_investments(dist: dict):
    db = get_db()
    for entry in dist.get("per_investment", []):
        try:
            await db.syndication_investments.update_one(
                {"_id": ObjectId(entry["investment_id"])},
                {"$inc": {"total_distributions_received": entry.get("amount", 0)}},
            )
        except Exception as e:
            logger.warning(f"Failed to apply distribution to {entry.get('investment_id')}: {e}")


async def _notify_distribution(dist: dict, deal: dict):
    """Send distribution emails to LPs (best-effort)"""
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not sendgrid_key:
        cfg = await get_db().api_config.find_one({"_id": "main"})
        if cfg:
            sendgrid_key = cfg.get("sendgrid_api_key") or cfg.get("SENDGRID_API_KEY")
            from_email = cfg.get("sendgrid_from_email", from_email)
    if not sendgrid_key:
        logger.info("ℹ️ SENDGRID_API_KEY missing, distribution emails skipped")
        return
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        for entry in dist.get("per_investment", []):
            inv_email = entry.get("investor_email")
            if not inv_email:
                continue
            amt = entry.get("amount", 0)
            html = f"""
            <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
              <div style="background:linear-gradient(135deg,#10b981,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                <h2 style="margin:0;color:#fff;">💰 Distribución recibida</h2>
              </div>
              <p style="color:#cbd5e1;margin-top:18px;">Hola <strong style="color:#fff;">{entry.get('investor_name','')}</strong>,</p>
              <p style="color:#cbd5e1;">Tu distribución del deal <strong>{deal.get('name','')}</strong> ha sido procesada.</p>
              <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;">
                <div><span style="color:#94a3b8;">Tipo:</span> <strong>{dist.get('distribution_type','').replace('_',' ').title()}</strong></div>
                <div><span style="color:#94a3b8;">Período:</span> {dist.get('period','')}</div>
                <div style="font-size:22px;color:#10b981;margin-top:8px;"><strong>${amt:,.2f}</strong></div>
              </div>
              <p style="color:#cbd5e1;font-size:13px;">Puedes ver el detalle completo en tu portal:</p>
              <a href="https://www.rosshouserentals.com/inversor/dashboard" 
                 style="display:inline-block;background:#10b981;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">
                Ver mi portafolio →
              </a>
              <p style="color:#64748b;font-size:11px;margin-top:18px;">— Ross House Rentals</p>
            </div>
            """
            try:
                mail = Mail(
                    from_email=Email(from_email, "Ross House Rentals"),
                    to_emails=To(inv_email),
                    subject=f"💰 Distribución recibida: {deal.get('name','')} (${amt:,.2f})",
                    plain_text_content=Content("text/plain",
                        f"Hola {entry.get('investor_name','')},\nTu distribución del deal '{deal.get('name','')}' de ${amt:,.2f} ha sido procesada.\n\nVer detalle: https://www.rosshouserentals.com/inversor/dashboard"),
                )
                mail.add_content(Content("text/html", html))
                sg.client.mail.send.post(request_body=mail.get())
                logger.info(f"📧 Distribution email sent to {inv_email}")
            except Exception as e:
                logger.warning(f"Distribution email failed for {inv_email}: {e}")
    except Exception as e:
        logger.warning(f"Distribution notification block failed: {e}")


@router.patch('/admin/syndication/distributions/{dist_id}')
async def update_distribution(dist_id: str, request: Request):
    """Admin: update distribution (e.g. mark paid)"""
    await auth_admin(request)
    if not ObjectId.is_valid(dist_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    dist = await db.syndication_distributions.find_one({"_id": ObjectId(dist_id)})
    if not dist:
        raise HTTPException(status_code=404, detail="Distribución no encontrada")

    data = await request.json()
    updates: dict = {}
    new_status = None
    if "status" in data:
        s = (data["status"] or "").lower()
        if s in DISTRIBUTION_STATUSES:
            updates["status"] = s
            new_status = s
            if s == "paid":
                updates["paid_date"] = _parse_date(data.get("paid_date")) or datetime.utcnow()
    if "notes" in data:
        updates["notes"] = (data["notes"] or "").strip()
    if not updates:
        raise HTTPException(status_code=400, detail="Sin cambios")
    await db.syndication_distributions.update_one({"_id": ObjectId(dist_id)}, {"$set": updates})

    # Apply distribution amounts to investments if transitioning to paid
    was_paid = dist.get("status") == "paid"
    if new_status == "paid" and not was_paid:
        # Need to fetch the deal name for notification
        deal = await db.syndication_deals.find_one({"_id": dist["deal_id"]})
        await _apply_distribution_to_investments(dist)
        if deal:
            await _notify_distribution(dist, deal)

    dist = await db.syndication_distributions.find_one({"_id": ObjectId(dist_id)})
    return {"success": True, "distribution": _serialize_distribution(dist)}


@router.delete('/admin/syndication/distributions/{dist_id}')
async def delete_distribution(dist_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(dist_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    dist = await db.syndication_distributions.find_one({"_id": ObjectId(dist_id)})
    if not dist:
        raise HTTPException(status_code=404, detail="No encontrada")
    if dist.get("status") == "paid":
        raise HTTPException(status_code=400, detail="No se puede eliminar una distribución ya pagada")
    await db.syndication_distributions.delete_one({"_id": ObjectId(dist_id)})
    return {"success": True, "message": "Distribución eliminada"}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: INVESTORS (cross-deal view)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get('/admin/syndication/investors')
async def list_investors(
    request: Request,
    search: str = "",
    page: int = 1,
    limit: int = 100,
):
    """Admin: list all investors with their aggregated portfolios"""
    await auth_admin(request)
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 100), 500))
    db = get_db()

    # Query investor accounts
    query: dict = {"role": "investor"}
    if search:
        rg = {"$regex": re.escape(search), "$options": "i"}
        query["$or"] = [{"email": rg}, {"name": rg}, {"phone": rg}]

    total = await db.app_users.count_documents(query)
    skip = (page - 1) * limit
    cursor = db.app_users.find(query).sort("name", 1).skip(skip).limit(limit)
    investors: List[dict] = []
    async for u in cursor:
        # Aggregate investments
        agg = await db.syndication_investments.aggregate([
            {"$match": {"investor_id": u["_id"], "status": {"$in": ["pending", "active"]}}},
            {"$group": {"_id": None, "total_invested": {"$sum": "$amount"},
                        "total_distributions": {"$sum": "$total_distributions_received"},
                        "deal_count": {"$sum": 1}}},
        ]).to_list(length=1)
        a = agg[0] if agg else {"total_invested": 0, "total_distributions": 0, "deal_count": 0}
        investors.append({
            "id": str(u["_id"]),
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "total_invested": a.get("total_invested", 0),
            "total_distributions_received": a.get("total_distributions", 0),
            "active_deals": a.get("deal_count", 0),
            "created_at": u.get("created_at", "").isoformat() if u.get("created_at") else "",
            "last_login": u.get("last_login", "").isoformat() if u.get("last_login") else "",
        })
    return {
        "success": True,
        "investors": investors,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


@router.get('/admin/syndication/investors/{investor_id}')
async def get_investor(investor_id: str, request: Request):
    """Admin: investor detail with all their investments"""
    await auth_admin(request)
    if not ObjectId.is_valid(investor_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    u = await db.app_users.find_one({"_id": ObjectId(investor_id)})
    if not u:
        raise HTTPException(status_code=404, detail="Inversionista no encontrado")
    inv_cursor = db.syndication_investments.find({"investor_id": ObjectId(investor_id)}).sort("created_at", -1)
    investments = [_serialize_investment(i) async for i in inv_cursor]
    return {
        "success": True,
        "investor": {
            "id": str(u["_id"]),
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "role": u.get("role", "investor"),
            "created_at": u.get("created_at", "").isoformat() if u.get("created_at") else "",
        },
        "investments": investments,
    }


@router.post('/admin/syndication/investors/{investor_id}/reset-password')
async def admin_reset_investor_password(investor_id: str, request: Request):
    """Admin: reset an investor's password (returns the new temp password)"""
    await auth_admin(request)
    if not ObjectId.is_valid(investor_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    user = await db.app_users.find_one({"_id": ObjectId(investor_id)})
    if not user:
        raise HTTPException(status_code=404, detail="No encontrado")
    temp_pw = f"Inv{datetime.utcnow().strftime('%y%m%d%H%M')}"
    await db.app_users.update_one(
        {"_id": ObjectId(investor_id)},
        {"$set": {"password_hash": _hash_password(temp_pw), "_temp_password": temp_pw}},
    )
    return {"success": True, "temp_password": temp_pw, "email": user.get("email", "")}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC: OPEN DEALS (marketing)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get('/public/syndication/deals')
async def public_list_deals():
    """Public: list deals that are currently 'open' for investment"""
    db = get_db()
    cursor = db.syndication_deals.find({"status": "open"}).sort("created_at", -1).limit(50)
    deals = [_serialize_deal(d, include_documents=False) async for d in cursor]
    return {"success": True, "deals": deals}


@router.get('/public/syndication/deals/{slug}')
async def public_get_deal_by_slug(slug: str):
    """Public: deal details by slug (for marketing page)"""
    db = get_db()
    deal = await db.syndication_deals.find_one({"slug": slug, "status": {"$in": ["open", "funded", "closed"]}})
    if not deal:
        raise HTTPException(status_code=404, detail="Deal no encontrado o no público")
    return {"success": True, "deal": _serialize_deal(deal, include_documents=False)}


@router.post('/public/syndication/inquire')
async def public_inquire(request: Request):
    """Public: express interest in a deal (anonymous)"""
    data = await request.json()
    deal_id = data.get("deal_id") or ""
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    amount_interested = data.get("amount_interested") or 0
    if not name or not (email or phone):
        raise HTTPException(status_code=400, detail="Nombre y email/teléfono requeridos")
    inquiry = {
        "deal_id": ObjectId(deal_id) if ObjectId.is_valid(deal_id) else None,
        "name": name,
        "email": email,
        "phone": phone,
        "amount_interested": amount_interested,
        "message": (data.get("message") or "").strip(),
        "status": "new",
        "created_at": datetime.utcnow(),
    }
    result = await get_db().syndication_inquiries.insert_one(inquiry)
    # Notify admins
    try:
        await send_rental_push_to_admins(
            title="💼 Nuevo interés en deal",
            body=f"{name} · ${amount_interested:,.0f}" if isinstance(amount_interested, (int, float)) else name,
            data={"type": "syndication_inquiry", "inquiry_id": str(result.inserted_id)},
        )
    except Exception:
        pass
    return {"success": True, "inquiry_id": str(result.inserted_id)}


# ═══════════════════════════════════════════════════════════════════════════════
# INVESTOR PORTAL — AUTH
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/investor/login')
async def investor_login(request: Request):
    """Investor portal login (email + password). Returns marketplace JWT."""
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    pw = data.get("password") or ""
    if not email or not pw:
        raise HTTPException(status_code=400, detail="Email y password requeridos")
    db = get_db()
    user = await db.app_users.find_one({"email": email})
    if not user or user.get("role") not in ("investor", "admin"):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    if user.get("password_hash") != _hash_password(pw):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    await db.app_users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login": datetime.utcnow()}, "$unset": {"_temp_password": ""}},
    )
    token = create_marketplace_token(str(user["_id"]), email, user.get("role", "investor"))
    return {
        "success": True,
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "email": email,
            "name": user.get("name", ""),
            "role": user.get("role", "investor"),
        },
    }


@router.post('/investor/change-password')
async def investor_change_password(request: Request):
    """Investor: change own password"""
    user = await auth_investor(request)
    data = await request.json()
    old_pw = data.get("old_password") or ""
    new_pw = data.get("new_password") or ""
    if not new_pw or len(new_pw) < 6:
        raise HTTPException(status_code=400, detail="Nueva contraseña debe tener al menos 6 caracteres")
    db = get_db()
    u = await db.app_users.find_one({"_id": ObjectId(user["id"])})
    if not u or u.get("password_hash") != _hash_password(old_pw):
        raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")
    await db.app_users.update_one(
        {"_id": u["_id"]},
        {"$set": {"password_hash": _hash_password(new_pw)}, "$unset": {"_temp_password": ""}},
    )
    return {"success": True, "message": "Contraseña actualizada"}


# ═══════════════════════════════════════════════════════════════════════════════
# INVESTOR PORTAL — DATA
# ═══════════════════════════════════════════════════════════════════════════════

@router.get('/investor/dashboard')
async def investor_dashboard(request: Request):
    """Investor dashboard: portfolio summary"""
    user = await auth_investor(request)
    db = get_db()
    investor_id = ObjectId(user["id"])
    inv_cursor = db.syndication_investments.find({"investor_id": investor_id}).sort("created_at", -1)
    investments = []
    total_invested = 0
    total_dist_received = 0
    deal_ids = set()
    async for i in inv_cursor:
        investments.append(_serialize_investment(i))
        if i.get("status") in ("pending", "active"):
            total_invested += i.get("amount", 0)
            deal_ids.add(i["deal_id"])
        total_dist_received += i.get("total_distributions_received", 0)

    # Recent distributions across all my investments
    inv_ids = [inv["id"] for inv in investments]
    recent_dist = []
    if inv_ids:
        dist_cursor = db.syndication_distributions.find({
            "per_investment.investment_id": {"$in": inv_ids},
        }).sort("created_at", -1).limit(20)
        async for d in dist_cursor:
            # Filter per_investment to just this investor
            d_copy = dict(d)
            d_copy["per_investment"] = [p for p in d.get("per_investment", []) if p.get("investment_id") in inv_ids]
            recent_dist.append(_serialize_distribution(d_copy))

    return {
        "success": True,
        "investor": {
            "id": str(user["id"]),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
        },
        "summary": {
            "total_invested": round(total_invested, 2),
            "total_distributions_received": round(total_dist_received, 2),
            "active_deals": len(deal_ids),
            "roi_percent": round((total_dist_received / total_invested * 100), 2) if total_invested > 0 else 0,
        },
        "investments": investments,
        "recent_distributions": recent_dist,
    }


@router.get('/investor/deals')
async def investor_deals(request: Request):
    """Investor: list deals where I have an investment"""
    user = await auth_investor(request)
    db = get_db()
    investor_id = ObjectId(user["id"])
    deal_ids = await db.syndication_investments.distinct("deal_id", {"investor_id": investor_id})
    if not deal_ids:
        return {"success": True, "deals": []}
    cursor = db.syndication_deals.find({"_id": {"$in": deal_ids}}).sort("created_at", -1)
    deals = [_serialize_deal(d, include_documents=False) async for d in cursor]
    return {"success": True, "deals": deals}


@router.get('/investor/deals/{deal_id}')
async def investor_deal_detail(deal_id: str, request: Request):
    """Investor: detail of one of MY deals (with my investment(s) + distributions)"""
    user = await auth_investor(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    investor_id = ObjectId(user["id"])

    deal = await db.syndication_deals.find_one({"_id": ObjectId(deal_id)})
    if not deal:
        raise HTTPException(status_code=404, detail="Deal no encontrado")

    # Verify investor has a position in this deal
    my_investments_cursor = db.syndication_investments.find({
        "deal_id": ObjectId(deal_id),
        "investor_id": investor_id,
    })
    my_investments = [_serialize_investment(i) async for i in my_investments_cursor]
    if not my_investments and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="No tienes inversión en este deal")

    inv_ids = [inv["id"] for inv in my_investments]
    dist_cursor = db.syndication_distributions.find({
        "deal_id": ObjectId(deal_id),
        "per_investment.investment_id": {"$in": inv_ids},
    }).sort("created_at", -1)
    my_distributions = []
    async for d in dist_cursor:
        d_copy = dict(d)
        d_copy["per_investment"] = [p for p in d.get("per_investment", []) if p.get("investment_id") in inv_ids]
        my_distributions.append(_serialize_distribution(d_copy))

    # Filter documents: shared (no investor_id) + ones explicitly for me
    deal_docs = deal.get("documents", []) or []
    my_docs = [
        {k: v for k, v in d.items() if k != "data"}
        for d in deal_docs
        if not d.get("investor_id") or d.get("investor_id") == str(investor_id)
    ]
    deal_clean = _serialize_deal(deal, include_documents=False)
    deal_clean["documents"] = my_docs

    return {
        "success": True,
        "deal": deal_clean,
        "my_investments": my_investments,
        "my_distributions": my_distributions,
    }


@router.get('/investor/deals/{deal_id}/documents/{doc_id}/download')
async def investor_download_document(deal_id: str, doc_id: str, request: Request):
    """Investor: download a single document (returns base64)"""
    user = await auth_investor(request)
    if not ObjectId.is_valid(deal_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    investor_id = ObjectId(user["id"])

    # Verify access — investor must have an investment in this deal
    if user.get("role") != "admin":
        has_inv = await db.syndication_investments.find_one({
            "deal_id": ObjectId(deal_id),
            "investor_id": investor_id,
        })
        if not has_inv:
            raise HTTPException(status_code=403, detail="No tienes acceso a este deal")

    deal = await db.syndication_deals.find_one({"_id": ObjectId(deal_id)})
    if not deal:
        raise HTTPException(status_code=404, detail="Deal no encontrado")

    doc = next((d for d in (deal.get("documents") or []) if d.get("id") == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    if doc.get("investor_id") and doc["investor_id"] != str(investor_id) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Documento no disponible")

    return {
        "success": True,
        "name": doc.get("name", ""),
        "mime_type": doc.get("mime_type", "application/pdf"),
        "data": doc.get("data", ""),
    }


@router.get('/investor/distributions')
async def investor_distributions(request: Request):
    """Investor: list all my distributions"""
    user = await auth_investor(request)
    db = get_db()
    investor_id = ObjectId(user["id"])
    # Find my investment IDs
    inv_ids = [str(i["_id"]) async for i in db.syndication_investments.find({"investor_id": investor_id})]
    if not inv_ids:
        return {"success": True, "distributions": []}
    cursor = db.syndication_distributions.find({
        "per_investment.investment_id": {"$in": inv_ids},
    }).sort("created_at", -1)
    out = []
    async for d in cursor:
        d_copy = dict(d)
        d_copy["per_investment"] = [p for p in d.get("per_investment", []) if p.get("investment_id") in inv_ids]
        out.append(_serialize_distribution(d_copy))
    return {"success": True, "distributions": out}
