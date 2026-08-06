"""
Drip cron — envía la siguiente plantilla activa según la frecuencia configurada.
per_week: 1 → martes · 2 → martes y viernes · 3 → lunes, miércoles y viernes.
Hora: config hour_ct (default 9am, America/Chicago). Chequea cada 30 min.
Idempotente: no envía dos veces el mismo día (last_sent_at).
"""
import asyncio
import logging
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone.utc

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 30 * 60
SEND_DAYS = {1: [1], 2: [1, 4], 3: [0, 2, 4]}  # weekday(): lunes=0


async def drip_tick(db) -> bool:
    """Un chequeo: envía si toca. Devuelve True si envió."""
    cfg = await db.app_settings.find_one({"_id": "drip"}) or {}
    if cfg.get("enabled", True) is False:
        return False
    per_week = int(cfg.get("per_week") or 2)
    hour_ct = int(cfg.get("hour_ct") or 9)
    now_ct = datetime.now(CT)

    if now_ct.weekday() not in SEND_DAYS.get(per_week, [1, 4]):
        return False
    if now_ct.hour < hour_ct:
        return False
    last = cfg.get("last_sent_at")
    if last and last.replace(tzinfo=timezone.utc).astimezone(CT).date() == now_ct.date():
        return False  # ya se envió hoy

    tpl = await db.email_templates.find_one(
        {"status": "active", "sent_at": None}, sort=[("created_at", 1)])
    if not tpl:
        logger.info("[drip] cola vacía — no hay plantillas pendientes")
        return False

    from rental.drip_router import send_template_now
    result = await send_template_now(tpl)
    await db.app_settings.update_one(
        {"_id": "drip"}, {"$set": {"last_sent_at": datetime.utcnow()}}, upsert=True)
    logger.info(f"[drip] enviada '{tpl.get('subject_es')}' a {result.get('sent')} suscriptores")
    return True


async def drip_loop():
    await asyncio.sleep(240)
    from rental.shared import get_db
    while True:
        try:
            await drip_tick(get_db())
        except Exception as e:
            logger.error(f"[drip] error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)
