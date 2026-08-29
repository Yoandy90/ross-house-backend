"""Publicación de anuncios (Fase 5).

- GET /public/listings-feed.xml — feed XML estilo hotPadsItems (Hotpads/Zumper/partners).
  Incluye propiedades 'available' y unidades disponibles de multi-unidad, con fotos públicas.
- POST /admin/listings/{property_id}/ad-copy — genera anuncio con AI (Claude) en ES+EN:
  título, descripción, bullets y post social. Se cachea en properties.ad_copy.
- GET /admin/listings/publish-info — resumen para la página Publicar.

Nota: Zillow no acepta feeds públicos de landlords pequeños — para Zillow se usa el
texto generado (copiar/pegar en Zillow Rental Manager).
"""
import json
import logging
from datetime import datetime
from xml.sax.saxutils import escape

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response

from rental.shared import get_db, auth_admin

router = APIRouter()
logger = logging.getLogger(__name__)

SITE = "https://www.rosshouserentals.com"
CONTACT = {"name": "Ross House Rentals", "email": "info@rosshouserentals.com",
           "phone": "8069342018"}
_ACTIVE_PROPERTY_FILTER = {
    "$or": [
        {"archived_at": {"$exists": False}},
        {"archived_at": None},
    ]
}


async def _photo_urls(property_id: str) -> list:
    db = get_db()
    photos = await db.property_photos.find(
        {"property_id": property_id, "is_deleted": {"$ne": True}}
    ).sort("uploaded_at", -1).to_list(20)
    urls = []
    for p in photos:
        sp = p.get("storage_path", "")
        if sp.startswith("ross-rentals/"):
            sp = sp[len("ross-rentals/"):]
        if sp:
            urls.append(f"{SITE}/api/public/property-file/{sp}")
    return urls


async def _available_listings() -> list:
    """Propiedades operativas disponibles + unidades libres de multi-unidad."""
    db = get_db()
    out = []
    async for prop in db.properties.find(_ACTIVE_PROPERTY_FILTER):
        pid = str(prop["_id"])
        photos = await _photo_urls(pid)
        base = {
            "property_id": pid,
            "address": prop.get("address", ""),
            "city": prop.get("city", "Dumas"),
            "state": prop.get("state", "TX"),
            "zip": prop.get("zip_code", "") or "79029",
            "type": prop.get("type", "house"),
            "description": prop.get("description", ""),
            "features": prop.get("features", []),
            "photos": photos,
        }
        if prop.get("is_multi_unit"):
            async for u in db.property_units.find(
                    {"property_id": pid, "status": "available"}):
                out.append({**base,
                            "listing_id": f"{pid}-{u['_id']}",
                            "unit_id": str(u["_id"]),
                            "unit_name": u.get("unit_name", ""),
                            "name": f"{prop.get('name', base['address'])} — {u.get('unit_name')}",
                            "bedrooms": u.get("bedrooms", 0),
                            "bathrooms": u.get("bathrooms", 0),
                            "square_feet": u.get("square_feet", 0) or prop.get("square_feet", 0),
                            "rent": float(u.get("rent_amount") or 0),
                            "deposit": float(u.get("deposit_amount") or 0)})
        elif prop.get("status") == "available":
            out.append({**base,
                        "listing_id": pid, "unit_id": None, "unit_name": "",
                        "name": prop.get("name", base["address"]),
                        "bedrooms": prop.get("bedrooms", 0),
                        "bathrooms": prop.get("bathrooms", 0),
                        "square_feet": prop.get("square_feet", 0),
                        "rent": float(prop.get("rent_amount") or 0),
                        "deposit": float(prop.get("deposit_amount") or 0)})
    return out


@router.get('/public/listings-feed.xml')
async def listings_feed():
    """Feed XML (esquema estilo hotPadsItems) para Hotpads/Zumper/partners."""
    listings = await _available_listings()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    for l in listings:
        photos_xml = "".join(
            f'<ListingPhoto source="{escape(u)}" mediumImageUrl="{escape(u)}" '
            f'thumbImageUrl="{escape(u)}"/>' for u in l["photos"])
        desc = escape((l["description"] or "")[:2000])
        feats = "".join(f"<Amenity>{escape(str(f))}</Amenity>" for f in l["features"][:15])
        items.append(f"""
  <Listing id="{escape(l['listing_id'])}" type="RENTAL" companyName="Ross House Rentals">
    <Name>{escape(l['name'])}</Name>
    <UnitNumber>{escape(l['unit_name'])}</UnitNumber>
    <Street hide="false">{escape(l['address'])}</Street>
    <City>{escape(l['city'])}</City>
    <State>{escape(l['state'])}</State>
    <Zip>{escape(l['zip'])}</Zip>
    <PropertyType>{escape(l['type'])}</PropertyType>
    <Price>{l['rent']:.0f}</Price>
    <Deposit>{l['deposit']:.0f}</Deposit>
    <NumBedrooms>{l['bedrooms']}</NumBedrooms>
    <NumFullBaths>{l['bathrooms']}</NumFullBaths>
    <SquareFeet>{l['square_feet'] or ''}</SquareFeet>
    <Description>{desc}</Description>
    <Amenities>{feats}</Amenities>
    <ListingPhotos>{photos_xml}</ListingPhotos>
    <ContactName>{CONTACT['name']}</ContactName>
    <ContactEmail>{CONTACT['email']}</ContactEmail>
    <ContactPhone>{CONTACT['phone']}</ContactPhone>
    <ListingUrl>{SITE}</ListingUrl>
    <LastUpdated>{now}</LastUpdated>
  </Listing>""")
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<hotPadsItems version="2.1">{"".join(items)}\n</hotPadsItems>')
    return Response(content=xml, media_type="application/xml")


