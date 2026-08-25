"""Manual Payment Confirmations — Cash App / Money Order / Bank Transfer.

Mirror del patrón Zelle (zelle_router) con colección propia:
  manual_payment_confirmations
Estados: submitted → under_review → approved | rejected

Reglas:
- Enviar confirmación REQUIERE contrato activo (tenant derivado de sesión).
- Un submission NUNCA marca la renta pagada; solo el APPROVE del admin crea
  el rental_payment canónico (idempotente, anti double-payment).
- Dedupe: tenant+contract+mes+método (+referencia) con estado vivo.
- Recibos: base64 acotado (patrón Zelle), solo visibles para dueño/admin.
- Sin datos bancarios sensibles: solo referencia/número de money order.
"""
import logging
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from .shared import (
    get_db, auth_admin, auth_tenant_flex,
    send_rental_push_to_user, send_rental_push_to_admins,
)

router = APIRouter()
logger = logging.getLogger("manual_confirmations")

METHODS = {"cashapp", "money_order", "bank_transfer"}
METHOD_LABEL = {"cashapp": "Cash App", "money_order": "Money Order",
                "bank_transfer": "Bank Transfer"}
MAX_RECEIPT_B64 = 8_000_000  # ~6 MB imagen (igual que Zelle)
LIVE_STATUSES = ["submitted", "under_review", "approved"]


async def _notify(*, title: str, body: str, title_en: str, body_en: str,
                  ntype: str, data: dict, user_id: str = "", target: str = ""):
    """Notificación interna (rental_notifications) + push best-effort.
    Recipiente derivado SIEMPRE del servidor (nunca del body del tenant).
    Nunca incluye receipt/base64 ni datos bancarios. Nunca rompe el flujo."""
    now = datetime.now(timezone.utc)
    try:
        doc = {"title": title, "body": body, "title_en": title_en, "body_en": body_en,
               "type": ntype, "data": data, "read_by": [], "created_at": now}
        if user_id:
            doc["user_id"] = user_id
        else:
            doc["target"] = target or "admin"
        await get_db().rental_notifications.insert_one(doc)
    except Exception as e:
        logger.warning("notify insert error: %s", e)
    try:
        if user_id:
            await send_rental_push_to_user(user_id, title, body, data)
        else:
            await send_rental_push_to_admins(title, body, data)
    except Exception as e:
        logger.warning("notify push error: %s", e)


async def _active_contract(tenant: dict):
    return await get_db().rental_contracts.find_one({
        "tenant_id": str(tenant["_id"]),
        "status": {"$in": ["active", "activo"]},
    })


