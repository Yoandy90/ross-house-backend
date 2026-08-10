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


@router.get('/admin/property-expenses/tax-report')
async def tax_report_pdf(request: Request, year: int = 0):
    """PDF fiscal anual: gastos agrupados por línea IRS Schedule E por propiedad."""
    await auth_admin(request)
    if not year:
        year = datetime.utcnow().year

    # Fallback: mapear categoría interna → línea IRS si el gasto no fue clasificado
    cat_to_irs = {
        'maintenance': 'cleaning_maintenance', 'cleaning': 'cleaning_maintenance',
        'landscaping': 'cleaning_maintenance', 'repair': 'repairs', 'appliance': 'repairs',
        'insurance': 'insurance', 'taxes': 'taxes', 'utilities': 'utilities',
        'legal': 'legal_professional', 'advertising': 'advertising',
        'management': 'management_fees', 'other': 'other',
    }

    expenses = []
    async for e in get_db().property_expenses.find({"expense_date": {"$gte": f"{year}-01-01", "$lte": f"{year}-12-31"}}).sort("expense_date", 1):
        irs = e.get("irs_category") or cat_to_irs.get(e.get("category", "other"), "other")
        expenses.append({
            "property_id": e.get("property_id") or "",
            "irs": irs if irs in IRS_SCHEDULE_E else "other",
            "date": e.get("expense_date", ""),
            "vendor": e.get("vendor", ""),
            "description": e.get("description", ""),
            "amount": float(e.get("amount", 0)),
            "auto": not bool(e.get("irs_category")),
        })

    prop_names = {}
    async for p in get_db().properties.find({}, {"name": 1, "address": 1}):
        prop_names[str(p["_id"])] = p.get("name") or p.get("address") or "Propiedad"

    # ── Construir PDF ──
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=ss['Title'], fontSize=17, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=ss['Normal'], fontSize=9, textColor=colors.HexColor('#666666'), spaceAfter=10)
    h2 = ParagraphStyle('h2', parent=ss['Heading2'], fontSize=12, textColor=colors.HexColor('#C8102E'), spaceBefore=12, spaceAfter=4)
    small = ParagraphStyle('small', parent=ss['Normal'], fontSize=7.5, textColor=colors.HexColor('#888888'))
    cell = ParagraphStyle('cell', parent=ss['Normal'], fontSize=8)

    fm = lambda v: f"${v:,.2f}"
    story = [
        Paragraph(f"Reporte Fiscal {year} — Gastos de Propiedades", h1),
        Paragraph(f"Ross House Rentals · IRS Schedule E (Form 1040) · Generado {datetime.utcnow().strftime('%m/%d/%Y')}", sub),
    ]

    grand_by_irs, grand_total = {}, 0.0

    # Secciones por propiedad
    by_prop = {}
    for e in expenses:
        by_prop.setdefault(e["property_id"], []).append(e)

    for pid in sorted(by_prop.keys(), key=lambda x: prop_names.get(x, 'zz General')):
        pexp = by_prop[pid]
        pname = prop_names.get(pid, "General (sin propiedad)")
        ptotal = sum(e["amount"] for e in pexp)
        story.append(Paragraph(f"{pname} — Total: {fm(ptotal)}", h2))

        by_irs = {}
        for e in pexp:
            by_irs.setdefault(e["irs"], []).append(e)

        rows = [["Línea IRS", "Fecha", "Proveedor", "Descripción", "Monto"]]
        row_styles = []
        r = 1
        for irs_key in IRS_SCHEDULE_E:
            if irs_key not in by_irs:
                continue
            items = by_irs[irs_key]
            subtotal = sum(e["amount"] for e in items)
            for i, e in enumerate(items):
                label = IRS_SCHEDULE_E[irs_key] if i == 0 else ""
                mark = " *" if e["auto"] else ""
                rows.append([Paragraph(f"<b>{label}</b>", cell) if label else "",
                             e["date"], Paragraph((e["vendor"] or "—")[:40], cell),
                             Paragraph((e["description"] or "")[:60] + mark, cell), fm(e["amount"])])
                r += 1
            rows.append(["", "", "", Paragraph(f"<b>Subtotal {IRS_SCHEDULE_E[irs_key]}</b>", cell), Paragraph(f"<b>{fm(subtotal)}</b>", cell)])
            row_styles.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#FDECEC')))
            r += 1
            grand_by_irs[irs_key] = grand_by_irs.get(irs_key, 0) + subtotal
            grand_total += subtotal

        t = Table(rows, colWidths=[1.75 * inch, 0.75 * inch, 1.35 * inch, 2.4 * inch, 0.85 * inch], repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#C8102E')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ] + row_styles))
        story.append(t)

    # Resumen global Schedule E
    story.append(PageBreak())
    story.append(Paragraph(f"Resumen Schedule E {year} — Todas las Propiedades", h2))
    srows = [["Línea IRS Schedule E", "Total"]]
    for irs_key, total in grand_by_irs.items():
        srows.append([IRS_SCHEDULE_E[irs_key], fm(total)])
    srows.append([Paragraph("<b>TOTAL GASTOS DEDUCIBLES</b>", cell), Paragraph(f"<b>{fm(grand_total)}</b>", cell)])
    st = Table(srows, colWidths=[4.5 * inch, 1.5 * inch])
    st.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDDD')),
        ('BACKGROUND', (0, len(srows) - 1), (-1, len(srows) - 1), colors.HexColor('#FDECEC')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(st)
    story.append(Spacer(1, 14))
    story.append(Paragraph("* Clasificación IRS asignada automáticamente según la categoría del gasto (no revisada manualmente). "
                           "Este reporte es un resumen informativo para tu contador; no constituye asesoría fiscal.", small))

    if not expenses:
        story = [Paragraph(f"Reporte Fiscal {year}", h1), Paragraph(f"No hay gastos registrados en {year}.", sub)]

    doc.build(story)
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Reporte_Fiscal_{year}_ScheduleE.pdf"'},
    )


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
