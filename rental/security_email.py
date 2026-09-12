"""Best-effort account security email notifications."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo


logger = logging.getLogger("security_email")
SUPPORT_EMAIL = "info@rosshouserentals.com"
SUPPORT_PHONE = "(806) 934-2018"
SECURITY_FROM_NAME = "Ross House Security"
CENTRAL_TIME = ZoneInfo("America/Chicago")
SPANISH_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _format_changed_at(changed_at: datetime) -> str:
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=timezone.utc)
    local = changed_at.astimezone(CENTRAL_TIME)
    hour = local.strftime("%I").lstrip("0") or "12"
    minute = local.strftime("%M")
    period = "a. m." if local.hour < 12 else "p. m."
    return (
        f"{local.day} de {SPANISH_MONTHS[local.month - 1]} de {local.year}, "
        f"{hour}:{minute} {period} {local.tzname()}"
    )


def build_password_changed_message(name: str, changed_at: datetime) -> dict:
    """Build the Spanish notification without ever receiving the password."""
    display_name = escape((name or "").strip()) or "cliente"
    timestamp = _format_changed_at(changed_at)
    subject = "Tu contraseña de Ross House Rentals fue cambiada"
    text = (
        f"Hola {name.strip() or 'cliente'},\n\n"
        f"La contraseña de tu cuenta fue cambiada el {timestamp}. "
        "Por seguridad, cerramos las demás sesiones de tu cuenta.\n\n"
        "Si tú hiciste este cambio, no necesitas hacer nada. "
        f"Si no lo reconoces, contáctanos de inmediato en {SUPPORT_EMAIL} "
        f"o al {SUPPORT_PHONE}.\n\n"
        "Ross House Rentals"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#202124">
      <h2 style="color:#c8102e">Contraseña actualizada</h2>
      <p>Hola {display_name},</p>
      <p>La contraseña de tu cuenta fue cambiada el <strong>{timestamp}</strong>.</p>
      <p>Por seguridad, cerramos las demás sesiones de tu cuenta.</p>
      <p>Si tú hiciste este cambio, no necesitas hacer nada.</p>
      <div style="background:#fff3f5;border-left:4px solid #c8102e;padding:16px;margin:20px 0">
        <strong>¿No reconoces este cambio?</strong><br>
        Contáctanos de inmediato en <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>
        o al <a href="tel:+18069342018">{SUPPORT_PHONE}</a>.
      </div>
      <p style="color:#687080;font-size:13px">Este correo nunca incluye tu contraseña.</p>
      <p>Ross House Rentals</p>
    </div>
    """.strip()
    return {"subject": subject, "text": text, "html": html}


