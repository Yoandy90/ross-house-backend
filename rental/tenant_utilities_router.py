"""
Tenant Utilities Router — Ross House Rentals
Allows tenants to track their utility bills, scan receipts with AI Vision,
and view consumption summaries directly from the mobile app.
"""
import os
import logging
import base64
from datetime import datetime
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
logger = logging.getLogger(__name__)


def get_db():
    from rental.shared import get_db as _get_db
    return _get_db()


async def auth_marketplace(request: Request):
    from rental.shared import auth_marketplace as _auth
    return await _auth(request)


# ═══════════════════════════════════════════════════════════════
# UTILITY PROVIDERS
# ═══════════════════════════════════════════════════════════════
PROVIDERS = [
    {"id": "xcel_energy", "name": "Xcel Energy", "type": "electricity", "icon": "flash", "color": "#F59E0B"},
    {"id": "atmos_energy", "name": "Atmos Energy", "type": "gas", "icon": "flame", "color": "#EF4444"},
    {"id": "city_water", "name": "Ciudad de Dumas - Agua", "type": "water", "icon": "water", "color": "#3B82F6"},
    {"id": "windstream", "name": "Windstream / Kinetic", "type": "internet", "icon": "wifi", "color": "#8B5CF6"},
    {"id": "sparklight", "name": "Sparklight", "type": "internet", "icon": "wifi", "color": "#8B5CF6"},
    {"id": "plains_internet", "name": "Plains Internet", "type": "internet", "icon": "wifi", "color": "#8B5CF6"},
    {"id": "att", "name": "AT&T", "type": "phone", "icon": "call", "color": "#06B6D4"},
    {"id": "tmobile", "name": "T-Mobile", "type": "phone", "icon": "call", "color": "#EC4899"},
    {"id": "directv", "name": "DirecTV", "type": "tv", "icon": "tv", "color": "#6366F1"},
    {"id": "other", "name": "Otro Servicio", "type": "other", "icon": "document-text", "color": "#6B7280"},
]


@router.get('/tenant/utilities/providers')
async def list_utility_providers(request: Request):
    """List available utility providers for the tenant app."""
    await auth_marketplace(request)
    return {"success": True, "providers": PROVIDERS}


