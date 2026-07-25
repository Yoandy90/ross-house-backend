"""
Social Poster Router
═══════════════════════════════════════════════════════════════════════════════
Help admin post property listings to multiple Facebook groups efficiently.

Features:
1. Save favorite FB groups (name, URL, category, member count)
2. Track "last posted" per group + days-ago badge
3. Generate 5 AI post variations (Claude Sonnet 4.5 via Emergent LLM Key)
4. Metrics: leads attributed per group (via utm_campaign)
"""
import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from bson import ObjectId

from rental.shared import auth_admin, get_db

logger = logging.getLogger("social_poster")
router = APIRouter(prefix="/admin/marketing/social", tags=["Social Poster"])

MODEL_PROVIDER = "anthropic"
MODEL_NAME = "claude-sonnet-4-5-20250929"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUPS CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class GroupPayload(BaseModel):
    name: str
    url: str
    category: Optional[str] = "general"  # rentals, hispanic, dumas, amarillo, buy-sell, etc.
    member_count: Optional[int] = 0
    notes: Optional[str] = ""


@router.get("/groups")
async def list_groups(request: Request):
    await auth_admin(request)
    db = get_db()
    docs = await db.social_groups.find().sort("last_posted_at", 1).to_list(None)

    now = datetime.now(timezone.utc)
    result = []
    for g in docs:
        last = g.get("last_posted_at")
        if isinstance(last, datetime):
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days_ago = (now - last).days
        else:
            days_ago = None

        result.append({
            "id": str(g.get("_id")),
            "name": g.get("name", ""),
            "url": g.get("url", ""),
            "category": g.get("category", "general"),
            "member_count": g.get("member_count", 0),
            "notes": g.get("notes", ""),
            "last_posted_at": last.isoformat() if isinstance(last, datetime) else None,
            "days_since_last_post": days_ago,
            "total_posts": g.get("total_posts", 0),
            "leads_generated": g.get("leads_generated", 0),
        })

    return {"status": "success", "groups": result}


@router.post("/groups")
async def create_group(payload: GroupPayload, request: Request):
    await auth_admin(request)
    db = get_db()

    if not payload.name.strip() or not payload.url.strip():
        raise HTTPException(status_code=400, detail="Nombre y URL son requeridos")

    doc = {
        "name": payload.name.strip(),
        "url": payload.url.strip(),
        "category": payload.category or "general",
        "member_count": payload.member_count or 0,
        "notes": payload.notes or "",
        "created_at": datetime.now(timezone.utc),
        "last_posted_at": None,
        "total_posts": 0,
        "leads_generated": 0,
    }
    r = await db.social_groups.insert_one(doc)
    return {"status": "success", "id": str(r.inserted_id)}


