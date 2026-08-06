"""Conciliación bancaria con Plaid (Fase 3).

Flujo: link-token → Plaid Link (frontend) → exchange public_token → access_token
guardado en plaid_items → transactions/sync con cursor → bank_transactions →
auto-match contra registros internos (rentas, gastos, pagos a proveedores, utilities).

Convención Plaid: amount > 0 = dinero que SALE (cargo); amount < 0 = dinero que ENTRA.
Match: monto exacto (±$0.01) y fecha ±4 días. status: matched | unmatched | ignored.
"""
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin

router = APIRouter()
logger = logging.getLogger(__name__)


def _plaid():
    import plaid
    from plaid.api import plaid_api
    env = plaid.Environment.Sandbox if os.environ.get("PLAID_ENV", "sandbox") == "sandbox" \
        else plaid.Environment.Production
    cfg = plaid.Configuration(host=env, api_key={
        "clientId": os.environ["PLAID_CLIENT_ID"],
        "secret": os.environ["PLAID_SECRET"]})
    return plaid_api.PlaidApi(plaid.ApiClient(cfg))


# ── Fuentes internas para el match ──────────────────────────────────────────
# (colección, dirección: in=ingreso/out=egreso, etiqueta)
MATCH_SOURCES = [
    ("rental_payments", "in", "Renta"),
    ("property_expenses", "out", "Gasto propiedad"),
    ("provider_payments", "out", "Pago proveedor"),
    ("utility_payments", "out", "Utility"),
]
AMOUNT_FIELDS = ("amount", "amount_paid", "total", "total_amount")
DATE_FIELDS = ("paid_at", "date", "paid_date", "payment_date", "created_at")


def _doc_amount(doc: dict):
    for f in AMOUNT_FIELDS:
        v = doc.get(f)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _doc_date(doc: dict):
    for f in DATE_FIELDS:
        v = doc.get(f)
        if isinstance(v, datetime):
            return v
        if isinstance(v, str) and len(v) >= 10:
            try:
                return datetime.fromisoformat(v[:19].replace("Z", ""))
            except ValueError:
                continue
    return None


async def _auto_match(limit: int = 500) -> int:
    """Cruza bank_transactions 'unmatched' contra las fuentes internas."""
    db = get_db()
    matched = 0
    txs = await (db.bank_transactions.find({"match.status": "unmatched"})
                 .sort([("date", -1)]).limit(limit).to_list(limit))
    if not txs:
        return 0
    # precargar candidatos internos (últimos 400 por fuente)
    candidates = []
    for coll, direction, label in MATCH_SOURCES:
        async for doc in db[coll].find({}).sort([("_id", -1)]).limit(400):
            amt, dt = _doc_amount(doc), _doc_date(doc)
            if amt and dt and doc.get("status") not in ("cancelled", "void", "failed"):
                candidates.append({"coll": coll, "dir": direction, "label": label,
                                   "id": str(doc["_id"]), "amount": round(amt, 2),
                                   "date": dt,
                                   "desc": str(doc.get("provider_name") or doc.get("tenant_name")
                                               or doc.get("description") or doc.get("notes") or "")[:60]})
    used = set()
    for tx in txs:
        tx_amt = float(tx.get("amount") or 0)
        direction = "out" if tx_amt > 0 else "in"
        tx_date = tx.get("date")
        if isinstance(tx_date, str):
            tx_date = datetime.fromisoformat(tx_date[:10])
        best = None
        for c in candidates:
            if c["dir"] != direction or c["id"] in used:
                continue
            if abs(c["amount"] - abs(tx_amt)) > 0.01:
                continue
            delta = abs((c["date"] - tx_date).days) if tx_date else 99
            if delta <= 4 and (best is None or delta < best[0]):
                best = (delta, c)
        if best:
            _, c = best
            used.add(c["id"])
            await db.bank_transactions.update_one(
                {"_id": tx["_id"]},
                {"$set": {"match": {"status": "matched", "type": c["label"],
                                    "collection": c["coll"], "ref_id": c["id"],
                                    "ref_desc": c["desc"], "days_delta": best[0],
                                    "matched_at": datetime.utcnow()}}})
            matched += 1
    return matched


@router.post('/admin/plaid/link-token')
async def create_link_token(request: Request):
    await auth_admin(request)
    from plaid.model.country_code import CountryCode
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.products import Products
    try:
        resp = _plaid().link_token_create(LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id="rhr-admin"),
            client_name="Ross House Rentals",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")], language="es"))
        return {"success": True, "link_token": resp["link_token"],
                "env": os.environ.get("PLAID_ENV", "sandbox")}
    except KeyError:
        raise HTTPException(status_code=500, detail="PLAID_CLIENT_ID/PLAID_SECRET no configurados")
    except Exception as e:
        logger.error(f"[plaid] link_token: {e}")
        raise HTTPException(status_code=502, detail="Plaid rechazó la solicitud de link token")


@router.post('/admin/plaid/exchange')
async def exchange_token(request: Request):
    """Body: {public_token, institution_name?}"""
    await auth_admin(request)
    data = await request.json()
    public_token = data.get("public_token")
    if not public_token:
        raise HTTPException(status_code=400, detail="Falta public_token")
    from plaid.model.accounts_get_request import AccountsGetRequest
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    client = _plaid()
    try:
        ex = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token))
        access_token, item_id = ex["access_token"], ex["item_id"]
        acc = client.accounts_get(AccountsGetRequest(access_token=access_token))
        accounts = [{"account_id": a["account_id"], "name": a["name"],
                     "mask": a.get("mask"), "type": str(a["type"]),
                     "subtype": str(a.get("subtype", "")),
                     "balance": (a.get("balances") or {}).get("current")}
                    for a in acc["accounts"]]
    except Exception as e:
        logger.error(f"[plaid] exchange: {e}")
        raise HTTPException(status_code=502, detail="Plaid rechazó el intercambio de token")
    await get_db().plaid_items.update_one(
        {"item_id": item_id},
        {"$set": {"item_id": item_id, "access_token": access_token,
                  "institution_name": data.get("institution_name", ""),
                  "accounts": accounts, "cursor": None,
                  "linked_at": datetime.utcnow(), "last_synced_at": None}},
        upsert=True)
    return {"success": True, "item_id": item_id, "accounts": accounts}


