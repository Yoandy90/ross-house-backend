"""
Deal Finder Auto-Scan Cron — Radar automático del condado
==========================================================
Recorre TODO el condado de Moore de forma continua rotando prefijos de calle
(StreetName:a → StreetName:z → 0-9, el portal BIS hace prefix-match), procesando
un lote de propiedades por corrida (default 200/noche) para no saturar el portal.

Al terminar cada lote envía email de alerta si encontró:
  - NUEVAS oportunidades con señales fuertes (impuestos atrasados, o dueño
    ausente + terreno baldío/mejora baja), o
  - propiedades ya conocidas que SE VOLVIERON morosas desde el último ciclo.

Config:  app_settings {_id:'deal_finder_cron'}  {enabled, max_per_run, alert_email}
Estado:  app_settings {_id:'deal_finder_cron_state'}  {letter_idx, page, cycles,
         last_run, last_result}
Intervalo: env DEAL_FINDER_SCAN_INTERVAL_HOURS (default 24h).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

LETTERS = "abcdefghijklmnopqrstuvwxyz0123456789"
DEFAULT_MAX_PER_RUN = 200
DEFAULT_ALERT_EMAIL = "yoandyross@gmail.com"
ADMIN_URL = "https://www.rosshouserentals.com/admin/oportunidades"


def _is_strong(lead: dict) -> bool:
    s = set(lead.get("signals") or [])
    if "tax_delinquent" in s:
        return True
    return "absentee_owner" in s and bool(s & {"vacant_land", "low_improvement"})


def _fmt_lead(lead: dict) -> str:
    tags = {
        "tax_delinquent": "impuestos atrasados",
        "absentee_owner": "dueño ausente",
        "out_of_state_owner": "fuera de TX",
        "vacant_land": "terreno baldío",
        "low_improvement": "mejora baja",
        "low_value": "valor bajo",
    }
    signals = ", ".join(tags.get(x, x) for x in lead.get("signals") or [])
    line = f"• {lead.get('address') or 'Cuenta #' + lead.get('property_id', '')} — {lead.get('owner_name', '').strip()}"
    if lead.get("tax_due_total"):
        line += f" — debe ${lead['tax_due_total']:,.2f}"
    if signals:
        line += f" [{signals}]"
    return line


async def send_alert_email(db, new_opps: list[dict], became_delinquent: list[dict]) -> bool:
    cfg = await db.app_settings.find_one({"_id": "deal_finder_cron"}) or {}
    to = cfg.get("alert_email") or DEFAULT_ALERT_EMAIL
    total = len(new_opps) + len(became_delinquent)
    subject = f"🎯 Radar de Oportunidades: {total} hallazgo(s) en Moore County"

    parts = []
    if new_opps:
        parts.append(f"NUEVAS OPORTUNIDADES ({len(new_opps)}):")
        parts += [_fmt_lead(x) for x in new_opps[:15]]
        if len(new_opps) > 15:
            parts.append(f"  ...y {len(new_opps) - 15} más")
    if became_delinquent:
        parts.append("")
        parts.append(f"SE VOLVIERON MOROSAS ({len(became_delinquent)}):")
        parts += [_fmt_lead(x) for x in became_delinquent[:10]]
    parts.append("")
    parts.append(f"Ver todas en el panel: {ADMIN_URL}")
    body = "\n".join(parts)

    try:
        from rental.email_inbox_router import _send_via_sendgrid
        ok = await _send_via_sendgrid(to, subject, body)
        if ok:
            logger.info(f"[deal-finder-cron] alerta enviada a {to} ({total} hallazgos)")
        return ok
    except Exception as e:
        logger.error(f"[deal-finder-cron] fallo enviando alerta: {e}")
        return False


async def run_auto_scan_batch(db, max_props: int | None = None) -> dict:
    """Procesa un lote del recorrido a-z del condado. Reanuda donde quedó."""
    from rental.deal_finder_router import (
        COUNTIES, UA, SKIP_TYPES,
        _open_search_session, _search_page, enrich_and_upsert,
    )
    county = "moore"
    base = COUNTIES[county]["base"]

    cfg = await db.app_settings.find_one({"_id": "deal_finder_cron"}) or {}
    if max_props is None:
        max_props = int(cfg.get("max_per_run") or DEFAULT_MAX_PER_RUN)

    state = await db.app_settings.find_one({"_id": "deal_finder_cron_state"}) or {}
    letter_idx = int(state.get("letter_idx") or 0) % len(LETTERS)
    page = int(state.get("page") or 1)
    cycles = int(state.get("cycles") or 0)

    processed = new_count = updated_count = 0
    new_opps: list[dict] = []
    became: list[dict] = []
    letters_visited = 0

    async with httpx.AsyncClient(timeout=40, headers=UA, follow_redirects=True) as client:
        while processed < max_props and letters_visited <= len(LETTERS):
            letter = LETTERS[letter_idx]
            keywords = f"StreetName:{letter}"
            try:
                token = await _open_search_session(client, base, keywords)
                data = await _search_page(client, base, keywords, token, page, 25)
            except Exception as e:
                logger.warning(f"[deal-finder-cron] búsqueda '{keywords}' p{page} falló: {e}")
                await asyncio.sleep(5)
                break

            items = [r for r in (data.get("resultsList") or [])
                     if (r.get("propertyTypeCode") or "").upper() not in SKIP_TYPES]
            total_pages = data.get("totalPages") or 1

            interrupted = False
            for item in items:
                if processed >= max_props:
                    interrupted = True
                    break
                outcome, lead, became_delinquent = await enrich_and_upsert(
                    db, client, base, county, item)
                processed += 1
                if outcome == "new":
                    new_count += 1
                    if _is_strong(lead):
                        new_opps.append(lead)
                elif outcome == "updated":
                    updated_count += 1
                if became_delinquent:
                    became.append(lead)

            # avanzar cursor (si el lote se llenó a mitad de página, la repetimos
            # en la próxima corrida — los upserts son idempotentes)
            if interrupted:
                pass
            elif page < total_pages:
                page += 1
            else:
                letter_idx = (letter_idx + 1) % len(LETTERS)
                page = 1
                letters_visited += 1
                if letter_idx == 0:
                    cycles += 1  # vuelta completa al condado

            await db.app_settings.update_one(
                {"_id": "deal_finder_cron_state"},
                {"$set": {"letter_idx": letter_idx, "page": page, "cycles": cycles}},
                upsert=True,
            )
            await asyncio.sleep(1.0)

    result = {
        "processed": processed, "new": new_count, "updated": updated_count,
        "strong_new": len(new_opps), "became_delinquent": len(became),
        "next_letter": LETTERS[letter_idx], "cycles": cycles,
    }

    alerted = False
    if new_opps or became:
        alerted = await send_alert_email(db, new_opps, became)
    result["alerted"] = alerted

    await db.app_settings.update_one(
        {"_id": "deal_finder_cron_state"},
        {"$set": {"last_run": datetime.now(timezone.utc), "last_result": result}},
        upsert=True,
    )
    logger.info(f"[deal-finder-cron] lote listo: {result}")
    return result


async def deal_finder_scan_loop():
    interval = float(os.environ.get("DEAL_FINDER_SCAN_INTERVAL_HOURS", "24")) * 3600
    await asyncio.sleep(180)  # esperar arranque completo
    from rental.shared import get_db
    while True:
        try:
            db = get_db()
            cfg = await db.app_settings.find_one({"_id": "deal_finder_cron"}) or {}
            if cfg.get("enabled", True) is False:
                logger.info("[deal-finder-cron] deshabilitado — omitido")
            else:
                await run_auto_scan_batch(db)
        except Exception as e:
            logger.error(f"[deal-finder-cron] error: {e}")
        await asyncio.sleep(interval)
