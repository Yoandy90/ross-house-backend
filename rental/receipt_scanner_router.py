"""AI Receipt Scanner — foto de recibo → extracción de datos + clasificación (GPT visión).

POST /admin/property-expenses/scan-receipt   (multipart: file)
GET  /admin/property-expenses/receipt/{receipt_id}
"""
import base64
import io
import json
import logging
import os
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import Response

from rental.shared import get_db, auth_admin
from rental.finances_router import EXPENSE_CATEGORIES

router = APIRouter()

# Categorías fiscales IRS Schedule E (propiedades de renta)
IRS_SCHEDULE_E = {
    'advertising': 'Advertising (Línea 5)',
    'auto_travel': 'Auto and Travel (Línea 6)',
    'cleaning_maintenance': 'Cleaning and Maintenance (Línea 7)',
    'commissions': 'Commissions (Línea 8)',
    'insurance': 'Insurance (Línea 9)',
    'legal_professional': 'Legal and Professional Fees (Línea 10)',
    'management_fees': 'Management Fees (Línea 11)',
    'mortgage_interest': 'Mortgage Interest (Línea 12)',
    'other_interest': 'Other Interest (Línea 13)',
    'repairs': 'Repairs (Línea 14)',
    'supplies': 'Supplies (Línea 15)',
    'taxes': 'Taxes (Línea 16)',
    'utilities': 'Utilities (Línea 17)',
    'other': 'Other (Línea 19)',
}


def _compress_image(raw: bytes, max_px: int = 1600) -> bytes:
    """Resize/compress to JPEG to keep the vision payload small."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img.thumbnail((max_px, max_px))
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=82)
    return out.getvalue()


@router.post('/admin/property-expenses/scan-receipt')
async def scan_receipt(request: Request, file: UploadFile = File(...)):
    """Analiza la foto de un recibo con AI y devuelve los datos extraídos + clasificación."""
    await auth_admin(request)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagen demasiado grande (máx 15MB)")

    try:
        jpeg = _compress_image(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="No se pudo leer la imagen. Usa JPG/PNG.")

    img_b64 = base64.b64encode(jpeg).decode()

    # Propiedades para que la AI sugiera a cuál pertenece el gasto
    props = []
    async for p in get_db().properties.find({}, {"name": 1, "address": 1}):
        props.append({"id": str(p["_id"]), "name": p.get("name", ""), "address": p.get("address", "")})
    props_txt = "\n".join(f"- id={p['id']} | {p['name']} | {p['address']}" for p in props) or "(ninguna)"

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="EMERGENT_LLM_KEY no configurada")

    system_msg = (
        "Eres un asistente contable experto en propiedades de renta. Extraes datos de recibos "
        "(fotos) y los clasificas. Respondes ÚNICAMENTE con un objeto JSON válido, sin markdown."
    )
    prompt = f"""Analiza esta foto de un recibo/factura y extrae los datos. Devuelve SOLO este JSON:
{{
  "vendor": "nombre del negocio/proveedor",
  "expense_date": "YYYY-MM-DD (fecha del recibo; si no se ve, usa null)",
  "amount": total final pagado como número (con impuestos),
  "tax_amount": impuesto como número o null,
  "description": "descripción corta en español de lo comprado (máx 10 palabras)",
  "category": "una de: {', '.join(EXPENSE_CATEGORIES.keys())}",
  "irs_category": "una de: {', '.join(IRS_SCHEDULE_E.keys())}",
  "property_id": "id de la propiedad si el recibo menciona una dirección que coincida, si no null",
  "confidence": número 0-100 de qué tan legible/confiable fue la extracción,
  "items": ["hasta 5 artículos principales del recibo"]
}}

Propiedades del usuario (para property_id):
{props_txt}

Reglas:
- "category" clasifica el TIPO de gasto (ej: Home Depot con materiales → repair o supplies; factura de agua → utilities).
- "irs_category" es la línea del IRS Schedule E para deducción fiscal de rentas.
- Si el texto no es un recibo, devuelve {{"error": "no_receipt"}}."""

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
        chat = LlmChat(
            api_key=api_key,
            session_id=f"receipt-scan-{datetime.utcnow().timestamp()}",
            system_message=system_msg,
        ).with_model("openai", "gpt-5.4")
        resp = await chat.send_message(UserMessage(
            text=prompt,
            file_contents=[ImageContent(image_base64=img_b64)],
        ))
        txt = (resp or "").strip()
        if txt.startswith("```"):
            txt = txt.strip("`").lstrip("json").strip()
        data = json.loads(txt)
    except json.JSONDecodeError:
        logging.error("Receipt scan: invalid JSON from model: %s", txt[:300])
        raise HTTPException(status_code=502, detail="La AI no pudo estructurar el recibo. Intenta con una foto más clara.")
    except Exception as e:
        logging.exception("Receipt scan failed")
        raise HTTPException(status_code=502, detail=f"Error analizando el recibo: {e}")

    if data.get("error") == "no_receipt":
        raise HTTPException(status_code=422, detail="La imagen no parece ser un recibo. Intenta de nuevo.")

    # Normalizar
    category = data.get("category") if data.get("category") in EXPENSE_CATEGORIES else "other"
    irs_category = data.get("irs_category") if data.get("irs_category") in IRS_SCHEDULE_E else "other"
    prop_id = data.get("property_id")
    if prop_id and prop_id not in {p["id"] for p in props}:
        prop_id = None
    try:
        amount = round(float(data.get("amount") or 0), 2)
    except (TypeError, ValueError):
        amount = 0.0

    # Detección de duplicados: mismo monto y fecha (o mismo monto + proveedor)
    duplicate = None
    if amount > 0:
        query = {"amount": amount}
        if data.get("expense_date"):
            query["expense_date"] = data["expense_date"]
        dup = await get_db().property_expenses.find_one(query)
        if dup:
            duplicate = {
                "expense_number": dup.get("expense_number", ""),
                "description": dup.get("description", ""),
                "amount": dup.get("amount", 0),
                "expense_date": dup.get("expense_date", ""),
            }

    # Guardar imagen del recibo
    receipt_doc = {
        "image_b64": img_b64,
        "content_type": "image/jpeg",
        "filename": file.filename or "recibo.jpg",
        "created_at": datetime.utcnow(),
    }
    r = await get_db().expense_receipts.insert_one(receipt_doc)

    return {
        "success": True,
        "receipt_id": str(r.inserted_id),
        "extracted": {
            "vendor": data.get("vendor") or "",
            "expense_date": data.get("expense_date") or datetime.utcnow().strftime('%Y-%m-%d'),
            "amount": amount,
            "tax_amount": data.get("tax_amount"),
            "description": data.get("description") or "",
            "category": category,
            "irs_category": irs_category,
            "property_id": prop_id,
            "confidence": min(100, max(0, int(data.get("confidence") or 0))),
            "items": (data.get("items") or [])[:5],
        },
        "possible_duplicate": duplicate,
        "irs_categories": IRS_SCHEDULE_E,
    }


@router.get('/admin/property-expenses/receipt/{receipt_id}')
async def get_receipt_image(receipt_id: str, request: Request):
    """Devuelve la imagen del recibo adjunto a un gasto."""
    await auth_admin(request)
    try:
        doc = await get_db().expense_receipts.find_one({"_id": ObjectId(receipt_id)})
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Recibo no encontrado")
    return Response(content=base64.b64decode(doc["image_b64"]), media_type=doc.get("content_type", "image/jpeg"))