@router.put("/groups/{group_id}")
async def update_group(group_id: str, payload: GroupPayload, request: Request):
    await auth_admin(request)
    db = get_db()
    try:
        oid = ObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    await db.social_groups.update_one(
        {"_id": oid},
        {"$set": {
            "name": payload.name.strip(),
            "url": payload.url.strip(),
            "category": payload.category or "general",
            "member_count": payload.member_count or 0,
            "notes": payload.notes or "",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {"status": "success"}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    try:
        oid = ObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    await db.social_groups.delete_one({"_id": oid})
    return {"status": "success"}


@router.post("/groups/{group_id}/mark-posted")
async def mark_posted(group_id: str, request: Request):
    """Mark a group as posted right now — updates last_posted_at + increments counter."""
    await auth_admin(request)
    db = get_db()
    try:
        oid = ObjectId(group_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")

    await db.social_groups.update_one(
        {"_id": oid},
        {
            "$set": {"last_posted_at": datetime.now(timezone.utc)},
            "$inc": {"total_posts": 1},
        },
    )
    # Audit log
    await db.social_post_log.insert_one({
        "group_id": group_id,
        "posted_at": datetime.now(timezone.utc),
    })
    return {"status": "success"}


# ═══════════════════════════════════════════════════════════════════════════════
# AI POST GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

class GeneratePayload(BaseModel):
    intent: str  # 'rental_listing', 'available_soon', 'general_promo', 'contractor_recruit'
    property_id: Optional[str] = None  # optional: pull real property details
    custom_context: Optional[str] = ""  # free-form context to feed the AI
    tone: str = "friendly"  # friendly, urgent, professional, informal
    include_hashtags: bool = True
    include_cta: bool = True


@router.post("/generate")
async def generate_variations(payload: GeneratePayload, request: Request):
    """Generate 5 post variations using Claude Sonnet 4.5."""
    await auth_admin(request)
    db = get_db()

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="EMERGENT_LLM_KEY no configurada en el backend")

    # Optionally pull property data
    property_ctx = ""
    if payload.property_id:
        try:
            prop = await db.rental_properties.find_one({"_id": ObjectId(payload.property_id)})
        except Exception:
            prop = None
        if prop:
            property_ctx = (
                f"\nPropiedad específica a promocionar:\n"
                f"- Dirección: {prop.get('address', '')}, {prop.get('city', 'Dumas')}, TX\n"
                f"- Habitaciones: {prop.get('bedrooms', '?')}\n"
                f"- Baños: {prop.get('bathrooms', '?')}\n"
                f"- Renta mensual: ${prop.get('rent_amount', 0):,}\n"
                f"- Depósito: ${prop.get('deposit_amount', 0):,}\n"
                f"- Sqft: {prop.get('sqft', '?')}\n"
                f"- Estado: {prop.get('status', 'disponible')}\n"
            )

    intent_map = {
        "rental_listing": "promover una casa disponible en renta",
        "available_soon": "anunciar que próximamente estará disponible una casa",
        "general_promo": "promover Ross House Rentals como empresa administradora",
        "contractor_recruit": (
            "invitar a contratistas locales (plomeros, electricistas, HVAC, handymen, pintores, "
            "jardineros, cerrajeros, techadores) a REGISTRAR SU CONTACTO en nuestro directorio de "
            "proveedores. IMPORTANTE: NO es una oferta de empleo, NO prometemos trabajo constante, "
            "NO garantizamos pagos rápidos. Es simplemente una base de datos de contactos locales "
            "que consultaremos cuando necesitemos servicios. El enfoque es: 'sin compromiso, solo "
            "un contacto útil para ambos, cuando necesitemos te llamamos'. Debe sonar a comunidad "
            "ayudándose, no a reclutamiento corporativo. Link: rosshouserentals.com/proveedores"
        ),
    }
    intent_desc = intent_map.get(payload.intent, payload.intent)

    tone_map = {
        "friendly": "cercano, cálido, como amigo hispano",
        "urgent": "con sentido de urgencia, tipo 'oportunidad única'",
        "professional": "profesional pero accesible",
        "informal": "muy relajado, con emojis, tipo mensaje de whatsapp",
    }
    tone_desc = tone_map.get(payload.tone, payload.tone)

    system_prompt = (
        "Eres un experto en marketing digital para Ross House Rentals LLC, una empresa "
        "de renta de casas en Dumas, TX (Texas Panhandle). El público objetivo es la "
        "comunidad hispana local que busca casas en renta. "
        "Escribes posts para grupos de Facebook locales de Dumas, Amarillo y ciudades cercanas. "
        "Regla crítica: los posts deben pasar como escritos por una persona real, NO por página comercial. "
        "Debes devolver SOLO un JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "variations": [\n'
        '    {"headline": "...", "body": "...", "cta": "...", "hashtags": ["...", "..."]},\n'
        "    ...5 items en total...\n"
        "  ]\n"
        "}\n"
        "Cada variación debe:\n"
        "- Ser distinta entre sí (diferente ángulo, headline, tono ligeramente distinto)\n"
        "- Máximo 400 caracteres en body (grupos de FB penalizan textos largos)\n"
        "- Sonar humana, no corporativa\n"
        "- Incluir la ciudad (Dumas, TX) para geo-targeting del algoritmo\n"
        "- Mezclar español e inglés naturalmente si el público lo requiere\n"
        "Prohibido usar comillas triples, backticks, o texto fuera del JSON."
    )

    user_prompt = (
        f"Objetivo del post: {intent_desc}\n"
        f"Tono deseado: {tone_desc}\n"
        f"Incluir hashtags: {payload.include_hashtags}\n"
        f"Incluir call-to-action con link: {payload.include_cta}\n"
        f"URL de la empresa (para CTA): https://www.rosshouserentals.com\n"
        f"Teléfono: (806) 934-2018\n"
        f"{property_ctx}\n"
        f"Contexto adicional del admin: {payload.custom_context or '(ninguno)'}\n\n"
        "Genera 5 variaciones y devuelve el JSON."
    )

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=api_key,
            session_id=f"social_gen_{uuid4()}",
            system_message=system_prompt,
        ).with_model(MODEL_PROVIDER, MODEL_NAME)

        raw = await chat.send_message(UserMessage(text=user_prompt))
        text = str(raw or "").strip()

        # Strip code fences if present
        if text.startswith("```"):
            parts = text.split("```", 2)
            text = parts[1] if len(parts) > 1 else parts[0]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip("` \n")

        import json
        parsed = json.loads(text)
        variations = parsed.get("variations", [])
        if not isinstance(variations, list) or not variations:
            raise ValueError("AI returned no variations")

        # Add composed_text field for easy copy-paste
        for v in variations:
            body_txt = v.get("body", "").strip()
            cta = v.get("cta", "").strip()
            tags = v.get("hashtags", [])
            tag_line = " ".join([f"#{t.lstrip('#')}" for t in tags]) if tags else ""
            v["composed_text"] = "\n\n".join(filter(None, [body_txt, cta, tag_line]))
            v["char_count"] = len(v["composed_text"])

        return {
            "status": "success",
            "variations": variations,
            "model": MODEL_NAME,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.exception(f"[social] AI generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Error al generar: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS — leads per group
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/metrics")
async def group_metrics(request: Request, days: int = Query(default=30, ge=1, le=365)):
    """Aggregate: posts made, leads generated per group (via property_leads)."""
    await auth_admin(request)
    db = get_db()

    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Total posts in the period
    total_posts = await db.social_post_log.count_documents({"posted_at": {"$gte": since}})

    # Total leads that came from social sources
    lead_query = {
        "created_at": {"$gte": since},
        "$or": [
            {"source": {"$regex": "facebook|social|fb_group", "$options": "i"}},
            {"utm_source": {"$regex": "facebook|social", "$options": "i"}},
        ],
    }
    total_leads = await db.property_leads.count_documents(lead_query)

    # Groups posted in period, ordered by frequency
    pipeline = [
        {"$match": {"posted_at": {"$gte": since}}},
        {"$group": {"_id": "$group_id", "post_count": {"$sum": 1}, "last_posted": {"$max": "$posted_at"}}},
        {"$sort": {"post_count": -1}},
    ]
    group_stats_raw = await db.social_post_log.aggregate(pipeline).to_list(None)

    # Attach group name from social_groups
    group_stats = []
    for g in group_stats_raw:
        gid = g.get("_id")
        try:
            group_doc = await db.social_groups.find_one({"_id": ObjectId(gid)})
        except Exception:
            group_doc = None
        group_stats.append({
            "group_id": gid,
            "group_name": (group_doc or {}).get("name", "(eliminado)"),
            "post_count": g.get("post_count", 0),
            "last_posted": g.get("last_posted").isoformat() if isinstance(g.get("last_posted"), datetime) else None,
        })

    return {
        "status": "success",
        "period_days": days,
        "total_posts": total_posts,
        "total_leads_from_social": total_leads,
        "conversion_rate_pct": round((total_leads / total_posts * 100), 1) if total_posts > 0 else 0.0,
        "top_groups": group_stats[:20],
    }


@router.get("/available-properties")
async def list_available_properties(request: Request):
    """Quick list of available rental properties for the property_id selector."""
    await auth_admin(request)
    db = get_db()
    props = await db.rental_properties.find(
        {"status": {"$in": ["available", "disponible", "vacant"]}}
    ).to_list(50)
    return {
        "status": "success",
        "properties": [
            {
                "id": str(p.get("_id")),
                "address": p.get("address", ""),
                "city": p.get("city", "Dumas"),
                "bedrooms": p.get("bedrooms", 0),
                "bathrooms": p.get("bathrooms", 0),
                "rent_amount": p.get("rent_amount", 0),
            } for p in props
        ],
    }