def _safe(doc: dict, include_receipt: bool = False) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("_id", "receipt_base64")}
    out["id"] = str(doc["_id"])
    out["has_receipt"] = bool(doc.get("receipt_base64"))
    if include_receipt and doc.get("receipt_base64"):
        out["receipt_base64"] = doc["receipt_base64"]
    for k in ("submitted_at", "created_at", "updated_at", "reviewed_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return out


@router.post("/tenant/manual-payment/confirm")
async def tenant_submit_confirmation(request: Request):
    """Tenant declara 'ya realicé el pago' — crea evidencia PENDIENTE, nunca un pago."""
    tenant = await auth_tenant_flex(request)  # identidad SIEMPRE de la sesión
    data = await request.json()
    method = str(data.get("method", "")).strip()
    if method not in METHODS:
        raise HTTPException(status_code=400, detail="Método inválido")

    contract = await _active_contract(tenant)
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")

    now = datetime.now(timezone.utc)
    period_month = int(data.get("period_month") or now.month)
    period_year = int(data.get("period_year") or now.year)
    try:
        amount = round(float(data.get("amount") or 0), 2)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")

    reference = str(data.get("reference") or "").strip()[:80]
    receipt = (data.get("receipt_base64") or "").split(",")[-1].strip()
    if receipt and len(receipt) > MAX_RECEIPT_B64:
        raise HTTPException(status_code=400, detail="Imagen demasiado grande")
    if receipt and not all(c.isalnum() or c in "+/=\n\r" for c in receipt[:200]):
        raise HTTPException(status_code=400, detail="Comprobante inválido")

    db = get_db()
    # Dedupe: mismo tenant+contrato+mes+método con estado vivo (o misma referencia)
    dup_q = {
        "tenant_id": str(tenant["_id"]), "contract_id": str(contract["_id"]),
        "period_month": period_month, "period_year": period_year,
        "method": method, "status": {"$in": LIVE_STATUSES},
    }
    if await db.manual_payment_confirmations.find_one(dup_q):
        raise HTTPException(status_code=409,
                            detail="Ya existe una confirmación pendiente para ese mes y método")
    if reference and await db.manual_payment_confirmations.find_one(
            {"method": method, "reference": reference, "status": {"$in": LIVE_STATUSES}}):
        raise HTTPException(status_code=409, detail="Esa referencia ya fue enviada")

    doc = {
        "tenant_id": str(tenant["_id"]), "tenant_name": tenant.get("name", ""),
        "contract_id": str(contract["_id"]),
        "property_id": str(contract.get("property_id", "")),
        "property_address": contract.get("property_address", ""),
        "method": method, "amount": amount,
        "period_month": period_month, "period_year": period_year,
        "reference": reference,
        "issuer": str(data.get("issuer") or "").strip()[:80],       # money order
        "paid_date": str(data.get("paid_date") or "").strip()[:20],  # fecha declarada
        "note": str(data.get("note") or "").strip()[:500],
        "receipt_base64": receipt,
        "status": "submitted",
        "submitted_at": now, "created_at": now, "updated_at": now,
    }
    r = await db.manual_payment_confirmations.insert_one(doc)
    logger.info("📩 Confirmación manual %s de %s — $%.2f %s/%s",
                method, tenant.get("name"), amount, period_month, period_year)

    # Notificación interna al admin (sin receipt, sin datos bancarios)
    mm = f"{period_month:02d}/{period_year}"
    summary = (f"{tenant.get('name', '')} · {METHOD_LABEL[method]} · ${amount:,.2f}"
               f" · {contract.get('property_address', '')} · {mm}")
    await _notify(
        target="admin",
        title="Nueva confirmación de pago recibida",
        body=summary,
        title_en="New payment confirmation received",
        body_en=summary,
        ntype="manual_confirmation_new",
        data={"type": "manual_confirmation_new", "confirmation_id": str(r.inserted_id),
              "method": method, "amount": amount,
              "period_month": period_month, "period_year": period_year})
    return {"success": True, "id": str(r.inserted_id), "status": "submitted"}


@router.get("/tenant/manual-payment/confirmations")
async def tenant_list_confirmations(request: Request):
    tenant = await auth_tenant_flex(request)
    cur = get_db().manual_payment_confirmations.find(
        {"tenant_id": str(tenant["_id"])}).sort("created_at", -1).limit(50)
    return {"success": True, "confirmations": [_safe(d) async for d in cur]}


@router.get("/admin/manual-payment/confirmations")
async def admin_list_confirmations(request: Request, status: str = ""):
    await auth_admin(request)
    q = {"status": status} if status else {}
    cur = get_db().manual_payment_confirmations.find(q).sort("created_at", -1).limit(200)
    return {"success": True, "confirmations": [_safe(d) async for d in cur]}


@router.get("/admin/manual-payment/confirmations/{cid}/receipt")
async def admin_get_receipt(cid: str, request: Request):
    await auth_admin(request)  # recibo SOLO para admin (nunca público)
    d = await get_db().manual_payment_confirmations.find_one({"_id": ObjectId(cid)})
    if not d:
        raise HTTPException(status_code=404, detail="No encontrado")
    return {"success": True, "receipt_base64": d.get("receipt_base64", "")}


@router.post("/admin/manual-payment/confirmations/{cid}/approve")
async def admin_approve(cid: str, request: Request):
    """Crea el rental_payment canónico. Idempotente + anti double-payment."""
    admin = await auth_admin(request)
    db = get_db()
    s = await db.manual_payment_confirmations.find_one({"_id": ObjectId(cid)})
    if not s:
        raise HTTPException(status_code=404, detail="No encontrado")
    if s["status"] == "approved":
        return {"success": True, "already": True,
                "payment_id": s.get("rental_payment_id", "")}
    if s["status"] == "rejected":
        raise HTTPException(status_code=400, detail="Confirmación ya rechazada")

    contract = await db.rental_contracts.find_one({"_id": ObjectId(s["contract_id"])})
    if not contract or contract.get("status") not in ("active", "activo"):
        raise HTTPException(status_code=400, detail="El contrato ya no está activo")

    # Anti double-payment: el mes no puede estar ya pagado
    already_paid = await db.rental_payments.find_one({
        "contract_id": s["contract_id"], "period_month": s["period_month"],
        "period_year": s["period_year"], "status": "completed"})
    if already_paid:
        raise HTTPException(status_code=409, detail="Ese mes ya está pagado")

    now = datetime.now(timezone.utc)
    prefix = {"cashapp": "CSH", "money_order": "MOR", "bank_transfer": "TRF"}[s["method"]]
    receipt = f"{prefix}-{now.strftime('%Y%m%d')}-{s['tenant_id'][-4:]}"
    pending = await db.rental_payments.find_one({
        "contract_id": s["contract_id"],
        "period_month": s["period_month"], "period_year": s["period_year"],
        "status": {"$in": ["pending", "late", "partial"]}})
    if pending:
        await db.rental_payments.update_one({"_id": pending["_id"]}, {"$set": {
            "status": "completed", "payment_method": s["method"],
            "receipt_number": receipt, "reference_number": s.get("reference", ""),
            "total_paid": s["amount"], "payment_date": now.isoformat(), "updated_at": now}})
        payment_id = str(pending["_id"])
    else:
        r = await db.rental_payments.insert_one({
            "contract_id": s["contract_id"], "property_id": s.get("property_id", ""),
            "tenant_id": s["tenant_id"], "tenant_name": s.get("tenant_name", ""),
            "amount": s["amount"], "late_fee": 0, "total_paid": s["amount"],
            "payment_method": s["method"], "reference_number": s.get("reference", ""),
            "receipt_number": receipt,
            "period_month": s["period_month"], "period_year": s["period_year"],
            "status": "completed", "payment_date": now.isoformat(),
            "submitted_by": "tenant_manual_confirmation", "submitted_at": s["created_at"],
            "created_at": now, "updated_at": now})
        payment_id = str(r.inserted_id)

    # Idempotencia dura: solo transiciona si sigue sin aprobar (doble click seguro)
    upd = await db.manual_payment_confirmations.update_one(
        {"_id": s["_id"], "status": {"$ne": "approved"}},
        {"$set": {"status": "approved", "reviewed_by": admin.get("email", "admin"),
                  "reviewed_at": now, "receipt_number": receipt,
                  "rental_payment_id": payment_id, "updated_at": now}})
    if not upd.modified_count:
        return {"success": True, "already": True, "payment_id": payment_id}

    from rental.security import audit_log
    await audit_log(admin_user_id=admin.get("_id", admin.get("id", "")),
                    action="manual_payment_approved", resource_type="manual_confirmation",
                    resource_id=cid, request=request,
                    metadata={"amount": s["amount"], "method": s["method"], "receipt": receipt})

    # Notificación interna al tenant — solo en la transición real (doble click = 1 sola)
    mm = f"{s['period_month']:02d}/{s['period_year']}"
    await _notify(
        user_id=s["tenant_id"],
        title="Tu confirmación de pago fue aprobada.",
        body=f"{METHOD_LABEL[s['method']]} · ${s['amount']:,.2f} · {mm} · Recibo {receipt}",
        title_en="Your payment confirmation was approved.",
        body_en=f"{METHOD_LABEL[s['method']]} · ${s['amount']:,.2f} · {mm} · Receipt {receipt}",
        ntype="manual_confirmation_approved",
        data={"type": "manual_confirmation_approved", "confirmation_id": cid,
              "receipt_number": receipt})
    return {"success": True, "receipt_number": receipt, "payment_id": payment_id}


@router.post("/admin/manual-payment/confirmations/{cid}/reject")
async def admin_reject(cid: str, request: Request):
    admin = await auth_admin(request)
    data = await request.json()
    now = datetime.now(timezone.utc)
    db = get_db()
    s = await db.manual_payment_confirmations.find_one({"_id": ObjectId(cid)})
    if not s:
        raise HTTPException(status_code=404, detail="No encontrado")
    reason = str(data.get("reason", ""))[:300]
    r = await db.manual_payment_confirmations.update_one(
        {"_id": ObjectId(cid), "status": {"$in": ["submitted", "under_review"]}},
        {"$set": {"status": "rejected", "reject_reason": reason,
                  "reviewed_by": admin.get("email", "admin"), "reviewed_at": now,
                  "updated_at": now}})
    if not r.modified_count:
        raise HTTPException(status_code=400, detail="No se pudo rechazar (estado inválido)")

    # Notificación interna al tenant — reason visible, sin notas internas de admin
    body_es = "Tu confirmación de pago fue rechazada."
    body_en = "Your payment confirmation was rejected."
    if reason:
        body_es += f" Motivo: {reason}"
        body_en += f" Reason: {reason}"
    await _notify(
        user_id=s["tenant_id"],
        title="Tu confirmación de pago fue rechazada.",
        body=body_es,
        title_en="Your payment confirmation was rejected.",
        body_en=body_en,
        ntype="manual_confirmation_rejected",
        data={"type": "manual_confirmation_rejected", "confirmation_id": cid,
              "reason": reason})
    return {"success": True}
