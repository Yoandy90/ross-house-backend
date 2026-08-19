"""Zelle — Pago de renta semi-manual con comprobante + validación AI.

Flujo:
1. GET  /tenant/zelle-info      → datos para pagar (email, QR, monto, referencia única)
2. POST /tenant/zelle-submit    → sube captura del comprobante; AI (gpt-4o) la valida
3. Admin revisa la cola:
   GET  /admin/zelle-payments            → lista
   GET  /admin/zelle-payments/{id}       → detalle con captura
   POST /admin/zelle-payments/{id}/confirm → marca renta pagada + recibo
   POST /admin/zelle-payments/{id}/reject  → rechaza con motivo
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from .shared import get_db, auth_admin, auth_tenant_flex

logger = logging.getLogger("zelle")
router = APIRouter(tags=["zelle"])

MONTHS_ES = {"january": "enero", "february": "febrero", "march": "marzo", "april": "abril",
             "may": "mayo", "june": "junio", "july": "julio", "august": "agosto",
             "september": "septiembre", "october": "octubre", "november": "noviembre",
             "december": "diciembre"}


async def _zelle_cfg() -> dict:
    return await get_db().rental_config.find_one({"type": "zelle_config"}) or {}


async def _tenant_contract(tenant: dict):
    ids = {tenant["_id"], str(tenant["_id"])}
    if tenant.get("app_user_id"):
        ids.add(str(tenant["app_user_id"]))
    return await get_db().rental_contracts.find_one({
        "tenant_id": {"$in": list(ids)},
        "status": {"$in": ["active", "activo"]},
    })


def _reference_for(tenant_id: str, now: datetime) -> str:
    return f"RHR-{now.strftime('%b%y').upper()}-{str(tenant_id)[-4:].upper()}"


@router.get("/tenant/zelle-info")
async def tenant_zelle_info(request: Request):
    tenant = await auth_tenant_flex(request)
    cfg = await _zelle_cfg()
    if not cfg.get("email"):
        raise HTTPException(status_code=404, detail="Zelle no está configurado")
    contract = await _tenant_contract(tenant)
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")

    now = datetime.now(timezone.utc)
    month = now.strftime('%B').lower()
    # ¿Ya pagó este mes?
    paid = await get_db().rental_payments.find_one({
        "contract_id": str(contract["_id"]),
        "period_month": {"$regex": f"^{month[:3]}", "$options": "i"},
        "period_year": now.year,
        "status": {"$in": ["completed", "paid"]},
    })
    # ¿Hay pago pendiente con monto real (incluye mora)?
    pending = await get_db().rental_payments.find_one(
        {"contract_id": str(contract["_id"]), "status": {"$in": ["pending", "late", "partial"]}},
        sort=[("due_date", 1)])
    rent = float(contract.get("rent_amount") or 0)
    late_fee = float((pending or {}).get("late_fee") or 0)
    total = (float((pending or {}).get("amount") or 0) + late_fee) if pending else rent

    reference = _reference_for(str(tenant["_id"]), now)
    # ¿Ya envió un comprobante en revisión?
    submission = await get_db().zelle_submissions.find_one(
        {"tenant_id": str(tenant["_id"]), "reference": reference,
         "status": {"$in": ["pending_review", "confirmed"]}},
        sort=[("created_at", -1)])
    return {
        "success": True,
        "zelle": {"email": cfg.get("email"), "name": cfg.get("name", "Ross House Rentals LLC"),
                  "qr_base64": cfg.get("qr_base64", "")},
        "amount": total if total > 0 else rent,
        "late_fee": late_fee,
        "reference": reference,
        "period": {"month": MONTHS_ES.get(month, month).title(), "year": now.year},
        "already_paid": bool(paid),
        "submission": ({"status": submission["status"],
                        "submitted_at": submission["created_at"].isoformat()}
                       if submission else None),
    }


async def _ai_validate(screenshot_b64: str, expected_amount: float,
                       expected_email: str, reference: str) -> dict:
    """Valida la captura del comprobante Zelle con visión AI. Nunca lanza excepción."""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
        chat = LlmChat(
            api_key=os.environ["EMERGENT_LLM_KEY"],
            session_id=f"zelle-{datetime.now(timezone.utc).timestamp()}",
            system_message=("Eres un verificador de comprobantes de pago Zelle. Analiza la captura "
                            "y responde SOLO un JSON válido, sin markdown."),
        ).with_model("openai", "gpt-4o")
        prompt = (
            "Analiza esta captura de una confirmación de pago Zelle y responde SOLO este JSON:\n"
            '{"is_zelle_receipt": bool, "amount": number|null, "date": "texto visible o null", '
            '"confirmation_number": "string|null", "recipient": "string|null", '
            '"memo": "string|null", "red_flags": ["señales de edición/captura vieja"], '
            '"summary": "1 línea en español"}\n'
            f"Contexto: se espera un pago de ${expected_amount:.2f} a {expected_email} "
            f"con memo/referencia {reference}."
        )
        msg = UserMessage(text=prompt, file_contents=[ImageContent(image_base64=screenshot_b64)])
        resp = await chat.send_message(msg)
        raw = re.sub(r"^```(json)?|```$", "", str(resp).strip(), flags=re.M).strip()
        data = json.loads(raw)
        amt = data.get("amount")
        amount_ok = amt is not None and abs(float(amt) - expected_amount) < 0.01
        memo_ok = reference.lower() in str(data.get("memo") or "").lower()
        data["amount_matches"] = amount_ok
        data["memo_matches"] = memo_ok
        data["valid"] = bool(data.get("is_zelle_receipt")) and amount_ok and not data.get("red_flags")
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("AI Zelle validation falló: %s", e)
        return {"valid": None, "summary": f"AI no disponible: {e}", "red_flags": []}


@router.post("/tenant/zelle-submit")
async def tenant_zelle_submit(request: Request):
    tenant = await auth_tenant_flex(request)
    data = await request.json()
    screenshot = (data.get("screenshot_base64") or "").split(",")[-1].strip()
    if len(screenshot) < 1000:
        raise HTTPException(status_code=400, detail="Comprobante inválido — sube la captura del pago")
    if len(screenshot) > 4_000_000:
        raise HTTPException(status_code=400, detail="Imagen demasiado grande")

    contract = await _tenant_contract(tenant)
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")
    now = datetime.now(timezone.utc)
    reference = _reference_for(str(tenant["_id"]), now)
    dup = await get_db().zelle_submissions.find_one({
        "tenant_id": str(tenant["_id"]), "reference": reference,
        "status": {"$in": ["pending_review", "confirmed"]}})
    if dup:
        raise HTTPException(status_code=400,
                            detail="Ya tienes un comprobante en revisión para este mes")

    pending = await get_db().rental_payments.find_one(
        {"contract_id": str(contract["_id"]), "status": {"$in": ["pending", "late", "partial"]}},
        sort=[("due_date", 1)])
    late_fee = float((pending or {}).get("late_fee") or 0)
    expected = (float((pending or {}).get("amount") or 0) + late_fee) if pending \
        else float(contract.get("rent_amount") or 0)

    cfg = await _zelle_cfg()
    ai = await _ai_validate(screenshot, expected, cfg.get("email", ""), reference)

    month = now.strftime('%B').lower()
    doc = {
        "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name", ""),
        "tenant_email": tenant.get("email", ""),
        "contract_id": str(contract["_id"]),
        "property_id": str(contract.get("property_id", "")),
        "property_address": contract.get("property_address", ""),
        "amount": expected,
        "late_fee": late_fee,
        "reference": reference,
        "period_month": month,
        "period_year": now.year,
        "screenshot_base64": screenshot,
        "ai": ai,
        "status": "pending_review",
        "created_at": now,
        "updated_at": now,
    }
    r = await get_db().zelle_submissions.insert_one(doc)
    logger.info("💜 Zelle comprobante recibido: %s $%.2f ref=%s ai_valid=%s",
                tenant.get("name"), expected, reference, ai.get("valid"))
    return {"success": True, "submission_id": str(r.inserted_id),
            "ai_valid": ai.get("valid"), "ai_summary": ai.get("summary", "")}


# ────────────────────────────── ADMIN ──────────────────────────────

@router.get("/admin/zelle-payments")
async def admin_list_zelle(request: Request, status: str = ""):
    await auth_admin(request)
    q = {"status": status} if status else {}
    items = []
    async for s in get_db().zelle_submissions.find(q, {"screenshot_base64": 0}) \
            .sort("created_at", -1).limit(100):
        s["_id"] = str(s["_id"])
        items.append(s)
    pending = await get_db().zelle_submissions.count_documents({"status": "pending_review"})
    return {"success": True, "items": items, "pending_count": pending}


@router.get("/admin/zelle-payments/{sub_id}")
async def admin_zelle_detail(sub_id: str, request: Request):
    await auth_admin(request)
    s = await get_db().zelle_submissions.find_one({"_id": ObjectId(sub_id)})
    if not s:
        raise HTTPException(status_code=404, detail="No encontrado")
    s["_id"] = str(s["_id"])
    return {"success": True, "submission": s}


@router.post("/admin/zelle-payments/{sub_id}/confirm")
async def admin_zelle_confirm(sub_id: str, request: Request):
    admin = await auth_admin(request)
    db = get_db()
    s = await db.zelle_submissions.find_one({"_id": ObjectId(sub_id)})
    if not s:
        raise HTTPException(status_code=404, detail="No encontrado")
    if s["status"] == "confirmed":
        return {"success": True, "already": True}

    now = datetime.now(timezone.utc)
    receipt = f"ZEL-{now.strftime('%Y%m%d')}-{s['tenant_id'][-4:]}"
    # Si existe un rental_payment pendiente del período, complétalo; si no, créalo
    pending = await db.rental_payments.find_one({
        "contract_id": s["contract_id"],
        "period_month": s["period_month"], "period_year": s["period_year"],
        "status": {"$in": ["pending", "late", "partial"]}})
    if pending:
        await db.rental_payments.update_one({"_id": pending["_id"]}, {"$set": {
            "status": "completed", "payment_method": "zelle",
            "receipt_number": receipt, "reference_number": s["reference"],
            "total_paid": s["amount"], "payment_date": now.isoformat(), "updated_at": now}})
        payment_id = str(pending["_id"])
    else:
        r = await db.rental_payments.insert_one({
            "contract_id": s["contract_id"], "property_id": s.get("property_id", ""),
            "tenant_id": s["tenant_id"], "tenant_name": s.get("tenant_name", ""),
            "amount": s["amount"] - s.get("late_fee", 0), "late_fee": s.get("late_fee", 0),
            "total_paid": s["amount"], "payment_method": "zelle",
            "reference_number": s["reference"], "receipt_number": receipt,
            "period_month": s["period_month"], "period_year": s["period_year"],
            "status": "completed", "payment_date": now.isoformat(),
            "submitted_by": "tenant_zelle", "submitted_at": s["created_at"],
            "created_at": now, "updated_at": now})
        payment_id = str(r.inserted_id)

    await db.zelle_submissions.update_one({"_id": s["_id"]}, {"$set": {
        "status": "confirmed", "confirmed_by": admin.get("email", "admin"),
        "confirmed_at": now, "receipt_number": receipt,
        "rental_payment_id": payment_id, "updated_at": now}})
    logger.info("✅ Zelle confirmado: %s — %s $%.2f", s.get("tenant_name"), receipt, s["amount"])
    return {"success": True, "receipt_number": receipt, "payment_id": payment_id}


@router.post("/admin/zelle-payments/{sub_id}/reject")
async def admin_zelle_reject(sub_id: str, request: Request):
    admin = await auth_admin(request)
    data = await request.json()
    now = datetime.now(timezone.utc)
    r = await get_db().zelle_submissions.update_one(
        {"_id": ObjectId(sub_id), "status": "pending_review"},
        {"$set": {"status": "rejected", "reject_reason": data.get("reason", ""),
                  "rejected_by": admin.get("email", "admin"), "updated_at": now}})
    if not r.modified_count:
        raise HTTPException(status_code=400, detail="No se pudo rechazar (¿ya procesado?)")
    return {"success": True}
