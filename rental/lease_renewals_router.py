"""
Lease Auto-Renewals — AI Brain Phase 3b
========================================
Analyzes leases that are ending in the next 60 days and lets Claude Sonnet 4.5
recommend an action (renew / raise rent / do not renew) with a rationale.

Endpoints (all admin-only):
  GET  /api/admin/lease-renewals/proposals        — list proposals (creates if missing)
  POST /api/admin/lease-renewals/refresh          — regenerate proposals for a lease
  POST /api/admin/lease-renewals/{id}/approve     — approve + queue notification
  POST /api/admin/lease-renewals/{id}/reject      — mark as rejected
  PATCH /api/admin/lease-renewals/{id}            — edit proposed rent / notes

Stored in collection `lease_renewal_proposals`:
  {
    "_id": ObjectId,
    "lease_id": str,
    "property_id": str,
    "tenant_email": str,
    "tenant_name": str,
    "property_address": str,
    "current_rent": float,
    "lease_end_date": date,
    "days_until_end": int,
    "recommendation": "renew" | "raise" | "non_renew",
    "proposed_rent": float,
    "confidence": "high"|"med"|"low",
    "rationale": str,
    "market_signals": {...},
    "status": "draft" | "approved" | "rejected" | "sent",
    "created_at", "updated_at",
    "approved_by", "approved_at",
  }
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from bson import ObjectId

from .shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewals"])

MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-4-5-20250929"

WINDOW_DAYS = 60  # window in advance to prepare a proposal


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif k == "_id":
            out[k] = str(v)
        else:
            out[k] = v
    return out


async def _lease_market_context(db, lease: Dict[str, Any]) -> Dict[str, Any]:
    """Build lightweight market signals for the LLM (bedrooms, average rent, payment history)."""
    prop_id = lease.get("property_id")
    property_doc = None
    if prop_id:
        try:
            property_doc = await db.properties.find_one({"_id": ObjectId(prop_id)} if isinstance(prop_id, str) else {"_id": prop_id})
        except Exception:
            property_doc = None

    # Comparable rent (same city / bedrooms)
    comparable_avg = None
    if property_doc:
        try:
            pipeline = [
                {"$match": {
                    "city": property_doc.get("city"),
                    "bedrooms": property_doc.get("bedrooms"),
                    "status": {"$in": ["rented", "available"]},
                    "_id": {"$ne": property_doc["_id"]},
                }},
                {"$group": {"_id": None, "avg": {"$avg": "$rent_amount"}, "n": {"$sum": 1}}},
            ]
            r = await db.properties.aggregate(pipeline).to_list(1)
            if r:
                comparable_avg = round(r[0].get("avg") or 0, 2)
        except Exception:
            pass

    # Payment behavior — count on-time vs late payments in last 12 months
    on_time = 0
    late = 0
    total_paid = 0.0
    try:
        year_ago = datetime.now(timezone.utc) - timedelta(days=365)
        pays = await db.rent_payments.find({
            "lease_id": str(lease.get("_id")),
            "created_at": {"$gte": year_ago},
        }).to_list(200)
        for p in pays:
            total_paid += float(p.get("amount") or 0)
            due = p.get("due_date")
            paid = p.get("paid_at") or p.get("created_at")
            if due and paid:
                try:
                    d1 = due if isinstance(due, datetime) else datetime.fromisoformat(str(due))
                    d2 = paid if isinstance(paid, datetime) else datetime.fromisoformat(str(paid))
                    if (d2 - d1).total_seconds() > 3 * 86400:
                        late += 1
                    else:
                        on_time += 1
                except Exception:
                    on_time += 1
    except Exception:
        pass

    return {
        "comparable_avg_rent": comparable_avg,
        "on_time_payments_12mo": on_time,
        "late_payments_12mo": late,
        "total_paid_12mo": round(total_paid, 2),
        "property": {
            "address": (property_doc or {}).get("address"),
            "city": (property_doc or {}).get("city"),
            "bedrooms": (property_doc or {}).get("bedrooms"),
            "bathrooms": (property_doc or {}).get("bathrooms"),
            "current_rent": (property_doc or {}).get("rent_amount"),
        } if property_doc else None,
    }


async def _llm_analyze(lease: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return _rule_based_recommendation(lease, ctx)
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        system_prompt = (
            "Eres el CFO virtual de Ross House Rentals. Analizas si conviene renovar el "
            "contrato de arrendamiento. Devuelve SOLO JSON con:\n"
            "{\n"
            '  "recommendation": "renew" | "raise" | "non_renew",\n'
            '  "proposed_rent": number,\n'
            '  "confidence": "high" | "med" | "low",\n'
            '  "rationale": "2 oraciones claras en español que justifiquen la decisión",\n'
            '  "highlights": ["punto clave 1","punto clave 2","..."]\n'
            "}\n"
            "Regla: 'renew' = mismo precio; 'raise' = subir 3-8% si mercado subió y pagó a tiempo; "
            "'non_renew' = si pagos consistentemente tardíos, quejas, o mercado bajó fuerte."
        )
        payload = {
            "lease": {
                "tenant": lease.get("tenant_name"),
                "start": str(lease.get("start_date")),
                "end": str(lease.get("end_date") or lease.get("lease_end_date")),
                "rent": lease.get("monthly_rent") or ctx.get("property", {}).get("current_rent"),
            },
            "market_signals": ctx,
        }
        chat = LlmChat(api_key=api_key, session_id=f"lease_renewal_{uuid4()}", system_message=system_prompt).with_model(MODEL_PROVIDER, MODEL_NAME)
        from emergentintegrations.llm.chat import UserMessage as UM
        raw = await chat.send_message(UM(text=f"Analiza y devuelve JSON:\n```json\n{json.dumps(payload, default=str, ensure_ascii=False, indent=2)}\n```"))
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = text.split("```", 2)
            text = text[1] if len(text) > 1 else text[0]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip("` \n")
        try:
            return json.loads(text)
        except Exception:
            first = text.find("{"); last = text.rfind("}")
            return json.loads(text[first:last + 1]) if first >= 0 and last > first else _rule_based_recommendation(lease, ctx)
    except Exception as e:
        logger.exception(f"[lease-renewals] LLM fail: {e}")
        return _rule_based_recommendation(lease, ctx)


def _rule_based_recommendation(lease: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    current = float(lease.get("monthly_rent") or (ctx.get("property") or {}).get("current_rent") or 0)
    comp = ctx.get("comparable_avg_rent") or current
    late = ctx.get("late_payments_12mo") or 0
    on_time = ctx.get("on_time_payments_12mo") or 0

    if late >= 4:
        return {
            "recommendation": "non_renew",
            "proposed_rent": current,
            "confidence": "med",
            "rationale": "Historial de pagos con muchos atrasos; considerar no renovar o requerir garantía adicional.",
            "highlights": [f"{late} pagos tardíos", f"{on_time} pagos a tiempo"],
        }
    if comp > current * 1.05:
        raise_amt = min(current * 1.06, comp)
        return {
            "recommendation": "raise",
            "proposed_rent": round(raise_amt, 0),
            "confidence": "high",
            "rationale": f"Mercado comparable está en ${comp:.0f}; subir renta ~6% mantiene competitividad.",
            "highlights": [f"Comparable: ${comp:.0f}", f"Actual: ${current:.0f}"],
        }
    return {
        "recommendation": "renew",
        "proposed_rent": current,
        "confidence": "high",
        "rationale": "Buen inquilino y renta alineada con mercado. Renovar al mismo precio para retener.",
        "highlights": [f"{on_time} pagos a tiempo", "Renta alineada con mercado"],
    }


# ═══════════════════════════════════════════════════════════════════════════
@router.get("/proposals")
async def list_proposals(status: Optional[str] = None, db=Depends(get_db), admin=Depends(auth_admin)):
    """List existing proposals + auto-generate for any lease ending soon that doesn't have one yet."""
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=WINDOW_DAYS)

    # Find leases ending soon
    try:
        active = await db.leases.find({"status": {"$in": ["active", "current", None]}}).to_list(500)
    except Exception:
        active = []

    for lease in active:
        end_str = lease.get("end_date") or lease.get("lease_end_date")
        if not end_str:
            continue
        try:
            end_dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00")) if isinstance(end_str, str) else end_str
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not (now <= end_dt <= soon):
            continue

        # Skip if a proposal already exists
        existing = await db.lease_renewal_proposals.find_one({"lease_id": str(lease.get("_id"))})
        if existing:
            continue

        ctx = await _lease_market_context(db, lease)
        rec = await _llm_analyze(lease, ctx)

        prop_doc = {
            "lease_id": str(lease.get("_id")),
            "property_id": str(lease.get("property_id")) if lease.get("property_id") else None,
            "tenant_name": lease.get("tenant_name"),
            "tenant_email": lease.get("tenant_email"),
            "tenant_phone": lease.get("tenant_phone"),
            "property_address": lease.get("property_address") or (ctx.get("property") or {}).get("address"),
            "current_rent": float(lease.get("monthly_rent") or (ctx.get("property") or {}).get("current_rent") or 0),
            "lease_end_date": end_dt.isoformat(),
            "days_until_end": (end_dt - now).days,
            "recommendation": rec.get("recommendation", "renew"),
            "proposed_rent": float(rec.get("proposed_rent") or 0),
            "confidence": rec.get("confidence", "med"),
            "rationale": rec.get("rationale", ""),
            "highlights": rec.get("highlights", []),
            "market_signals": ctx,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db.lease_renewal_proposals.insert_one(prop_doc)
        except Exception as e:
            logger.warning(f"[lease-renewals] insert fail: {e}")

    # Return list
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    docs = await db.lease_renewal_proposals.find(q).sort("days_until_end", 1).to_list(200)
    return {"proposals": [_serialize(d) for d in docs], "total": len(docs)}


@router.post("/refresh/{proposal_id}")
async def refresh_proposal(proposal_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    doc = await db.lease_renewal_proposals.find_one({"_id": ObjectId(proposal_id)})
    if not doc:
        raise HTTPException(404, "Not found")
    lease = await db.leases.find_one({"_id": ObjectId(doc["lease_id"])})
    if not lease:
        raise HTTPException(404, "Lease not found")
    ctx = await _lease_market_context(db, lease)
    rec = await _llm_analyze(lease, ctx)
    await db.lease_renewal_proposals.update_one(
        {"_id": ObjectId(proposal_id)},
        {"$set": {
            "recommendation": rec.get("recommendation"),
            "proposed_rent": float(rec.get("proposed_rent") or 0),
            "confidence": rec.get("confidence"),
            "rationale": rec.get("rationale"),
            "highlights": rec.get("highlights", []),
            "market_signals": ctx,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    updated = await db.lease_renewal_proposals.find_one({"_id": ObjectId(proposal_id)})
    return _serialize(updated)


@router.patch("/{proposal_id}")
async def edit_proposal(proposal_id: str, body: Dict[str, Any] = Body(...), db=Depends(get_db), admin=Depends(auth_admin)):
    updates: Dict[str, Any] = {}
    for k in ("proposed_rent", "recommendation", "rationale", "status"):
        if k in body:
            updates[k] = body[k]
    if not updates:
        raise HTTPException(400, "Nothing to update")
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.lease_renewal_proposals.update_one({"_id": ObjectId(proposal_id)}, {"$set": updates})
    d = await db.lease_renewal_proposals.find_one({"_id": ObjectId(proposal_id)})
    return _serialize(d)


@router.post("/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    """Approve + mark for tenant notification (email/SMS is queued in future 3c or handled here in future)."""
    doc = await db.lease_renewal_proposals.find_one({"_id": ObjectId(proposal_id)})
    if not doc:
        raise HTTPException(404, "Not found")
    await db.lease_renewal_proposals.update_one(
        {"_id": ObjectId(proposal_id)},
        {"$set": {
            "status": "approved",
            "approved_by": admin.get("email") if isinstance(admin, dict) else str(admin),
            "approved_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {"ok": True}


@router.post("/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, body: Dict[str, Any] = Body(default={}), db=Depends(get_db), admin=Depends(auth_admin)):
    reason = (body or {}).get("reason", "")
    await db.lease_renewal_proposals.update_one(
        {"_id": ObjectId(proposal_id)},
        {"$set": {
            "status": "rejected",
            "reject_reason": reason,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {"ok": True}


async def ensure_indexes(db) -> None:
    try:
        await db.lease_renewal_proposals.create_index("lease_id", unique=True)
        await db.lease_renewal_proposals.create_index("status")
    except Exception as e:
        logger.warning(f"[lease-renewals] index: {e}")
