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

    cursor = db.faqs.find({"active": True}).sort("order", 1)
    faqs = []
    async for doc in cursor:
        q_field = f"question_{lang}" if f"question_{lang}" in doc else "question_es"
        a_field = f"answer_{lang}" if f"answer_{lang}" in doc else "answer_es"
        faqs.append({
            "id": str(doc["_id"]),
            "question": doc.get(q_field, doc.get("question_es", "")),
            "answer": doc.get(a_field, doc.get("answer_es", "")),
            "category": doc.get("category", "general"),
        })

    return {"status": "success", "faqs": faqs, "total": len(faqs), "lang": lang}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS (CRUD)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("")
async def list_faqs():
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
    logger.info(f"✅ FAQ created: {faq['question_es'][:50]}")

    return {"status": "success", "faq": faq}


@router.put("/{faq_id}")
async def update_faq(faq_id: str, request: Request):
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
async def delete_faq(faq_id: str):
    """Delete a FAQ."""
    from rental.shared import get_db
    db = get_db()

    result = await db.faqs.delete_one({"_id": ObjectId(faq_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="FAQ not found")

    logger.info(f"🗑️ FAQ deleted: {faq_id}")
    return {"status": "success", "deleted": True}


@router.post("/seed")
async def seed_default_faqs():
    """Seed default FAQs if collection is empty."""
    from rental.shared import get_db
    db = get_db()

    count = await db.faqs.count_documents({})
    if count > 0:
        return {"status": "success", "message": f"FAQs already exist ({count})", "seeded": 0}

    defaults = [
        {
            "question_es": "¿Cómo puedo aplicar para rentar una propiedad?",
            "question_en": "How can I apply to rent a property?",
            "answer_es": "Puedes aplicar directamente desde la app. Ve a la sección de Propiedades, selecciona la que te interese y presiona 'Aplicar'. Necesitarás proporcionar identificación, comprobante de ingresos y referencias.",
            "answer_en": "You can apply directly from the app. Go to the Properties section, select the one you're interested in, and press 'Apply'. You'll need to provide ID, proof of income, and references.",
            "category": "rentals",
            "order": 1,
        },
        {
            "question_es": "¿Cuáles son los requisitos para rentar?",
            "question_en": "What are the requirements to rent?",
            "answer_es": "Los requisitos básicos son: identificación válida, comprobante de ingresos (últimos 3 meses), historial crediticio aceptable, y referencias de arrendadores anteriores. El ingreso mensual debe ser al menos 3 veces el monto de la renta.",
            "answer_en": "Basic requirements are: valid ID, proof of income (last 3 months), acceptable credit history, and previous landlord references. Monthly income must be at least 3 times the rent amount.",
            "category": "rentals",
            "order": 2,
        },
        {
            "question_es": "¿Cómo pago mi renta?",
            "question_en": "How do I pay my rent?",
            "answer_es": "Puedes pagar tu renta directamente desde la app en la sección de Pagos. Aceptamos tarjetas de débito/crédito y transferencias bancarias. Los pagos se procesan de forma segura a través de Stripe.",
            "answer_en": "You can pay your rent directly from the app in the Payments section. We accept debit/credit cards and bank transfers. Payments are processed securely through Stripe.",
            "category": "payments",
            "order": 3,
        },
        {
            "question_es": "¿Qué hago si necesito una reparación en mi propiedad?",
            "question_en": "What do I do if I need a repair in my property?",
            "answer_es": "Reporta cualquier problema de mantenimiento desde la app en tu dashboard de Inicio. Incluye fotos y una descripción detallada. Nuestro equipo se pondrá en contacto contigo para programar la reparación.",
            "answer_en": "Report any maintenance issues from the app on your Home dashboard. Include photos and a detailed description. Our team will contact you to schedule the repair.",
            "category": "maintenance",
            "order": 4,
        },
        {
            "question_es": "¿Puedo tener mascotas en las propiedades?",
            "question_en": "Can I have pets in the properties?",
            "answer_es": "La política de mascotas varía según la propiedad. Algunas permiten mascotas con un depósito adicional. Consulta los detalles de cada propiedad o contáctanos para más información.",
            "answer_en": "Pet policy varies by property. Some allow pets with an additional deposit. Check the details of each property or contact us for more information.",
            "category": "general",
            "order": 5,
        },
        {
            "question_es": "¿Cómo puedo contactar al administrador?",
            "question_en": "How can I contact the administrator?",
            "answer_es": "Puedes contactarnos por teléfono al (806) 934-2018, por email a info@rosshouserentals.com, o a través del chat en nuestra página web rosshouserentals.com.",
            "answer_en": "You can contact us by phone at (806) 934-2018, by email at info@rosshouserentals.com, or through the chat on our website rosshouserentals.com.",
            "category": "general",
            "order": 6,
        },
        {
            "question_es": "¿Las propiedades del Mercado están disponibles para comprar?",
            "question_en": "Are the Market properties available for purchase?",
            "answer_es": "Sí, las propiedades en la sección Mercado están en venta. Puedes ver los detalles y mostrar tu interés directamente desde la app. Un asesor te contactará para guiarte en el proceso de compra.",
            "answer_en": "Yes, the properties in the Market section are for sale. You can view details and show your interest directly from the app. An advisor will contact you to guide you through the purchase process.",
            "category": "market",
            "order": 7,
        },
    ]

    for faq in defaults:
        faq["active"] = True
        faq["created_at"] = datetime.now(timezone.utc)
        faq["updated_at"] = datetime.now(timezone.utc)

    await db.faqs.insert_many(defaults)
    return {"status": "success", "message": f"Seeded {len(defaults)} default FAQs", "seeded": len(defaults)}
