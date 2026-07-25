"""
Re-engagement email cron for App Adoption.

Runs continuously; every hour checks if it's the configured weekday+hour
(America/Chicago timezone). If yes and idempotency marker hasn't fired
this week, sends the re-engagement email campaign.

Config is stored in `app_settings` document with _id='app_adoption_reengagement'.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone.utc

# Check every 30 min (guarantees we catch the target hour even with slight drift)
CHECK_INTERVAL_SECONDS = 30 * 60


async def _should_run_now(db) -> bool:
    """
    Returns True if:
      - Config enabled
      - Current CT weekday+hour matches config
      - We haven't run this week already
    """
    cfg = await db.app_settings.find_one({"_id": "app_adoption_reengagement"}) or {}
    if not cfg.get("enabled", True):
        return False

    target_weekday = int(cfg.get("weekday", 0))  # 0=Mon
    target_hour = int(cfg.get("hour_ct", 10))

    now_ct = datetime.now(CT)
    if now_ct.weekday() != target_weekday:
        return False
    if now_ct.hour != target_hour:
        return False

    last_run = cfg.get("last_run_at")
    if last_run and isinstance(last_run, datetime):
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        # Skip if last run was less than 6 days ago (avoid dup within same day)
        if (datetime.now(timezone.utc) - last_run) < timedelta(days=6):
            return False

    return True


async def reengagement_loop():
    """Background task — runs forever, checks every 30 min."""
    from rental.shared import get_db
    from rental.app_adoption_router import run_reengagement_campaign
    logging.info("🚀 Re-engagement email cron started (checks every 30 min)")

    while True:
        try:
            db = get_db()
            if db is not None and await _should_run_now(db):
                logging.info("📧 Re-engagement cron: firing weekly campaign")
                result = await run_reengagement_campaign(db, force=False)
                logging.info(f"📧 Re-engagement cron done: {result}")
        except Exception as e:
            logging.exception(f"Re-engagement loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
