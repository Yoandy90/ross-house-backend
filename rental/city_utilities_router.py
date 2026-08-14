"""
City of Dumas Utilities Router — monitoreo de bills de agua/basura de la ciudad
Portal: dumastx.municipalonlinepayments.com (Tyler Technologies Quick Pay)

Flujo: POST Quick Pay con número de cuenta + monto del último pago (verificación
de la ciudad) → parsear página de pago: dirección, saldo y fecha de vencimiento.

Endpoints:
  GET    /admin/city-utilities/accounts        — lista cuentas + último estado
  POST   /admin/city-utilities/accounts        — agregar cuenta {account_number, last_payment_amount, label}
  PATCH  /admin/city-utilities/accounts/{id}   — editar (monto verificación, label, active)
  DELETE /admin/city-utilities/accounts/{id}
  POST   /admin/city-utilities/sync            — sincronizar todas ahora

Cron: chequeo diario 8AM CT. Alerta por email si hay saldo > 0 (y si la
verificación falla, avisa para actualizar el monto del último pago).
"""
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin, serialize

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone.utc

logger = logging.getLogger("city_utilities")
router = APIRouter()

QUICKPAY_URL = "https://dumastx.municipalonlinepayments.com/dumastx/utilities/quickpay"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
ADMIN_EMAIL = "yoandyross@gmail.com"


# ─── Scraper del portal Quick Pay ────────────────────────────────

async def lookup_bill(account_number: str, last_payment_amount: str) -> dict:
    """Consulta el saldo de una cuenta en el portal de la ciudad.
    Returns: {ok, verify_failed, account, address, balance, due_date, raw_error}"""
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=True,
                                     headers={"User-Agent": UA}) as c:
            r = await c.get(QUICKPAY_URL)
            form_m = re.search(r'<form[^>]*quickpay[^>]*>(.*?)</form>', r.text, re.S | re.I)
            if not form_m:
                return {"ok": False, "verify_failed": False, "raw_error": "form no encontrado"}
            data = {}
            for tag in re.findall(r'<input[^>]*>', form_m.group(1)):
                n = re.search(r'name="([^"]+)"', tag)
                v = re.search(r'value="([^"]*)"', tag)
                if n:
                    data[n.group(1)] = v.group(1) if v else ""
            data["AccountNumber"] = account_number.strip()
            data["regentry_*LP*"] = str(last_payment_amount).replace("$", "").strip()
            r2 = await c.post(QUICKPAY_URL, data=data)
            if "problem verifying" in r2.text or "quickpay" in str(r2.url).lower():
                return {"ok": False, "verify_failed": True,
                        "raw_error": "La ciudad no pudo verificar cuenta + último pago"}
            html = r2.text
            addr = re.search(
                r'forge-typography--subheading1 address-field[^"]*"[^>]*>\s*([^<]+?)\s*</div>', html)
            due = re.search(r'<div slot="label">Due</div>\s*<div slot="value">([^<]+)</div>', html)
            bal = re.search(r'id="Due_[^"]*"[^>]*>\s*\$?([\d,]+\.\d\d)', html)
            return {
                "ok": True, "verify_failed": False,
                "account": account_number.strip(),
                "address": (addr.group(1).strip() if addr else ""),
                "balance": float(bal.group(1).replace(",", "")) if bal else 0.0,
                "due_date": (due.group(1).strip() if due else ""),
            }
    except Exception as e:
        logger.warning(f"[city-utils] lookup falló para {account_number}: {e}")
        return {"ok": False, "verify_failed": False, "raw_error": str(e)[:200]}


async def _match_property(db, service_addr: str) -> Optional[dict]:
    """Empareja la dirección de servicio de la ciudad ('305 BRUCE') con una
    propiedad del sistema ('305 Bruce Ave')."""
    if not service_addr:
        return None
    tokens = service_addr.lower().split()
    num = tokens[0] if tokens and tokens[0].isdigit() else None
    street = tokens[1] if len(tokens) > 1 else None
    async for p in db.properties.find({}):
        pa = (p.get("address") or "").lower()
        if num and pa.startswith(num) and (not street or street in pa):
            return p
    return None


