"""
Universal Utility Bill OCR (Phase 2 base)

Endpoint that accepts a PDF or image of a utility bill (water/gas/municipal/
trash/internet/etc.) and uses GPT-4o Vision via the Emergent LLM Key to
extract a structured JSON payload of the bill data.

Use cases:
 - Dumas City Utilities monthly bill (water + sewer + trash)
 - West Texas Gas Utility bill (natural gas)
 - EyeOnWater PDF export (smart water meter)
 - Any other bill that arrives outside of Green Button

Phase 2 will:
 - Save extracted bills under `non_xcel_utility_bills` collection
 - Allow admin to assign the bill to one or more properties
 - Pro-rate the cost among tenants automatically
 - Feed the consumption charts the same way Xcel data does
"""
import os
import re
import json
import base64
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from .shared import get_db, auth_admin, serialize

logger = logging.getLogger("utility_ocr")
router = APIRouter()


SUPPORTED_BILL_TYPES = [
    "water", "sewer", "trash", "gas", "electricity_other",
    "internet", "phone", "tv", "hoa", "other",
]


class OCRBillResponse(BaseModel):
    success: bool
    provider: Optional[str] = None
    bill_type: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    due_date: Optional[str] = None
    total_amount: Optional[float] = None
    usage_value: Optional[float] = None
    usage_unit: Optional[str] = None
    account_number: Optional[str] = None
    service_address: Optional[str] = None
    raw_text: Optional[str] = None
    confidence: Optional[float] = None
    needs_manual_review: bool = True


@router.post("/admin/utility-ocr/extract")
async def admin_utility_ocr_extract(
    request: Request,
    bill_type_hint: str = Form("auto"),
    file: UploadFile = File(...),
):
    """Extract structured data from an uploaded utility bill (PDF or image).

    Body:
      - file: PDF/PNG/JPG of the bill (multipart upload)
      - bill_type_hint: 'auto' | 'water' | 'gas' | 'trash' | etc.

    Returns: OCRBillResponse with the parsed fields. Caller can then
    confirm/edit before saving.
    """
    await auth_admin(request)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Archivo demasiado grande (>10 MB)")

    # Detect mime
    mime = (file.content_type or "").lower()
    is_pdf = mime == "application/pdf" or (file.filename or "").lower().endswith(".pdf")

    api_key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not api_key:
        return OCRBillResponse(
            success=False,
            raw_text="EMERGENT_LLM_KEY no está configurado en el servidor.",
            needs_manual_review=True,
        )

    # Build LLM extraction prompt
    system_prompt = (
        "Eres un parser experto de facturas de servicios públicos en USA. "
        "Recibes una imagen o PDF de una factura. Extrae los campos clave y "
        "responde ÚNICAMENTE con un JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "provider": "Dumas City Utilities" | "West Texas Gas Utility" | etc.,\n'
        '  "bill_type": "water" | "gas" | "trash" | "sewer" | "electricity_other" | "internet" | "phone" | "tv" | "hoa" | "other",\n'
        '  "period_start": "YYYY-MM-DD",\n'
        '  "period_end": "YYYY-MM-DD",\n'
        '  "due_date": "YYYY-MM-DD",\n'
        '  "total_amount": 87.50,\n'
        '  "usage_value": 4500,\n'
        '  "usage_unit": "gallons" | "therms" | "kwh" | "ccf" | "minutes" | null,\n'
        '  "account_number": "string or null",\n'
        '  "service_address": "string or null",\n'
        '  "confidence": 0.0 to 1.0\n'
        "}\n"
        "Si un campo no se puede determinar, usa null. NO inventes datos."
    )
    if bill_type_hint != "auto" and bill_type_hint in SUPPORTED_BILL_TYPES:
        system_prompt += f"\nPista del usuario: bill_type = {bill_type_hint}."

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage, FileContentWithMimeType  # type: ignore
    except Exception as e:
        logger.warning(f"emergentintegrations missing: {e}")
        return OCRBillResponse(
            success=False,
            raw_text="emergentintegrations no disponible en el servidor.",
            needs_manual_review=True,
        )

    try:
        # Save tempfile to send to LLM as a file attachment
        tmp_path = f"/tmp/bill_{datetime.now().timestamp()}_{file.filename or 'upload'}"
        with open(tmp_path, "wb") as f:
            f.write(content)

        chat = (
            LlmChat(
                api_key=api_key,
                session_id=f"ocr-{datetime.now().timestamp()}",
                system_message=system_prompt,
            )
            .with_model("openai", "gpt-4o")
            .with_max_tokens(1500)
        )
        user_msg = UserMessage(
            text="Extrae los datos de esta factura.",
            file_contents=[FileContentWithMimeType(
                file_path=tmp_path,
                mime_type=mime or ("application/pdf" if is_pdf else "image/jpeg"),
            )],
        )
        raw = (await chat.send_message(user_msg) or "").strip()

        # Clean potential fences
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        parsed = json.loads(raw)

        # Cleanup tempfile
        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return OCRBillResponse(
            success=True,
            provider=parsed.get("provider"),
            bill_type=parsed.get("bill_type"),
            period_start=parsed.get("period_start"),
            period_end=parsed.get("period_end"),
            due_date=parsed.get("due_date"),
            total_amount=parsed.get("total_amount"),
            usage_value=parsed.get("usage_value"),
            usage_unit=parsed.get("usage_unit"),
            account_number=parsed.get("account_number"),
            service_address=parsed.get("service_address"),
            confidence=parsed.get("confidence"),
            needs_manual_review=(parsed.get("confidence") or 0) < 0.85,
        )
    except json.JSONDecodeError as e:
        logger.exception(f"OCR JSON parse failed: {e}")
        return OCRBillResponse(
            success=False, raw_text=raw[:600] if raw else None,
            needs_manual_review=True,
        )
    except Exception as e:
        logger.exception(f"OCR call failed: {e}")
        return OCRBillResponse(
            success=False, raw_text=str(e)[:300],
            needs_manual_review=True,
        )


