"""
Property Entity Alignment Router (Admin only)

Surfaces and lets the admin manage the alignment between:
  - properties.owner_entity         → who legally owns the property
                                      ('personal' | 'llc' | 'unknown')
  - properties.utility_account_holder → who the utility account is in
                                        ('personal' | 'llc' | 'unknown')
  - properties.utility_account_holder_name → human-readable display name

The mismatch report helps landlords identify which properties have a
title-vs-utility-account mismatch (which weakens corporate veil protection)
and prioritize transfer requests to Xcel / other utilities.

When the Green Button OAuth flow eventually populates the RetailCustomer
name automatically, the same fields are written, so the report stays fresh
without manual upkeep.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .shared import get_db, auth_admin, serialize
from .entity_detection import detect_entity_type, compare_property_vs_account

logger = logging.getLogger("property_entity")
router = APIRouter()


class SetUtilityHolderRequest(BaseModel):
    holder_name: str = Field(..., min_length=1, max_length=200,
                             description="Full name as it appears on the utility bill")
    owner_entity: Optional[str] = Field(
        None,
        description="Optional: explicit override of property owner. "
                    "Values: 'personal' | 'llc'. If omitted, keeps existing value.",
    )


@router.get("/admin/properties/utility-alignment")
async def admin_properties_utility_alignment(
    request: Request,
    risk_level: Optional[str] = None,  # 'none' | 'low' | 'medium' | 'high'
):
    """List all properties with their title vs utility-account alignment.

    Optional filter: ?risk_level=high to only show critical mismatches.
    """
    await auth_admin(request)
    db = get_db()

    items: List[dict] = []
    counts = {"high": 0, "medium": 0, "low": 0, "none": 0, "total": 0}

    async for prop in db.properties.find({}):
        try:
            prop_id = str(prop["_id"])
            owner_entity = (prop.get("owner_entity") or "unknown").lower()
            utility_holder = (prop.get("utility_account_holder") or "unknown").lower()
            utility_holder_name = prop.get("utility_account_holder_name") or ""
            owner_display = prop.get("owner_display_name") or ""

            # If owner_entity hasn't been set explicitly, try to infer from a
            # `legal_owner` field that some sources may have populated.
            if owner_entity == "unknown" and prop.get("legal_owner"):
                owner_entity = detect_entity_type(prop["legal_owner"])
                owner_display = owner_display or prop["legal_owner"]

            comparison = compare_property_vs_account(owner_entity, utility_holder)
            risk = comparison.get("risk_level", "low")
            if risk not in counts:
                risk = "low"

            items.append({
                "id": prop_id,
                "name": prop.get("name") or prop.get("address") or "Sin nombre",
                "address": prop.get("address") or "",
                "city": prop.get("city") or "",
                "state": prop.get("state") or "",
                "status": prop.get("status") or "available",
                "owner_entity": owner_entity,
                "owner_display_name": owner_display,
                "utility_account_holder": utility_holder,
                "utility_account_holder_name": utility_holder_name,
                "comparison": comparison,
            })

            counts[risk] += 1
            counts["total"] += 1
        except Exception as e:
            logger.warning(f"Skipping property {prop.get('_id')}: {e}")
            continue

    if risk_level in ("none", "low", "medium", "high"):
        items = [x for x in items if x["comparison"]["risk_level"] == risk_level]

    # Sort: high risk first, then by name
    risk_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    items.sort(key=lambda x: (risk_order.get(x["comparison"]["risk_level"], 4), x["name"]))

    return {
        "properties": items,
        "summary": counts,
        "legend": {
            "high":   "🔴 LLC owner · personal utility account (perforación de velo)",
            "medium": "🟡 Personal owner · LLC utility account (caso inusual)",
            "low":    "🟢 Falta información (titular no marcado)",
            "none":   "✅ Coherente (sin acción)",
        },
    }


@router.post("/admin/properties/{property_id}/utility-holder")
async def admin_set_utility_holder(
    property_id: str,
    payload: SetUtilityHolderRequest,
    request: Request,
):
    """Manually set the utility-account holder name on a property.
    Auto-detects the entity type (personal/llc/unknown) from the name."""
    await auth_admin(request)
    db = get_db()

    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        prop = None
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    detected = detect_entity_type(payload.holder_name)

    update_doc = {
        "utility_account_holder": detected,
        "utility_account_holder_name": payload.holder_name.strip(),
        "utility_account_updated_at": datetime.now(timezone.utc),
    }
    if payload.owner_entity in ("personal", "llc"):
        update_doc["owner_entity"] = payload.owner_entity

    await db.properties.update_one({"_id": prop["_id"]}, {"$set": update_doc})

    refreshed = await db.properties.find_one({"_id": prop["_id"]})
    return {
        "success": True,
        "property_id": property_id,
        "detected_entity": detected,
        "holder_name": payload.holder_name,
        "owner_entity": refreshed.get("owner_entity", "unknown"),
        "comparison": compare_property_vs_account(
            refreshed.get("owner_entity"),
            refreshed.get("utility_account_holder"),
        ),
    }


@router.post("/admin/properties/{property_id}/owner-entity")
async def admin_set_owner_entity(
    property_id: str,
    request: Request,
):
    """Set the legal owner (deed-holder) entity type for a property.
    Body: { "owner_entity": "personal" | "llc", "owner_display_name": "Yoandy Ross" }"""
    await auth_admin(request)
    db = get_db()

    body = await request.json()
    owner_entity = (body.get("owner_entity") or "").lower()
    display = body.get("owner_display_name") or ""
    if owner_entity not in ("personal", "llc"):
        raise HTTPException(
            status_code=400,
            detail="owner_entity debe ser 'personal' o 'llc'",
        )

    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        prop = None
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    update_doc = {
        "owner_entity": owner_entity,
        "owner_entity_updated_at": datetime.now(timezone.utc),
    }
    if display:
        update_doc["owner_display_name"] = display

    await db.properties.update_one({"_id": prop["_id"]}, {"$set": update_doc})
    refreshed = await db.properties.find_one({"_id": prop["_id"]})

    return {
        "success": True,
        "property_id": property_id,
        "owner_entity": owner_entity,
        "owner_display_name": display,
        "comparison": compare_property_vs_account(
            refreshed.get("owner_entity"),
            refreshed.get("utility_account_holder"),
        ),
    }


@router.get("/admin/entity-detection/preview")
async def admin_entity_detection_preview(name: str):
    """Quick utility endpoint: classify any name without saving it.
    Useful for the admin UI to show a real-time preview while typing."""
    # No auth_admin required → this is read-only and harmless
    detected = detect_entity_type(name)
    return {
        "input": name,
        "detected_entity": detected,
        "is_business": detected == "llc",
    }