async def _sync_invoice(db, acc: dict, res: dict):
    """Libro de facturas de servicios de ciudad (city_utility_invoices).
    - Saldo > 0: crea/actualiza factura ABIERTA (dedupe por cuenta+vencimiento)
    - Saldo vuelve a 0: marca las facturas abiertas de esa cuenta como PAGADAS"""
    now = datetime.now(timezone.utc)
    acct = acc["account_number"]
    label = acc.get("label") or res.get("address", "")
    if res["balance"] > 0 and res.get("due_date"):
        inv_ref = f"CITY-{acct}-{res['due_date'].replace('/', '-')}"
        existing = await db.city_utility_invoices.find_one({"invoice_ref": inv_ref})
        if existing:
            if existing.get("status") == "open" and \
                    float(existing.get("amount") or 0) != res["balance"]:
                await db.city_utility_invoices.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"amount": res["balance"], "updated_at": now}})
            return
        count = await db.city_utility_invoices.count_documents({})
        await db.city_utility_invoices.insert_one({
            "invoice_number": f"CU-{now.year}-{str(count + 1).zfill(4)}",
            "invoice_ref": inv_ref,
            "account_number": acct,
            "label": label,
            "address": res.get("address", ""),
            "amount": res["balance"],
            "due_date": res["due_date"],
            "status": "open",
            "issued_at": now,
            "detected_at": now,
            "paid_at": None,
            "created_at": now, "updated_at": now,
        })
        logger.info(f"[city-utils] factura ABIERTA: {acct} ${res['balance']:,.2f} vence {res['due_date']}")
    elif res["balance"] == 0:
        r = await db.city_utility_invoices.update_many(
            {"account_number": acct, "status": "open"},
            {"$set": {"status": "paid", "paid_at": now, "updated_at": now}})
        if r.modified_count:
            logger.info(f"[city-utils] {r.modified_count} factura(s) de {acct} marcadas PAGADAS")


async def _sync_expense(db, acc: dict, res: dict):
    """Registra el bill como gasto en /admin/gastos (categoría utilities).
    - Bill nuevo (saldo > 0): crea gasto 'pending' (dedupe por cuenta+vencimiento)
    - Saldo vuelve a 0: marca los gastos pendientes de esa cuenta como pagados"""
    now = datetime.utcnow()
    acct = acc["account_number"]
    if res["balance"] > 0 and res.get("due_date"):
        ref = f"CITY-{acct}-{res['due_date'].replace('/', '-')}"
        existing = await db.property_expenses.find_one({"receipt_number": ref})
        if existing:
            if existing.get("status") == "pending" and \
                    float(existing.get("amount") or 0) != res["balance"]:
                await db.property_expenses.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"amount": res["balance"], "updated_at": now}})
            return
        prop = await _match_property(db, res.get("address") or acc.get("label", ""))
        count = await db.property_expenses.count_documents({})
        await db.property_expenses.insert_one({
            "expense_number": f"EXP-{now.year}-{str(count + 1).zfill(4)}",
            "property_id": str(prop["_id"]) if prop else None,
            "property_address": (prop or {}).get("address") or acc.get("label") or res.get("address", ""),
            "property_number": (prop or {}).get("property_number", ""),
            "category": "utilities",
            "description": f"Agua/basura City de Dumas — cuenta {acct} (vence {res['due_date']})",
            "amount": res["balance"],
            "vendor": "City of Dumas",
            "expense_date": now.strftime("%Y-%m-%d"),
            "receipt_number": ref,
            "notes": "Registrado automáticamente por el monitor de City de Dumas",
            "status": "pending",
            "created_at": now, "updated_at": now,
            "created_by": "city-utilities-bot",
        })
        logger.info(f"[city-utils] gasto creado: {acct} ${res['balance']:,.2f} (vence {res['due_date']})")
    elif res["balance"] == 0:
        r = await db.property_expenses.update_many(
            {"receipt_number": {"$regex": f"^CITY-{re.escape(acct)}-"}, "status": "pending"},
            {"$set": {"status": "completed", "updated_at": now,
                      "notes": f"Pagado — confirmado por el monitor el {now.strftime('%m/%d/%Y')}"}})
        if r.modified_count:
            logger.info(f"[city-utils] {r.modified_count} gasto(s) de {acct} marcados como pagados")


