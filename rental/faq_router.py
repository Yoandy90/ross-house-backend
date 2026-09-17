"""
FAQ Router — Dynamic Bilingual FAQ System
==========================================
Admin manages FAQs with ES/EN content.
Mobile app fetches based on user's language preference.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
import logging

from rental.shared import auth_admin

logger = logging.getLogger("faq")

router = APIRouter(prefix="/admin/faqs", tags=["FAQ Management"])
public_router = APIRouter(prefix="/public/faqs", tags=["Public FAQs"])


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENDPOINTS (Mobile App)
# ═══════════════════════════════════════════════════════════════════════════════

@public_router.get("")
async def get_public_faqs(lang: str = Query(default="es", description="Language: es or en")):
    """Get all active FAQs in the specified language."""
    from rental.shared import get_db
    db = get_db()
    lang = "en" if lang.lower().startswith("en") else "es"

    cursor = db.faqs.find({"active": True}).sort("order", 1)
    faqs = []
    async for doc in cursor:
        q_field = f"question_{lang}" if f"question_{lang}" in doc else "question_es"
        a_field = f"answer_{lang}" if f"answer_{lang}" in doc else "answer_es"
        faqs.append({
            "id": str(doc["_id"]),
            "question": doc.get(q_field) or doc.get("question_es", ""),
            "answer": doc.get(a_field) or doc.get("answer_es", ""),
            "category": doc.get("category", "general"),
        })

    return {"status": "success", "faqs": faqs, "total": len(faqs), "lang": lang}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS (CRUD)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("")
async def list_faqs(request: Request):
    await auth_admin(request)  # P1B-4: era público por error
    """List all FAQs for admin management."""
    from rental.shared import get_db
    db = get_db()

    cursor = db.faqs.find().sort("order", 1)
    faqs = []
    async for doc in cursor:
        faqs.append({
            "id": str(doc["_id"]),
            "question_es": doc.get("question_es", ""),
            "question_en": doc.get("question_en", ""),
            "answer_es": doc.get("answer_es", ""),
            "answer_en": doc.get("answer_en", ""),
            "category": doc.get("category", "general"),
            "order": doc.get("order", 0),
            "active": doc.get("active", True),
            "created_at": doc.get("created_at", ""),
            "updated_at": doc.get("updated_at", ""),
        })

    return {"status": "success", "faqs": faqs, "total": len(faqs)}


@router.post("")
async def create_faq(request: Request):
    await auth_admin(request)
    """Create a new FAQ entry."""
    from rental.shared import get_db
    db = get_db()
    body = await request.json()

    if not body.get("question_es") or not body.get("answer_es"):
        raise HTTPException(status_code=400, detail="question_es and answer_es are required")

    # Auto-assign order
    last_faq = await db.faqs.find_one(sort=[("order", -1)])
    next_order = (last_faq.get("order", 0) + 1) if last_faq else 1

    faq = {
        "question_es": body["question_es"],
        "question_en": body.get("question_en", ""),
        "answer_es": body["answer_es"],
        "answer_en": body.get("answer_en", ""),
        "category": body.get("category", "general"),
        "order": body.get("order", next_order),
        "active": body.get("active", True),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    result = await db.faqs.insert_one(faq)
    faq["id"] = str(result.inserted_id)
    faq.pop("_id", None)
    logger.info(f"✅ FAQ created: {faq['question_es'][:50]}")

    return {"status": "success", "faq": faq}


@router.put("/{faq_id}")
async def update_faq(faq_id: str, request: Request):
    await auth_admin(request)
    """Update an existing FAQ."""
    from rental.shared import get_db
    db = get_db()
    body = await request.json()

    update_fields = {"updated_at": datetime.now(timezone.utc)}
    for field in ["question_es", "question_en", "answer_es", "answer_en", "category", "order", "active"]:
        if field in body:
            update_fields[field] = body[field]

    result = await db.faqs.update_one(
        {"_id": ObjectId(faq_id)},
        {"$set": update_fields},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="FAQ not found")

    logger.info(f"✅ FAQ updated: {faq_id}")
    return {"status": "success", "modified": result.modified_count}


@router.delete("/{faq_id}")
async def delete_faq(faq_id: str, request: Request):
    await auth_admin(request)
    """Delete a FAQ."""
    from rental.shared import get_db
    db = get_db()

    result = await db.faqs.delete_one({"_id": ObjectId(faq_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="FAQ not found")

    logger.info(f"🗑️ FAQ deleted: {faq_id}")
    return {"status": "success", "deleted": True}


@router.post("/seed")
async def seed_default_faqs(request: Request):
    await auth_admin(request)
    """Seed default FAQs if collection is empty."""
    from rental.shared import get_db
    db = get_db()

    count = await db.faqs.count_documents({})
    if count > 0:
        return {"status": "success", "message": f"FAQs already exist ({count})", "seeded": 0}

    import json
    from pathlib import Path
    defaults = json.loads((Path(__file__).parent / 'content' / 'default_faqs.json').read_text())

    for faq in defaults:
        faq["active"] = True
        faq["created_at"] = datetime.now(timezone.utc)
        faq["updated_at"] = datetime.now(timezone.utc)

    await db.faqs.insert_many(defaults)
    return {"status": "success", "message": f"Seeded {len(defaults)} default FAQs", "seeded": len(defaults)}