def build_maintenance_received_message(
    *,
    name: str,
    request_id: str,
    title: str,
    property_address: str,
    category: str,
    priority: str,
    photo_count: int,
    submitted_at: datetime,
) -> dict:
    """Build a bilingual receipt without embedding maintenance evidence."""
    display_name = escape(" ".join(str(name or "").split()) or "cliente")
    safe_id = escape(" ".join(str(request_id or "").split()))
    safe_title = escape(" ".join(str(title or "").split()))
    safe_address = escape(" ".join(str(property_address or "").split()) or "No indicada")
    safe_category = escape(" ".join(str(category or "").split()) or "general")
    safe_priority = escape(" ".join(str(priority or "").split()) or "normal")
    safe_photo_count = max(0, min(int(photo_count or 0), 5))
    timestamp = _format_changed_at(submitted_at)
    subject = f"Recibimos tu solicitud de mantenimiento #{safe_id}"
    text = (
        f"Hola {' '.join(str(name or '').split()) or 'cliente'},\n\n"
        "Recibimos tu solicitud de mantenimiento.\n"
        f"Número de solicitud: {request_id}\n"
        f"Título: {' '.join(str(title or '').split())}\n"
        f"Propiedad: {' '.join(str(property_address or '').split()) or 'No indicada'}\n"
        f"Categoría: {' '.join(str(category or '').split()) or 'general'}\n"
        f"Prioridad: {' '.join(str(priority or '').split()) or 'normal'}\n"
        f"Fotos recibidas: {safe_photo_count}\n"
        f"Fecha: {timestamp}\n\n"
        "Conserva este número para dar seguimiento. Te avisaremos cuando cambie el estado.\n\n"
        "We received your maintenance request and will notify you when its status changes.\n\n"
        f"¿Necesitas ayuda? {SUPPORT_EMAIL} · {SUPPORT_PHONE}\n"
        "Ross House Rentals"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#202124">
      <div style="background:#202124;color:white;padding:22px;border-bottom:4px solid #c8102e">
        <h2 style="margin:0">Solicitud de mantenimiento recibida</h2>
        <p style="margin:6px 0 0">Maintenance request received</p>
      </div>
      <div style="padding:24px">
        <p>Hola {display_name},</p>
        <p>Recibimos tu solicitud y la registramos correctamente.</p>
        <div style="background:#f7f7f8;border-radius:10px;padding:16px;line-height:1.7">
          <strong>Número:</strong> {safe_id}<br>
          <strong>Título:</strong> {safe_title}<br>
          <strong>Propiedad:</strong> {safe_address}<br>
          <strong>Categoría:</strong> {safe_category}<br>
          <strong>Prioridad:</strong> {safe_priority}<br>
          <strong>Fotos recibidas:</strong> {safe_photo_count}<br>
          <strong>Fecha:</strong> {timestamp}
        </div>
        <p>Conserva este número para dar seguimiento. Te avisaremos cuando cambie el estado.</p>
        <p style="color:#687080">We received your maintenance request and will notify you when its status changes.</p>
        <p>Ayuda: <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> ·
        <a href="tel:+18069342018">{SUPPORT_PHONE}</a></p>
        <p>Ross House Rentals</p>
      </div>
    </div>
    """.strip()
    return {"subject": subject, "text": text, "html": html}


async def _sendgrid_config(db) -> tuple[str, str]:
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    from_email = (
        os.getenv("SECURITY_FROM_EMAIL")
        or os.getenv("SENDGRID_FROM_EMAIL")
        or SUPPORT_EMAIL
    ).strip()
    if not api_key:
        try:
            config = await db.api_config.find_one({"_id": "main"}) or {}
            api_key = str(
                config.get("sendgrid_api_key") or config.get("SENDGRID_API_KEY") or ""
            ).strip()
            from_email = str(
                config.get("security_from_email")
                or config.get("SECURITY_FROM_EMAIL")
                or config.get("sendgrid_from_email")
                or config.get("SENDGRID_FROM_EMAIL")
                or from_email
            ).strip()
        except Exception:
            logger.exception("Could not load SendGrid configuration")
    return api_key, from_email or SUPPORT_EMAIL


async def send_password_changed_email(
    db, *, to_email: str, name: str, changed_at: datetime
) -> bool:
    """Send a security notice; failures never undo the password change."""
    recipient = (to_email or "").strip()
    if not recipient:
        logger.warning("Password-change email skipped: user has no email")
        return False

    api_key, from_email = await _sendgrid_config(db)
    if not api_key:
        logger.warning("Password-change email skipped: SendGrid is not configured")
        return False

    content = build_password_changed_message(name, changed_at)

    def _send() -> bool:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=(from_email, SECURITY_FROM_NAME),
            to_emails=recipient,
            subject=content["subject"],
            plain_text_content=content["text"],
            html_content=content["html"],
        )
        response = SendGridAPIClient(api_key).send(message)
        return 200 <= int(response.status_code) < 300

    try:
        return await asyncio.to_thread(_send)
    except Exception:
        logger.exception("Password-change security email failed")
        return False


async def send_maintenance_received_email(
    db,
    *,
    to_email: str,
    name: str,
    request_id: str,
    title: str,
    property_address: str,
    category: str,
    priority: str,
    photo_count: int,
    submitted_at: datetime,
) -> bool:
    """Send a best-effort tenant receipt; failures never undo the ticket."""
    recipient = (to_email or "").strip()
    if not recipient:
        logger.warning("Maintenance receipt skipped: tenant has no email")
        return False

    api_key, from_email = await _sendgrid_config(db)
    if not api_key:
        logger.warning("Maintenance receipt skipped: SendGrid is not configured")
        return False

    content = build_maintenance_received_message(
        name=name,
        request_id=request_id,
        title=title,
        property_address=property_address,
        category=category,
        priority=priority,
        photo_count=photo_count,
        submitted_at=submitted_at,
    )

    def _send() -> bool:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=(from_email, "Ross House Rentals"),
            to_emails=recipient,
            subject=content["subject"],
            plain_text_content=content["text"],
            html_content=content["html"],
        )
        response = SendGridAPIClient(api_key).send(message)
        return 200 <= int(response.status_code) < 300

    try:
        return await asyncio.to_thread(_send)
    except Exception:
        logger.exception("Maintenance receipt email failed")
        return False