@router.post("/admin/utility-ocr/save-bill")
async def admin_utility_ocr_save_bill(request: Request):
    """Persist an extracted bill into `non_xcel_utility_bills` AND optionally
    create a payable `tenant_utility_bills` entry so the tenant sees it as a
    pending invoice in their mobile app.

    Body fields:
      - property_id or property_ids: required
      - all OCR fields (provider, bill_type, period_*, total_amount, etc.)
      - create_tenant_bill: bool (default True) — also makes it payable
      - tenant_id: optional override; otherwise resolved from active contract
    """
    await auth_admin(request)
    body = await request.json()

    property_ids = body.get("property_ids") or ([body.get("property_id")] if body.get("property_id") else [])
    if not property_ids:
        raise HTTPException(status_code=400, detail="Se requiere property_id o property_ids")

    db = get_db()
    now = datetime.now(timezone.utc)

    bill_doc = {
        "property_ids": [str(p) for p in property_ids],
        "provider": body.get("provider"),
        "bill_type": body.get("bill_type", "other"),
        "period_start": body.get("period_start"),
        "period_end": body.get("period_end"),
        "due_date": body.get("due_date"),
        "total_amount": float(body.get("total_amount") or 0),
        "usage_value": body.get("usage_value"),
        "usage_unit": body.get("usage_unit"),
        "account_number": body.get("account_number"),
        "service_address": body.get("service_address"),
        "notes": body.get("notes", ""),
        "source": "ocr",
        "confidence": body.get("confidence"),
        "created_at": now,
        "created_by": "admin",
    }
    result = await db.non_xcel_utility_bills.insert_one(bill_doc)
    bill_doc["_id"] = str(result.inserted_id)

    # ─── Also create payable tenant_utility_bills (Phase 2 bridge) ───
    create_tenant_bill = body.get("create_tenant_bill", True)
    created_tenant_bills = []
    if create_tenant_bill and bill_doc["total_amount"] > 0:
        # Map our bill_type → tenant_utility_bills 'type'
        type_map = {
            "water": "water", "sewer": "water", "trash": "trash",
            "gas": "gas", "electricity_other": "electricity",
            "internet": "internet", "phone": "phone", "tv": "tv",
            "hoa": "hoa", "other": "other",
        }
        tenant_type = type_map.get(bill_doc["bill_type"], "other")

        # Period: prefer period_end's YYYY-MM, fallback to current period
        period_iso = ""
        for ps in (bill_doc.get("period_end"), bill_doc.get("period_start"), bill_doc.get("due_date")):
            if ps and len(ps) >= 7:
                period_iso = ps[:7]
                break
        if not period_iso:
            period_iso = now.strftime("%Y-%m")

        # Split amount across properties if multiple (rare; usually one)
        per_prop_amount = round(bill_doc["total_amount"] / max(len(property_ids), 1), 2)

        for pid in property_ids:
            tenant_id = body.get("tenant_id")
            if not tenant_id:
                # Find active tenant for this property
                contract = await db.rental_contracts.find_one({
                    "property_id": str(pid),
                    "status": {"$in": ["active", "activo"]},
                })
                if contract:
                    tenant_id = str(contract.get("tenant_id", ""))

            t_doc = {
                "property_id": str(pid),
                "tenant_id": tenant_id or "",
                "type": tenant_type,
                "period": period_iso,
                "amount": per_prop_amount,
                "status": "pending",
                "paid": False,
                "source": "ocr",
                "ocr_bill_id": str(result.inserted_id),
                "provider": bill_doc.get("provider"),
                "due_date": bill_doc.get("due_date"),
                "usage_value": bill_doc.get("usage_value"),
                "usage_unit": bill_doc.get("usage_unit"),
                "notes": body.get("notes", ""),
                "created_at": now,
                "updated_at": now,
            }
            tb_res = await db.tenant_utility_bills.insert_one(t_doc)
            t_doc["_id"] = str(tb_res.inserted_id)
            created_tenant_bills.append(t_doc)

    return {
        "success": True,
        "bill": serialize(bill_doc),
        "tenant_bills_created": len(created_tenant_bills),
        "tenant_bill_ids": [b["_id"] for b in created_tenant_bills],
    }


@router.get("/admin/utility-ocr/non-xcel-bills")
async def admin_list_non_xcel_bills(request: Request, property_id: Optional[str] = None):
    """List all extracted non-Xcel bills (water, gas, trash, etc.)."""
    await auth_admin(request)
    db = get_db()
    q: dict = {}
    if property_id:
        q["property_ids"] = property_id
    bills = []
    async for b in db.non_xcel_utility_bills.find(q).sort("period_end", -1).limit(200):
        bills.append(serialize(b))
    return {"bills": bills, "total": len(bills)}