@router.get('/admin/listings/publish-info')
async def publish_info(request: Request):
    await auth_admin(request)
    db = get_db()
    listings = await _available_listings()
    # anexar ad_copy cacheado
    cache = {}
    async for p in db.properties.find({"ad_copy": {"$exists": True}}, {"ad_copy": 1}):
        cache[str(p["_id"])] = p["ad_copy"]
    for l in listings:
        l["ad_copy"] = cache.get(l["property_id"])
    return {"success": True, "listings": listings,
            "feed_url": f"{SITE}/api/public/listings-feed.xml"}


AD_PROMPT = """Eres un copywriter experto en anuncios de renta de casas en Texas.
Con los datos de la propiedad genera un anuncio atractivo y honesto (no inventes
amenidades que no estén en los datos). Responde SOLO con JSON válido:
{"es": {"title": "...máx 70 chars...", "description": "...120-180 palabras...",
"bullets": ["...", "...", "..."], "social": "...post corto para Facebook Marketplace
con emojis, máx 400 chars, incluye precio y teléfono (806) 934-2018..."},
"en": {"title": "...", "description": "...", "bullets": ["..."], "social": "..."}}"""


@router.post('/admin/listings/{property_id}/ad-copy')
async def generate_ad_copy(property_id: str, request: Request):
    """Genera (o regenera) el anuncio AI para una propiedad. Body: {unit_id?}"""
    await auth_admin(request)
    db = get_db()
    if not ObjectId.is_valid(property_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    if prop.get("archived_at"):
        raise HTTPException(status_code=409, detail="property_archived")

    import os
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="EMERGENT_LLM_KEY no configurada")

    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    unit = None
    unit_id = data.get("unit_id")
    if unit_id:
        if not ObjectId.is_valid(str(unit_id)):
            raise HTTPException(status_code=400, detail="unit_id_invalid")
        unit = await db.property_units.find_one({"_id": ObjectId(str(unit_id))})
        if not unit:
            raise HTTPException(status_code=404, detail="unit_not_found")
        if str(unit.get("property_id") or "") != property_id:
            raise HTTPException(status_code=409, detail="unit_property_mismatch")

    src = {
        "direccion": prop.get("address"), "ciudad": prop.get("city", "Dumas") + ", TX",
        "tipo": prop.get("type"), "habitaciones": (unit or prop).get("bedrooms"),
        "banos": (unit or prop).get("bathrooms"),
        "pies_cuadrados": (unit or prop).get("square_feet"),
        "renta_mensual": (unit or prop).get("rent_amount"),
        "deposito": (unit or prop).get("deposit_amount"),
        "unidad": unit.get("unit_name") if unit else None,
        "caracteristicas": prop.get("features", []),
        "descripcion_existente": (prop.get("description") or "")[:600],
        "section8": prop.get("section8_accepted", False),
    }
    from uuid import uuid4
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    from rental.ai_brain_router import MODEL_PROVIDER, MODEL_NAME
    chat = LlmChat(api_key=api_key, session_id=f"adcopy_{uuid4()}",
                   system_message=AD_PROMPT).with_model(MODEL_PROVIDER, MODEL_NAME)
    raw = str(await chat.send_message(
        UserMessage(text=json.dumps(src, ensure_ascii=False, default=str))))
    # extraer JSON
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        ad = json.loads(raw[start:end])
        assert "es" in ad and "en" in ad
    except Exception:
        logger.error(f"[publicar] respuesta AI no parseable: {raw[:300]}")
        raise HTTPException(status_code=502, detail="La AI no devolvió un anuncio válido — reintenta")

    ad["generated_at"] = datetime.utcnow().isoformat()
    ad["unit_id"] = str(unit["_id"]) if unit else None
    result = await db.properties.update_one(
        {"_id": prop["_id"], "$or": [{"archived_at": {"$exists": False}}, {"archived_at": None}]},
        {"$set": {"ad_copy": ad}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="property_archived")
    return {"success": True, "ad_copy": ad}
