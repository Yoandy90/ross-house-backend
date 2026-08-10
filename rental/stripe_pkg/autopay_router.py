"""Autopay endpoints — tenant configuration/status + admin overview/manual trigger."""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin, auth_marketplace

router = APIRouter()


@router.post('/tenant/autopay/configure')
async def tenant_configure_autopay(request: Request):
    """Enable or disable autopay for rent."""
    user = await auth_marketplace(request)
    data = await request.json()

    enabled = data.get("enabled", False)
    payment_method_id = data.get("payment_method_id", "")
    day_of_month = int(data.get("day_of_month", 1))

    if day_of_month < 1 or day_of_month > 28:
        raise HTTPException(status_code=400, detail="El día debe ser entre 1 y 28")

    if enabled and not payment_method_id:
        raise HTTPException(status_code=400, detail="Selecciona un método de pago para autopago")

    now = datetime.utcnow()
    await get_db().autopay_config.update_one(
        {"user_id": str(user["_id"])},
        {"$set": {
            "user_id": str(user["_id"]),
            "user_name": user.get("name", ""),
            "user_email": user.get("email", ""),
            "enabled": enabled,
            "payment_method_id": payment_method_id,
            "day_of_month": day_of_month,
            "updated_at": now,
        },
        "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    status = "activado" if enabled else "desactivado"
    return {"success": True, "message": f"Autopago {status} exitosamente"}


@router.get('/tenant/autopay/status')
async def tenant_autopay_status(request: Request):
    """Get current autopay configuration."""
    user = await auth_marketplace(request)
    autopay = await get_db().autopay_config.find_one({"user_id": str(user["_id"])})

    if not autopay:
        return {"success": True, "autopay": {"enabled": False, "payment_method_id": "", "day_of_month": 1}}

    return {
        "success": True,
        "autopay": {
            "enabled": autopay.get("enabled", False),
            "payment_method_id": autopay.get("payment_method_id", ""),
            "day_of_month": autopay.get("day_of_month", 1),
            "last_attempt_date": autopay.get("last_attempt_date"),
            "last_attempt_status": autopay.get("last_attempt_status"),
            "last_attempt_error": autopay.get("last_attempt_error"),
            "successful_charges": autopay.get("successful_charges", 0),
            "failed_charges": autopay.get("failed_charges", 0),
        }
    }


@router.get('/admin/autopay/configs')
async def admin_list_autopay_configs(request: Request):
    """Admin: List ALL autopay configs across tenants with stats."""
    await auth_admin(request)
    items = []
    cursor = get_db().autopay_config.find({}).sort("updated_at", -1)
    async for c in cursor:
        items.append({
            "id": str(c["_id"]),
            "user_id": c.get("user_id", ""),
            "user_name": c.get("user_name", ""),
            "user_email": c.get("user_email", ""),
            "enabled": c.get("enabled", False),
            "day_of_month": c.get("day_of_month", 1),
            "payment_method_id": c.get("payment_method_id", ""),
            "last_attempt_date": c.get("last_attempt_date"),
            "last_attempt_status": c.get("last_attempt_status"),
            "last_attempt_error": c.get("last_attempt_error"),
            "successful_charges": c.get("successful_charges", 0),
            "failed_charges": c.get("failed_charges", 0),
            "updated_at": c.get("updated_at"),
        })
    return {"success": True, "configs": items, "count": len(items)}


@router.get('/admin/autopay/tenants')
async def admin_autopay_tenants(request: Request):
    """Admin: usuarios con cliente Stripe y sus tarjetas guardadas (para crear autopagos)."""
    await auth_admin(request)
    import os
    import stripe as stripe_lib
    from rental.stripe_pkg.helpers import _get_stripe_config
    config = await _get_stripe_config()
    sk = (config.get("stripe_secret_key") or os.environ.get("STRIPE_SECRET_KEY")
          or os.environ.get("STRIPE_API_KEY") or "")
    out = []
    cursor = get_db().app_users.find(
        {"stripe_customer_id": {"$exists": True, "$ne": ""}},
        {"name": 1, "email": 1, "stripe_customer_id": 1})
    async for u in cursor:
        cards = []
        if sk:
            try:
                stripe_lib.api_key = sk
                pms = stripe_lib.PaymentMethod.list(customer=u["stripe_customer_id"], type="card")
                cards = [{"id": pm["id"], "brand": pm["card"]["brand"],
                          "last4": pm["card"]["last4"],
                          "exp": f"{pm['card']['exp_month']}/{pm['card']['exp_year']}"}
                         for pm in pms.get("data", [])]
            except Exception:
                pass
        out.append({"user_id": str(u["_id"]), "name": u.get("name", ""),
                    "email": u.get("email", ""), "cards": cards})
    return {"success": True, "tenants": out}


@router.post('/admin/autopay/configs')
async def admin_create_autopay(request: Request):
    """Admin: crear (o reemplazar) el autopago de un inquilino."""
    await auth_admin(request)
    data = await request.json()
    user_id = (data.get("user_id") or "").strip()
    pm = (data.get("payment_method_id") or "").strip()
    day = int(data.get("day_of_month", 1))
    if day < 1 or day > 28:
        raise HTTPException(status_code=400, detail="El día debe ser entre 1 y 28")
    if not user_id or not pm:
        raise HTTPException(status_code=400, detail="Inquilino y método de pago son requeridos")
    from bson import ObjectId
    user = await get_db().app_users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")
    now = datetime.utcnow()
    await get_db().autopay_config.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "user_name": user.get("name", ""),
            "user_email": user.get("email", ""),
            "enabled": bool(data.get("enabled", True)),
            "payment_method_id": pm,
            "day_of_month": day,
            "updated_at": now,
            "created_by_admin": True,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True)
    return {"success": True, "message": "Autopago creado"}


@router.put('/admin/autopay/configs/{config_id}')
async def admin_update_autopay(request: Request, config_id: str):
    """Admin: editar día/tarjeta o activar/cancelar un autopago."""
    await auth_admin(request)
    data = await request.json()
    from bson import ObjectId
    upd = {"updated_at": datetime.utcnow()}
    if "enabled" in data:
        upd["enabled"] = bool(data["enabled"])
    if "day_of_month" in data:
        day = int(data["day_of_month"])
        if day < 1 or day > 28:
            raise HTTPException(status_code=400, detail="El día debe ser entre 1 y 28")
        upd["day_of_month"] = day
    if data.get("payment_method_id"):
        upd["payment_method_id"] = data["payment_method_id"].strip()
    r = await get_db().autopay_config.update_one({"_id": ObjectId(config_id)}, {"$set": upd})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Autopago no encontrado")
    return {"success": True, "message": "Autopago actualizado"}


@router.delete('/admin/autopay/configs/{config_id}')
async def admin_delete_autopay(request: Request, config_id: str):
    """Admin: eliminar un autopago por completo."""
    await auth_admin(request)
    from bson import ObjectId
    r = await get_db().autopay_config.delete_one({"_id": ObjectId(config_id)})
    if r.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Autopago no encontrado")
    return {"success": True, "message": "Autopago eliminado"}


@router.post('/admin/autopay/run-now')
async def admin_trigger_autopay(request: Request):
    """Admin: Manually trigger the autopay cron once (useful for testing/recovery)."""
    await auth_admin(request)
    try:
        from rental.autopay_cron import run_once
        stats = await run_once(get_db())
        return {"success": True, "message": "Autopago ejecutado", "stats": stats}
    except Exception as e:
        logging.exception("Autopay manual run failed")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