async def sync_account(db, acc: dict) -> dict:
    """Sincroniza una cuenta y guarda el resultado en el documento."""
    res = await lookup_bill(acc["account_number"], acc.get("last_payment_amount", ""))
    now = datetime.now(timezone.utc)
    upd = {"last_checked_at": now}
    if res["ok"]:
        upd.update({
            "status": "ok",
            "address": res["address"] or acc.get("address", ""),
            "balance": res["balance"],
            "due_date": res["due_date"],
            "last_error": None,
        })
        try:
            await _sync_expense(db, acc, res)
        except Exception as e:
            logger.warning(f"[city-utils] registro de gasto falló para {acc['account_number']}: {e}")
        try:
            await _sync_invoice(db, acc, res)
        except Exception as e:
            logger.warning(f"[city-utils] registro de factura falló para {acc['account_number']}: {e}")
    else:
        upd.update({
            "status": "verify_failed" if res.get("verify_failed") else "error",
            "last_error": res.get("raw_error"),
        })
    await db.city_utility_accounts.update_one({"_id": acc["_id"]}, {"$set": upd})
    return {**serialize({**acc, **upd}), "ok": res["ok"]}


async def _send_admin_alert(db, subject: str, html: str) -> bool:
    from rental.tax_1099_router import _send_admin_email
    return await _send_admin_email(db, subject, html)


async def sync_all_and_alert(db, alert: bool = True) -> dict:
    """Sincroniza todas las cuentas activas. Si hay saldo > 0 o verificación
    fallida, envía UNA alerta consolidada al admin (máx. 1 cada 20h por estado)."""
    results = []
    async for acc in db.city_utility_accounts.find({"active": {"$ne": False}}):
        results.append(await sync_account(db, acc))
        await asyncio.sleep(2)  # cortesía con el portal
    with_debt = [r for r in results if r.get("status") == "ok" and (r.get("balance") or 0) > 0]
    failed = [r for r in results if r.get("status") == "verify_failed"]
    if alert and (with_debt or failed):
        rows = ""
        for r in with_debt:
            rows += (f"<tr><td style='padding:6px'><b>{r.get('label') or r.get('address','')}</b>"
                     f"<br/><span style='color:#64748b;font-size:11px'>{r['account_number']}</span></td>"
                     f"<td style='padding:6px;text-align:right;color:#b91c1c'><b>${r['balance']:,.2f}</b></td>"
                     f"<td style='padding:6px'>{r.get('due_date','')}</td></tr>")
        for r in failed:
            rows += (f"<tr><td style='padding:6px'><b>{r.get('label') or r['account_number']}</b></td>"
                     f"<td colspan='2' style='padding:6px;color:#b45309'>⚠️ Verificación falló — "
                     f"actualiza el monto del último pago en el panel</td></tr>")
        html = f"""
        <div style="font-family:system-ui,Arial,sans-serif;max-width:560px;margin:0 auto">
          <div style="background:#0c4a6e;color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">
            <h2 style="margin:0;font-size:17px">💧 City de Dumas — bills de agua/basura</h2>
          </div>
          <div style="border:1px solid #e2e8f0;border-top:0;padding:20px;border-radius:0 0 12px 12px;font-size:14px">
            <table style="width:100%;border-collapse:collapse">
              <tr style="text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">
                <th style="padding:6px">Cuenta</th><th style="padding:6px;text-align:right">Saldo</th>
                <th style="padding:6px">Vence</th></tr>
              {rows}
            </table>
            <a href="https://dumastx.municipalonlinepayments.com/dumastx/utilities/quickpay"
               style="display:inline-block;background:#0891b2;color:#fff;padding:9px 16px;border-radius:8px;
               text-decoration:none;font-weight:bold;font-size:13px;margin-top:12px">Pagar en el portal →</a>
            <span style="font-size:11px;color:#64748b;margin-left:8px">Kiosko 24/7 en City Hall · Tel 1-888-401-4282</span>
          </div>
        </div>"""
        n = len(with_debt)
        subject = (f"💧 City de Dumas: {n} cuenta(s) con saldo pendiente" if with_debt
                   else "⚠️ City de Dumas: actualiza la verificación de tus cuentas")
        await _send_admin_alert(db, subject, html)
    return {"success": True, "synced": len(results),
            "with_debt": len(with_debt), "verify_failed": len(failed),
            "accounts": results}


