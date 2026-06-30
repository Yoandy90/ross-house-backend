"""Jasmine Apartments — Email engagement tracker.

Polls SendGrid Activity API for `open`, `click`, `bounce`, `dropped` events on
the specific message sent to joe3359@gmail.com and notifies Yoandy via email
whenever a NEW event is detected.

State is persisted in MongoDB collection `email_trackers` so the same event is
never notified twice (idempotent).

Designed to run as a GitHub Actions cron every 30 minutes.

Usage:
    python scripts/jasmine_email_tracker.py

Environment required:
    SENDGRID_API_KEY    — SendGrid API key
    SENDGRID_FROM_EMAIL — Sender email (verified)
    MONGO_URL           — MongoDB connection string
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()
# Fallback to ross-house-backend/.env when running standalone
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from pymongo import MongoClient
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ─── CONFIG ───
TRACKED_MESSAGE = {
    "to_email":   "joe3359@gmail.com",
    "subject":    "Interest in Jasmine Apartments — a few questions (Ross House Rentals)",
    "sent_at":    "2026-06-30T16:39:01Z",
    "tracker_id": "jasmine_kuruvila_first_inquiry_2026_06_30",
}
NOTIFY_TO    = "yoandyross@gmail.com"
COLL_NAME    = "email_trackers"

API_KEY  = os.environ["SENDGRID_API_KEY"]
FROM_EM  = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
MONGO    = os.environ["MONGO_URL"]


def fetch_events() -> List[Dict[str, Any]]:
    """Query SendGrid Activity API for all events on the tracked email."""
    # Search by from/to/subject — narrow enough to be unique
    query = (
        f'to_email LIKE "%{TRACKED_MESSAGE["to_email"]}%" AND '
        f'subject LIKE "%Interest in Jasmine Apartments%"'
    )
    r = requests.get(
        "https://api.sendgrid.com/v3/messages",
        headers={"Authorization": f"Bearer {API_KEY}"},
        params={"limit": 10, "query": query},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("messages", [])


def format_event_label(status: str, lang_emoji: bool = True) -> str:
    labels = {
        "processed":     "📤 Procesado por SendGrid",
        "delivered":     "📬 Entregado en bandeja",
        "open":          "👀 ABIERTO — Joe vio el email",
        "click":         "🔗 CLICK — Joe hizo clic en un enlace",
        "bounce":        "❌ BOUNCE — email rebotó",
        "blocked":       "🚫 BLOCKED — bloqueado por filtro",
        "deferred":      "⏳ DEFERRED — retraso temporal",
        "dropped":       "💥 DROPPED — no se pudo entregar",
        "spamreport":    "⚠️ SPAM — marcado como spam",
        "unsubscribe":   "🛑 UNSUBSCRIBED",
        "group_unsubscribe": "🛑 GROUP UNSUBSCRIBED",
    }
    return labels.get(status, f"❓ {status.upper()}")


def build_alert_html(state_msg: Dict[str, Any], new_status: str) -> str:
    label  = format_event_label(new_status)
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    opens  = state_msg.get("opens_count", 0)
    clicks = state_msg.get("clicks_count", 0)
    is_important = new_status in ("open", "click")
    accent = "#10b981" if is_important else "#64748b"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#0f172a;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9;padding:24px 12px;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
      <!-- Header navy -->
      <tr><td bgcolor="#0d1a2e" style="background:#0d1a2e;padding:28px 32px;">
        <div style="display:inline-block;background:#3b2a06;border:1px solid #b45309;padding:5px 12px;border-radius:999px;">
          <span style="color:#fbbf24;font-size:11px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;">Jasmine Apartments · Email Tracker</span>
        </div>
        <div style="margin-top:10px;color:#ffffff;font-size:22px;font-weight:800;">🏢 Ross House Rentals</div>
      </td></tr>

      <!-- Content -->
      <tr><td style="padding:32px 32px 16px 32px;">
        <div style="font-size:13px;font-weight:700;color:{accent};text-transform:uppercase;letter-spacing:1px;">Nuevo evento detectado</div>
        <h1 style="font-size:26px;font-weight:800;color:#0f172a;margin:8px 0 4px 0;line-height:1.25;">{label}</h1>
        <div style="font-size:14px;color:#64748b;margin-bottom:22px;">{ts}</div>

        <div style="padding:18px 20px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;font-size:14px;line-height:1.85;">
          <div><strong style="color:#64748b;display:inline-block;width:120px;">Destinatario:</strong> Joe Kuruvila &lt;joe3359@gmail.com&gt;</div>
          <div><strong style="color:#64748b;display:inline-block;width:120px;">Asunto:</strong> Interest in Jasmine Apartments — a few questions</div>
          <div><strong style="color:#64748b;display:inline-block;width:120px;">Enviado:</strong> 30-Jun-2026 16:39 UTC</div>
          <div><strong style="color:#64748b;display:inline-block;width:120px;">Aperturas:</strong> <span style="color:#0f172a;font-weight:700;">{opens}</span></div>
          <div><strong style="color:#64748b;display:inline-block;width:120px;">Clicks:</strong> <span style="color:#0f172a;font-weight:700;">{clicks}</span></div>
        </div>

        {"<div style='margin-top:20px;padding:16px 18px;background:#ecfdf5;border-left:4px solid #10b981;border-radius:8px;font-size:14px;color:#065f46;line-height:1.6;'><strong>🎯 ¿Qué significa esto?</strong><br/>Joe abrió tu email " + ("y" if clicks > 0 else "") + " hizo clic en un enlace. Está en su radar — espera respuesta en los próximos días o, si quieres acelerar, ahora es buen momento para mandar el follow-up corto en 24-48h.</div>" if is_important else "<div style='margin-top:20px;padding:14px 16px;background:#fffbeb;border-left:4px solid #f59e0b;border-radius:8px;font-size:13px;color:#78350f;line-height:1.6;'>Evento técnico de SendGrid. No requiere acción.</div>"}
      </td></tr>

      <!-- Footer -->
      <tr><td bgcolor="#f8fafc" style="background:#f8fafc;padding:24px 32px;border-top:1px solid #e2e8f0;">
        <div style="font-size:12px;color:#64748b;line-height:1.6;text-align:center;">
          <strong style="color:#0f172a;">Ross House Rentals</strong> · Dumas, TX · (806) 934-2018<br>
          <a href="https://www.rosshouserentals.com" style="color:#d97706;text-decoration:none;">www.rosshouserentals.com</a><br>
          <span style="display:inline-block;margin-top:8px;color:#94a3b8;font-size:11px;">Notificación automática del tracker de email de Jasmine Apartments. Para silenciar, elimina el documento <code>{TRACKED_MESSAGE["tracker_id"]}</code> de la colección <code>email_trackers</code>.</span>
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def send_alert(state_msg: Dict[str, Any], new_status: str) -> int:
    """Send Yoandy a notification email for a new event."""
    msg = Mail(
        from_email=(FROM_EM, "Jasmine Tracker · Ross House"),
        to_emails=NOTIFY_TO,
        subject=f"🎯 Jasmine Tracker: {format_event_label(new_status)}",
        html_content=build_alert_html(state_msg, new_status),
        plain_text_content=f"Nuevo evento en el email a Joe Kuruvila: {new_status}",
    )
    return SendGridAPIClient(API_KEY).send(msg).status_code


def main():
    client = MongoClient(MONGO)
    db = client.get_default_database()
    coll = db[COLL_NAME]

    # Load previous state for this tracker
    state = coll.find_one({"tracker_id": TRACKED_MESSAGE["tracker_id"]}) or {
        "tracker_id":     TRACKED_MESSAGE["tracker_id"],
        "to_email":       TRACKED_MESSAGE["to_email"],
        "last_status":    "delivered",
        "last_opens":     0,
        "last_clicks":    0,
        "last_event_ts":  None,
        "notifications_sent": 0,
    }

    # Fetch current state from SendGrid
    messages = fetch_events()
    msg = next(
        (m for m in messages if m.get("to_email") == TRACKED_MESSAGE["to_email"]),
        None,
    )
    if not msg:
        print(f"⚠️ No SendGrid record found for {TRACKED_MESSAGE['to_email']}")
        return

    cur_status = msg.get("status", "unknown")
    cur_opens  = msg.get("opens_count", 0)
    cur_clicks = msg.get("clicks_count", 0)
    cur_ts     = msg.get("last_event_time")

    print(f"📊 Current SendGrid state: status={cur_status} opens={cur_opens} clicks={cur_clicks}")
    print(f"📊 Last known state:       status={state['last_status']} opens={state['last_opens']} clicks={state['last_clicks']}")

    # Detect new events
    new_event = None
    if cur_status != state["last_status"]:
        new_event = cur_status  # status changed (e.g. delivered → open)
    elif cur_opens > state["last_opens"]:
        new_event = "open"
    elif cur_clicks > state["last_clicks"]:
        new_event = "click"

    if new_event:
        print(f"🚨 NEW EVENT DETECTED: {new_event}")
        status = send_alert(msg, new_event)
        print(f"📧 Alert sent to {NOTIFY_TO} — SendGrid status {status}")

        # Update state in Mongo
        coll.update_one(
            {"tracker_id": TRACKED_MESSAGE["tracker_id"]},
            {
                "$set": {
                    "tracker_id":     TRACKED_MESSAGE["tracker_id"],
                    "to_email":       TRACKED_MESSAGE["to_email"],
                    "last_status":    cur_status,
                    "last_opens":     cur_opens,
                    "last_clicks":    cur_clicks,
                    "last_event_ts":  cur_ts,
                    "last_check_ts":  datetime.now(timezone.utc).isoformat(),
                    "last_event":     new_event,
                },
                "$inc": {"notifications_sent": 1},
            },
            upsert=True,
        )
        print(f"💾 State persisted")
    else:
        print(f"✓ No new events. Tracker still watching.")
        coll.update_one(
            {"tracker_id": TRACKED_MESSAGE["tracker_id"]},
            {
                "$set": {
                    "last_check_ts": datetime.now(timezone.utc).isoformat(),
                    "to_email":      TRACKED_MESSAGE["to_email"],
                    "last_status":   cur_status,
                    "last_opens":    cur_opens,
                    "last_clicks":   cur_clicks,
                    "last_event_ts": cur_ts,
                },
            },
            upsert=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ ERROR: {e}", file=sys.stderr)
        sys.exit(1)
