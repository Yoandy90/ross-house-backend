"""Cron de sincronización bancaria Plaid + alertas de movimientos grandes sin conciliar.

- Corre cada PLAID_SYNC_INTERVAL_HOURS (default 24h), con delay inicial de 2 min.
- Si no hay cuentas vinculadas o faltan credenciales Plaid, no hace nada.
- Tras cada sync: busca bank_transactions 'unmatched' con |monto| >= umbral
  (app_settings {_id:'plaid_alerts'}.threshold, default $500) que no hayan sido
  alertadas antes → envía email al admin y las marca alerted=true.
"""
import asyncio
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

ADMIN_EMAIL = "yoandyross@gmail.com"
DEFAULT_THRESHOLD = 500.0


async def check_large_unmatched(db) -> int:
    """Alerta por email sobre movimientos grandes sin conciliar. Devuelve # alertados."""
    cfg = await db.app_settings.find_one({"_id": "plaid_alerts"}) or {}
    threshold = float(cfg.get("threshold") or DEFAULT_THRESHOLD)
    txs = await (db.bank_transactions.find({
        "match.status": "unmatched",
        "alerted": {"$ne": True},
        "$or": [{"amount": {"$gte": threshold}}, {"amount": {"$lte": -threshold}}],
    }).sort([("date", -1)]).limit(20).to_list(20))
    if not txs:
        return 0

    lines = []
    for t in txs:
        d = t.get("date")
        d = d.strftime("%Y-%m-%d") if isinstance(d, datetime) else str(d or "")[:10]
        direction = "SALIÓ" if float(t.get("amount") or 0) > 0 else "ENTRÓ"
        lines.append(f"• {d} — {t.get('name','')[:50]} — {direction} "
                     f"${abs(float(t.get('amount') or 0)):,.2f}")
    body = (f"Alerta de conciliación bancaria 🏦\n\n"
            f"Se detectaron {len(txs)} movimiento(s) de ${threshold:,.0f}+ "
            f"que NO cruzan con ninguna renta, gasto o pago registrado:\n\n"
            + "\n".join(lines) +
            "\n\nRevisa y concilia en: https://www.rosshouserentals.com/admin/banco\n"
            "— Ross House Rentals (sync automático)")
    try:
        from rental.email_inbox_router import _send_via_sendgrid
        ok = await _send_via_sendgrid(
            ADMIN_EMAIL,
            f"🏦 {len(txs)} movimiento(s) bancario(s) grande(s) sin conciliar",
            body)
        if not ok:
            return 0
    except Exception as e:
        logger.error(f"[plaid-cron] error enviando alerta: {e}")
        return 0
    await db.bank_transactions.update_many(
        {"_id": {"$in": [t["_id"] for t in txs]}},
        {"$set": {"alerted": True, "alerted_at": datetime.utcnow()}})
    logger.info(f"[plaid-cron] alerta enviada: {len(txs)} movimientos grandes")
    return len(txs)


async def plaid_sync_loop():
    interval = float(os.environ.get("PLAID_SYNC_INTERVAL_HOURS", "24")) * 3600
    await asyncio.sleep(120)  # esperar arranque completo
    from rental.shared import get_db
    while True:
        try:
            db = get_db()
            if not os.environ.get("PLAID_CLIENT_ID"):
                logger.info("[plaid-cron] sin credenciales Plaid — omitido")
            elif await db.plaid_items.count_documents({}) == 0:
                logger.info("[plaid-cron] sin cuentas vinculadas — omitido")
            else:
                from rental.plaid_router import run_full_sync
                result = await run_full_sync()
                alerted = await check_large_unmatched(db)
                logger.info(f"[plaid-cron] sync: {result.get('imported')} importadas, "
                            f"{result.get('auto_matched')} conciliadas, {alerted} alertadas")
        except Exception as e:
            logger.error(f"[plaid-cron] error: {e}")
        await asyncio.sleep(interval)