# ─── Endpoints admin ─────────────────────────────────────────────

@router.get("/admin/city-utilities/accounts")
async def list_accounts(request: Request):
    await auth_admin(request)
    db = get_db()
    out = [serialize(a) async for a in
           db.city_utility_accounts.find({}).sort("created_at", 1)]
    for a in out:
        a.pop("last_payment_amount", None)  # no exponer el dato de verificación
    return {"success": True, "accounts": out}


@router.post("/admin/city-utilities/accounts")
async def add_account(request: Request):
    """Body: {account_number, last_payment_amount, label} — verifica en vivo antes de guardar."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    acct = str(data.get("account_number", "")).strip()
    lp = str(data.get("last_payment_amount", "")).replace("$", "").strip()
    if not acct or not lp:
        raise HTTPException(status_code=422, detail="Número de cuenta y monto del último pago requeridos")
    if await db.city_utility_accounts.find_one({"account_number": acct}):
        raise HTTPException(status_code=409, detail="Esa cuenta ya está registrada")
    res = await lookup_bill(acct, lp)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res.get(
            "raw_error", "No se pudo verificar la cuenta con el portal de la ciudad"))
    doc = {
        "account_number": acct,
        "last_payment_amount": lp,
        "label": str(data.get("label", "")).strip() or res["address"],
        "address": res["address"],
        "balance": res["balance"],
        "due_date": res["due_date"],
        "status": "ok",
        "active": True,
        "created_at": datetime.now(timezone.utc),
        "last_checked_at": datetime.now(timezone.utc),
    }
    ins = await db.city_utility_accounts.insert_one(doc)
    doc["_id"] = ins.inserted_id
    return {"success": True, "account": serialize(doc)}


@router.patch("/admin/city-utilities/accounts/{account_id}")
async def update_account(account_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    upd = {}
    if "last_payment_amount" in data:
        upd["last_payment_amount"] = str(data["last_payment_amount"]).replace("$", "").strip()
        upd["status"] = "ok"  # re-verificará en el próximo sync
    if "label" in data:
        upd["label"] = str(data["label"]).strip()
    if "active" in data:
        upd["active"] = bool(data["active"])
    if not upd:
        raise HTTPException(status_code=422, detail="Nada que actualizar")
    from bson import ObjectId
    r = await db.city_utility_accounts.update_one({"_id": ObjectId(account_id)}, {"$set": upd})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return {"success": True}


@router.delete("/admin/city-utilities/accounts/{account_id}")
async def delete_account(account_id: str, request: Request):
    await auth_admin(request)
    from bson import ObjectId
    r = await get_db().city_utility_accounts.delete_one({"_id": ObjectId(account_id)})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return {"success": True}


@router.post("/admin/city-utilities/sync")
async def manual_sync(request: Request):
    await auth_admin(request)
    return await sync_all_and_alert(get_db(), alert=False)


@router.get("/admin/city-utilities/invoices")
async def list_invoices(request: Request, status: Optional[str] = None):
    """Libro de facturas de servicios de ciudad (abiertas + pagadas)."""
    await auth_admin(request)
    db = get_db()
    filt: dict = {}
    if status in ("open", "paid"):
        filt["status"] = status
    invoices = [serialize(i) async for i in
                db.city_utility_invoices.find(filt).sort("issued_at", -1)]
    total_open = sum(i["amount"] for i in invoices if i["status"] == "open")
    total_paid = sum(i["amount"] for i in invoices if i["status"] == "paid")
    return {"success": True, "invoices": invoices,
            "open_count": sum(1 for i in invoices if i["status"] == "open"),
            "paid_count": sum(1 for i in invoices if i["status"] == "paid"),
            "total_open": round(total_open, 2), "total_paid": round(total_paid, 2)}


@router.post("/admin/city-utilities/invoices/manual-paid")
async def add_manual_paid_invoice(request: Request):
    """Registra una factura YA PAGADA (para pagos hechos antes de que el
    monitor los detectara). Body: {account_number, amount, paid_date, label?}"""
    await auth_admin(request)
    db = get_db()
    data = await request.json()
    acct = str(data.get("account_number", "")).strip()
    amount = float(str(data.get("amount", "0")).replace("$", "").replace(",", "").strip() or 0)
    paid_date = str(data.get("paid_date", "")).strip()
    if not acct or amount <= 0:
        raise HTTPException(status_code=422, detail="Número de cuenta y monto (> 0) requeridos")
    acc = await db.city_utility_accounts.find_one({"account_number": acct})
    label = str(data.get("label", "")).strip() or (acc or {}).get("label") or (acc or {}).get("address", "")
    now = datetime.now(timezone.utc)
    try:
        paid_at = datetime.strptime(paid_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if paid_date else now
    except Exception:
        paid_at = now
    count = await db.city_utility_invoices.count_documents({})
    doc = {
        "invoice_number": f"CU-{now.year}-{str(count + 1).zfill(4)}",
        "invoice_ref": f"CITY-{acct}-MANUAL-{int(now.timestamp())}",
        "account_number": acct,
        "label": label,
        "address": (acc or {}).get("address", ""),
        "amount": round(amount, 2),
        "due_date": paid_date,
        "status": "paid",
        "issued_at": paid_at,
        "detected_at": now,
        "paid_at": paid_at,
        "manual": True,
        "created_at": now, "updated_at": now,
    }
    ins = await db.city_utility_invoices.insert_one(doc)
    doc["_id"] = ins.inserted_id

    # Registrar también como gasto contable COMPLETADO (para impuestos de fin de año)
    exp_ref = f"CITY-{acct}-MANUAL-{int(now.timestamp())}"
    prop = await _match_property(db, (acc or {}).get("address") or label)
    exp_count = await db.property_expenses.count_documents({})
    await db.property_expenses.insert_one({
        "expense_number": f"EXP-{now.year}-{str(exp_count + 1).zfill(4)}",
        "property_id": str(prop["_id"]) if prop else None,
        "property_address": (prop or {}).get("address") or label,
        "property_number": (prop or {}).get("property_number", ""),
        "category": "utilities",
        "description": f"Agua/basura City de Dumas — cuenta {acct} (factura {doc['invoice_number']})",
        "amount": round(amount, 2),
        "vendor": "City of Dumas",
        "expense_date": paid_at.strftime("%Y-%m-%d"),
        "receipt_number": exp_ref,
        "notes": "Pago registrado manualmente (Libro de Facturas)",
        "status": "completed",
        "created_at": now, "updated_at": now,
        "created_by": "manual",
    })
    return {"success": True, "invoice": serialize(doc)}


@router.delete("/admin/city-utilities/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str, request: Request):
    await auth_admin(request)
    from bson import ObjectId
    r = await get_db().city_utility_invoices.delete_one({"_id": ObjectId(invoice_id)})
    if not r.deleted_count:
        raise HTTPException(status_code=404, detail="Factura no encontrada")
    return {"success": True}


# ─── Cron diario (8AM CT) ────────────────────────────────────────

async def _should_run(db) -> bool:
    now_ct = datetime.now(CT)
    if now_ct.hour != 8:
        return False
    cfg = await db.app_settings.find_one({"_id": "city_utilities"}) or {}
    last = cfg.get("last_run_at")
    if last and isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last) < timedelta(hours=20):
            return False
    return True


async def city_utilities_loop():
    logger.info("🚀 City of Dumas utilities cron started (diario 8AM CT)")
    while True:
        try:
            db = get_db()
            if db is not None and await _should_run(db):
                await sync_all_and_alert(db, alert=True)
                await db.app_settings.update_one(
                    {"_id": "city_utilities"},
                    {"$set": {"last_run_at": datetime.now(timezone.utc)}}, upsert=True)
        except Exception as e:
            logger.exception(f"[city-utils] loop error: {e}")
        await asyncio.sleep(60 * 60)