# ═══════════════════════════════════════════════════════════════
# CREATE UTILITY RECORD
# ═══════════════════════════════════════════════════════════════
@router.post('/tenant/utilities')
async def create_utility_record(request: Request):
    """Add a utility bill record for the tenant."""
    user = await auth_marketplace(request)
    data = await request.json()

    required = ['provider_id', 'provider_name', 'amount']
    for field in required:
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"Campo requerido: {field}")

    record = {
        "tenant_id": str(user.get('_id', '')),
        "tenant_email": user.get('email', ''),
        "provider_id": data['provider_id'],
        "provider_name": data['provider_name'],
        "provider_type": data.get('provider_type', 'other'),
        "account_number": data.get('account_number', ''),
        "amount": float(data['amount']),
        "period": data.get('period', datetime.utcnow().strftime('%Y-%m')),
        "due_date": data.get('due_date', ''),
        "paid": data.get('paid', False),
        "bill_image_url": data.get('bill_image_url', ''),
        "extracted_data": data.get('extracted_data', {}),
        "notes": data.get('notes', ''),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await get_db().tenant_utilities.insert_one(record)
    record['_id'] = str(result.inserted_id)

    return {"success": True, "record": record, "message": "Registro de servicio agregado"}


# ═══════════════════════════════════════════════════════════════
# LIST UTILITY RECORDS
# ═══════════════════════════════════════════════════════════════
@router.get('/tenant/utilities')
async def list_utility_records(request: Request):
    """List tenant's utility records with optional filters."""
    user = await auth_marketplace(request)
    params = dict(request.query_params)

    query = {"tenant_id": str(user.get('_id', ''))}

    if params.get('provider_type'):
        query['provider_type'] = params['provider_type']
    if params.get('period'):
        query['period'] = params['period']
    if params.get('provider_id'):
        query['provider_id'] = params['provider_id']

    records = await get_db().tenant_utilities.find(query).sort("created_at", -1).to_list(200)
    for r in records:
        r['_id'] = str(r['_id'])
        if isinstance(r.get('created_at'), datetime):
            r['created_at'] = r['created_at'].isoformat()
        if isinstance(r.get('updated_at'), datetime):
            r['updated_at'] = r['updated_at'].isoformat()

    return {"success": True, "records": records, "count": len(records)}


# ═══════════════════════════════════════════════════════════════
# UTILITY SUMMARY
# ═══════════════════════════════════════════════════════════════
@router.get('/tenant/utilities/summary')
async def utility_summary(request: Request):
    """Get tenant's utility spending summary."""
    user = await auth_marketplace(request)
    tenant_id = str(user.get('_id', ''))

    all_records = await get_db().tenant_utilities.find(
        {"tenant_id": tenant_id}
    ).sort("period", -1).to_list(500)

    # By type totals
    by_type = {}
    by_period = {}
    total_all_time = 0

    for r in all_records:
        ptype = r.get('provider_type', 'other')
        period = r.get('period', 'unknown')
        amount = r.get('amount', 0)

        if ptype not in by_type:
            by_type[ptype] = {"count": 0, "total": 0, "last_amount": 0, "provider_name": r.get('provider_name', '')}
        by_type[ptype]["count"] += 1
        by_type[ptype]["total"] += amount
        if by_type[ptype]["count"] == 1 or period >= by_type[ptype].get("last_period", ""):
            by_type[ptype]["last_amount"] = amount
            by_type[ptype]["last_period"] = period

        if period not in by_period:
            by_period[period] = {"total": 0, "services": {}}
        by_period[period]["total"] += amount
        by_period[period]["services"][ptype] = by_period[period]["services"].get(ptype, 0) + amount

        total_all_time += amount

    # Current month
    current_period = datetime.utcnow().strftime('%Y-%m')
    current_month_total = by_period.get(current_period, {}).get('total', 0)

    # Last 6 months trend
    periods_sorted = sorted(by_period.keys(), reverse=True)[:6]
    trend = [{"period": p, "total": round(by_period[p]["total"], 2), "services": by_period[p]["services"]} for p in reversed(periods_sorted)]

    return {
        "success": True,
        "current_month_total": round(current_month_total, 2),
        "total_all_time": round(total_all_time, 2),
        "total_records": len(all_records),
        "by_type": {k: {**v, "total": round(v["total"], 2)} for k, v in by_type.items()},
        "trend": trend,
        "current_period": current_period,
    }


# ═══════════════════════════════════════════════════════════════
# ADMIN VISIBILITY — list all tenant-scanned utility records
# ═══════════════════════════════════════════════════════════════
async def auth_admin_local(request: Request):
    from rental.shared import auth_admin as _auth
    return await _auth(request)


@router.get('/admin/tenant-utilities')
async def admin_list_tenant_utilities(
    request: Request,
    tenant_id: str = "",
    provider_type: str = "",
    period: str = "",
    page: int = 1,
    limit: int = 50,
):
    """Admin: list all utility records scanned/added by tenants across the portfolio.
    Useful for the admin OCR dashboard to monitor incoming bills.
    """
    await auth_admin_local(request)
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 50), 200))

    query: dict = {}
    if tenant_id:
        query["tenant_id"] = tenant_id
    if provider_type:
        query["provider_type"] = provider_type
    if period:
        query["period"] = period

    db = get_db()
    total = await db.tenant_utilities.count_documents(query)
    skip = (page - 1) * limit
    cursor = db.tenant_utilities.find(query).sort("created_at", -1).skip(skip).limit(limit)
    records = []
    async for r in cursor:
        records.append({
            "id": str(r["_id"]),
            "tenant_id": r.get("tenant_id", ""),
            "tenant_email": r.get("tenant_email", ""),
            "provider_id": r.get("provider_id", ""),
            "provider_name": r.get("provider_name", ""),
            "provider_type": r.get("provider_type", "other"),
            "account_number": r.get("account_number", ""),
            "amount": r.get("amount", 0),
            "period": r.get("period", ""),
            "due_date": r.get("due_date", ""),
            "paid": bool(r.get("paid", False)),
            "notes": r.get("notes", ""),
            "extracted_data": r.get("extracted_data", {}),
            "source": "tenant_scan" if r.get("extracted_data") else "tenant_manual",
            "created_at": r.get("created_at", "").isoformat() if r.get("created_at") else "",
        })

    return {
        "success": True,
        "records": records,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


# ═══════════════════════════════════════════════════════════════
# UPDATE UTILITY RECORD
# ═══════════════════════════════════════════════════════════════
@router.put('/tenant/utilities/{record_id}')
async def update_utility_record(record_id: str, request: Request):
    """Update a utility record (mark as paid, edit amount, etc.)."""
    user = await auth_marketplace(request)
    data = await request.json()

    record = await get_db().tenant_utilities.find_one({
        "_id": ObjectId(record_id),
        "tenant_id": str(user.get('_id', ''))
    })
    if not record:
        raise HTTPException(status_code=404, detail="Registro no encontrado")

    update_fields = {}
    allowed = ['amount', 'paid', 'notes', 'account_number', 'due_date', 'period']
    for field in allowed:
        if field in data:
            update_fields[field] = data[field]
    update_fields["updated_at"] = datetime.utcnow()

    await get_db().tenant_utilities.update_one(
        {"_id": ObjectId(record_id)},
        {"$set": update_fields}
    )
    return {"success": True, "message": "Registro actualizado"}


# ═══════════════════════════════════════════════════════════════
# DELETE UTILITY RECORD
# ═══════════════════════════════════════════════════════════════
@router.delete('/tenant/utilities/{record_id}')
async def delete_utility_record(record_id: str, request: Request):
    """Delete a utility record."""
    user = await auth_marketplace(request)
    result = await get_db().tenant_utilities.delete_one({
        "_id": ObjectId(record_id),
        "tenant_id": str(user.get('_id', ''))
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    return {"success": True, "message": "Registro eliminado"}


# ═══════════════════════════════════════════════════════════════
# SCAN BILL (GPT-4o Vision)
# ═══════════════════════════════════════════════════════════════
@router.post('/tenant/utilities/scan')
async def scan_utility_bill(request: Request):
    """
    Use GPT-4o Vision (via emergentintegrations + Emergent LLM Key) to
    extract data from a utility bill photo.
    Expects: { "image_base64": "..." }
    Returns: extracted provider, amount, due date, account number, period.
    """
    user = await auth_marketplace(request)
    data = await request.json()

    image_b64 = data.get('image_base64', '')
    if not image_b64:
        raise HTTPException(status_code=400, detail="Se requiere image_base64")

    # Remove data URL prefix if present
    if ',' in image_b64:
        image_b64 = image_b64.split(',')[1]

    # ── 1) Validate the image FIRST (avoid leaking 500s on user-side errors) ──
    import tempfile
    import json as _json

    try:
        img_bytes = base64.b64decode(image_b64, validate=False)
    except Exception:
        raise HTTPException(status_code=400, detail="image_base64 inválido (no es base64 válido)")

    if len(img_bytes) == 0:
        raise HTTPException(status_code=400, detail="Imagen vacía")
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande (>10 MB)")

    # ── 2) Now check the API key (server-side config, not user error) ──
    llm_key = os.getenv('EMERGENT_LLM_KEY') or os.getenv('OPENAI_API_KEY', '')
    if not llm_key:
        logger.error("⚠️ EMERGENT_LLM_KEY / OPENAI_API_KEY no configurado en producción")
        # 503 = upstream service unavailable (config error, not client error)
        raise HTTPException(
            status_code=503,
            detail="Servicio de OCR no disponible: falta API key en el servidor. Contacta al administrador."
        )

    system_prompt = """You are a utility bill data extractor for Ross House Rentals. 
Extract the following from the utility bill image and respond ONLY in valid JSON:
{
  "provider_name": "name of the utility company",
  "provider_type": "electricity|gas|water|internet|phone|tv|other",
  "account_number": "account number if visible",
  "amount_due": 0.00,
  "due_date": "YYYY-MM-DD if visible",
  "billing_period": "YYYY-MM (the month the bill covers)",
  "usage_kwh": null or number if electricity,
  "usage_therms": null or number if gas,
  "usage_gallons": null or number if water,
  "customer_name": "name on the bill if visible",
  "service_address": "service address if visible",
  "confidence": "high|medium|low"
}
If you cannot extract a field, use null. Always return valid JSON only."""

    tmp_path = None
    try:
        # Write to tempfile for emergentintegrations file_contents API
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        tmp.write(img_bytes)
        tmp.close()
        tmp_path = tmp.name

        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage, FileContentWithMimeType  # type: ignore
        except Exception as ie:
            logger.error(f"emergentintegrations not available: {ie}")
            raise HTTPException(status_code=503, detail="AI Vision no disponible (emergentintegrations missing)")

        chat = (
            LlmChat(
                api_key=llm_key,
                session_id=f"tenant-scan-{datetime.utcnow().timestamp()}",
                system_message=system_prompt,
            )
            .with_model("openai", "gpt-4o")
            .with_max_tokens(700)
        )
        user_msg = UserMessage(
            text="Extract all utility bill information from this image and respond ONLY with the JSON object.",
            file_contents=[FileContentWithMimeType(
                file_path=tmp_path,
                mime_type="image/jpeg",
            )],
        )
        raw = (await chat.send_message(user_msg) or "").strip()

        # Strip markdown code fences (LLM commonly wraps in ```json ... ```)
        if raw.startswith("```"):
            # Remove leading fence
            raw = raw.lstrip("`").lstrip()
            # Drop optional 'json' tag
            if raw.lower().startswith("json"):
                raw = raw[4:].lstrip()
            # Strip trailing fence
            if "```" in raw:
                raw = raw.split("```", 1)[0].rstrip()

        try:
            extracted = _json.loads(raw)
        except _json.JSONDecodeError:
            logger.warning(f"Tenant scan: non-JSON response (first 300 chars): {raw[:300]}")
            return {
                "success": False,
                "extracted_data": {},
                "message": "No se pudo extraer datos de la imagen. Intenta con una foto más clara.",
            }

        logger.info(f"✅ Tenant bill scan ok for {user.get('email', '')}: {extracted.get('provider_name', 'unknown')}")
        return {
            "success": True,
            "extracted_data": extracted,
            "message": "Datos extraídos exitosamente",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Scan bill error: {e}")
        # Graceful fallback — don't crash the mobile app
        return {
            "success": False,
            "extracted_data": {},
            "message": f"No se pudo procesar la imagen: {str(e)[:120]}",
        }
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