@router.get('/admin/plaid/accounts')
async def list_accounts(request: Request):
    await auth_admin(request)
    items = []
    async for it in get_db().plaid_items.find({}, {"access_token": 0}):
        it["_id"] = str(it["_id"])
        items.append(it)
    return {"success": True, "items": items,
            "env": os.environ.get("PLAID_ENV", "sandbox")}


@router.delete('/admin/plaid/items/{item_id}')
async def unlink_item(item_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    it = await db.plaid_items.find_one({"item_id": item_id})
    if not it:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    try:
        from plaid.model.item_remove_request import ItemRemoveRequest
        _plaid().item_remove(ItemRemoveRequest(access_token=it["access_token"]))
    except Exception as e:
        logger.warning(f"[plaid] item_remove: {e}")
    await db.plaid_items.delete_one({"item_id": item_id})
    await db.bank_transactions.delete_many({"item_id": item_id})
    return {"success": True, "message": "Cuenta desvinculada"}


async def run_full_sync() -> dict:
    """Sincroniza todos los items y auto-concilia. Usado por el endpoint y el cron."""
    db = get_db()
    from plaid.model.transactions_sync_request import TransactionsSyncRequest
    client = _plaid()
    total_added = total_removed = 0
    async for it in db.plaid_items.find({}):
        cursor = it.get("cursor")
        added, removed = [], []
        try:
            while True:
                kwargs = {"access_token": it["access_token"]}
                if cursor:
                    kwargs["cursor"] = cursor
                page = client.transactions_sync(TransactionsSyncRequest(**kwargs))
                added += [t.to_dict() for t in page["added"]] + \
                         [t.to_dict() for t in page["modified"]]
                removed += [t.to_dict() for t in page["removed"]]
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break
        except Exception as e:
            logger.error(f"[plaid] sync {it['item_id']}: {e}")
            continue
        now = datetime.utcnow()
        for t in added:
            date_val = t.get("date")
            if hasattr(date_val, "isoformat") and not isinstance(date_val, datetime):
                date_val = datetime(date_val.year, date_val.month, date_val.day)
            await db.bank_transactions.update_one(
                {"transaction_id": t["transaction_id"]},
                {"$set": {"transaction_id": t["transaction_id"],
                          "item_id": it["item_id"],
                          "account_id": t.get("account_id"),
                          "name": t.get("name") or t.get("merchant_name") or "",
                          "amount": float(t.get("amount") or 0),
                          "date": date_val, "pending": bool(t.get("pending")),
                          "category": (t.get("personal_finance_category") or {}).get("primary", ""),
                          "updated_at": now},
                 "$setOnInsert": {"match": {"status": "unmatched"}, "created_at": now}},
                upsert=True)
        for t in removed:
            await db.bank_transactions.delete_one({"transaction_id": t["transaction_id"]})
        await db.plaid_items.update_one(
            {"_id": it["_id"]},
            {"$set": {"cursor": cursor, "last_synced_at": now}})
        total_added += len(added)
        total_removed += len(removed)
    matched = await _auto_match()
    return {"success": True, "imported": total_added, "removed": total_removed,
            "auto_matched": matched}


@router.post('/admin/plaid/sync')
async def sync_transactions(request: Request):
    """Sincroniza transacciones de todas las cuentas vinculadas y auto-concilia."""
    await auth_admin(request)
    return await run_full_sync()


@router.get('/admin/plaid/transactions')
async def list_transactions(request: Request, status: str = "", limit: int = 100):
    await auth_admin(request)
    db = get_db()
    q = {}
    if status in ("matched", "unmatched", "ignored"):
        q["match.status"] = status
    txs = []
    async for t in db.bank_transactions.find(q).sort([("date", -1)]).limit(min(limit, 300)):
        t["_id"] = str(t["_id"])
        if isinstance(t.get("date"), datetime):
            t["date"] = t["date"].strftime("%Y-%m-%d")
        txs.append(t)
    counts = {}
    async for row in db.bank_transactions.aggregate([
            {"$group": {"_id": "$match.status", "n": {"$sum": 1}}}]):
        counts[row["_id"] or "unmatched"] = row["n"]
    return {"success": True, "transactions": txs, "counts": counts}


@router.post('/admin/plaid/transactions/{transaction_id}/status')
async def set_tx_status(transaction_id: str, request: Request):
    """Body: {status: ignored|unmatched} — override manual."""
    await auth_admin(request)
    data = await request.json()
    status = data.get("status")
    if status not in ("ignored", "unmatched"):
        raise HTTPException(status_code=400, detail="status debe ser ignored o unmatched")
    res = await get_db().bank_transactions.update_one(
        {"transaction_id": transaction_id},
        {"$set": {"match": {"status": status, "manual": True,
                            "updated_at": datetime.utcnow()}}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")
    return {"success": True}


@router.post('/admin/plaid/reconcile')
async def reconcile(request: Request):
    """Re-ejecuta el auto-match sobre las no conciliadas."""
    await auth_admin(request)
    matched = await _auto_match()
    return {"success": True, "auto_matched": matched}
