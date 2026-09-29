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
TRANSACTIONAL_FROM_NAME = "Ross House Rentals"
CENTRAL_TIME = ZoneInfo("America/Chicago")
SPANISH_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

_MAINTENANCE_CATEGORY_LABELS = {
    "es": {
        "plumbing": "Plomería", "electrical": "Eléctrico",
        "appliance": "Electrodoméstico", "hvac": "A/C - Calefacción",
        "structural": "Estructura", "pest": "Plagas",
        "cleaning": "Limpieza", "other": "Otro", "general": "General",
    },
    "en": {
        "plumbing": "Plumbing", "electrical": "Electrical",
        "appliance": "Appliance", "hvac": "HVAC",
        "structural": "Structural", "pest": "Pest control",
        "cleaning": "Cleaning", "other": "Other", "general": "General",
    },
}
_MAINTENANCE_PRIORITY_LABELS = {
    "es": {"low": "Baja", "normal": "Normal", "medium": "Media",
           "high": "Alta", "urgent": "Urgente"},
    "en": {"low": "Low", "normal": "Normal", "medium": "Medium",
           "high": "High", "urgent": "Urgent"},
}


def _localized_maintenance_label(labels: dict, value: str, locale: str,
                                  fallback: str) -> str:
    key = " ".join(str(value or "").split()).lower() or fallback
    return labels[locale].get(key, key.replace("_", " ").title())


def _normalize_locale(locale: str) -> str:
    return "en" if str(locale or "").strip().lower().startswith("en") else "es"


def _format_changed_at(changed_at: datetime, locale: str = "es") -> str:
    if changed_at.tzinfo is None:
        changed_at = changed_at.replace(tzinfo=timezone.utc)
    local = changed_at.astimezone(CENTRAL_TIME)
    if _normalize_locale(locale) == "en":
        return local.strftime("%B %-d, %Y, %-I:%M %p %Z")
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


def build_autopay_changed_message(
    *,
    name: str,
    change_type: str,
    method_brand: str,
    method_last4: str,
    day_of_month: int,
    changed_at: datetime,
    locale: str = "es",
) -> dict:
    """Build a localized autopay notice using masked display data only."""
    locale = _normalize_locale(locale)
    fallback_name = "customer" if locale == "en" else "cliente"
    raw_name = " ".join(str(name or "").split()) or fallback_name
    safe_name = escape(raw_name)
    safe_brand = escape(" ".join(str(method_brand or "").split())[:40])
    safe_last4 = escape(str(method_last4 or "")[-4:])
    safe_day = max(1, min(28, int(day_of_month or 1)))
    timestamp = _format_changed_at(changed_at, locale)
    method = f"{safe_brand} ••••{safe_last4}" if safe_last4 else safe_brand

    if locale == "en":
        labels = {
            "activated": ("Automatic payments activated", "Automatic payments are now active."),
            "deactivated": ("Automatic payments deactivated", "Automatic payments have been turned off."),
            "updated": ("Automatic payment settings updated", "Your automatic payment settings were updated."),
        }
        subject, summary = labels.get(change_type, labels["updated"])
        detail_lines = []
        detail_html = ""
        if change_type != "deactivated":
            detail_lines = [f"Payment day: day {safe_day} of each month"]
            detail_html = f"<strong>Payment day:</strong> day {safe_day} of each month<br>"
            if method:
                detail_lines.append(f"Payment method: {method}")
                detail_html += f"<strong>Payment method:</strong> {method}<br>"
        text = (
            f"Hello {raw_name},\n\n{summary}\n"
            + ("\n".join(detail_lines) + "\n" if detail_lines else "")
            + f"Date: {timestamp}\n\n"
            "You can review or change this setting in the Ross House Rentals app. "
            f"If you did not make this change, contact us at {SUPPORT_EMAIL} or {SUPPORT_PHONE}.\n\n"
            "Ross House Rentals"
        )
        date_label = "Date"
        help_text = "If you did not make this change, contact us immediately."
    else:
        labels = {
            "activated": ("Pagos automáticos activados", "Tus pagos automáticos quedaron activados."),
            "deactivated": ("Pagos automáticos desactivados", "Tus pagos automáticos fueron desactivados."),
            "updated": ("Configuración de pagos automáticos actualizada", "Actualizamos la configuración de tus pagos automáticos."),
        }
        subject, summary = labels.get(change_type, labels["updated"])
        detail_lines = []
        detail_html = ""
        if change_type != "deactivated":
            detail_lines = [f"Día de cobro: día {safe_day} de cada mes"]
            detail_html = f"<strong>Día de cobro:</strong> día {safe_day} de cada mes<br>"
            if method:
                detail_lines.append(f"Método de pago: {method}")
                detail_html += f"<strong>Método de pago:</strong> {method}<br>"
        text = (
            f"Hola {raw_name},\n\n{summary}\n"
            + ("\n".join(detail_lines) + "\n" if detail_lines else "")
            + f"Fecha: {timestamp}\n\n"
            "Puedes revisar o cambiar esta configuración desde la aplicación de Ross House Rentals. "
            f"Si no hiciste este cambio, contáctanos en {SUPPORT_EMAIL} o al {SUPPORT_PHONE}.\n\n"
            "Ross House Rentals"
        )
        date_label = "Fecha"
        help_text = "Si no hiciste este cambio, contáctanos inmediatamente."

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#202124">
      <div style="background:#202124;color:white;padding:22px;border-bottom:4px solid #c8102e">
        <h2 style="margin:0">{escape(subject)}</h2>
      </div>
      <div style="padding:24px">
        <p>{'Hello' if locale == 'en' else 'Hola'} {safe_name},</p>
        <p>{escape(summary)}</p>
        <div style="background:#f7f7f8;border-radius:10px;padding:16px;line-height:1.7">
          {detail_html}<strong>{date_label}:</strong> {escape(timestamp)}
        </div>
        <div style="background:#fff3f5;border-left:4px solid #c8102e;padding:16px;margin:20px 0">
          <strong>{help_text}</strong><br>
          <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> · {SUPPORT_PHONE}
        </div>
        <p style="color:#687080;font-size:13px">Ross House Rentals</p>
      </div>
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
    locale: str = "es",
) -> dict:
    """Build a localized receipt without embedding maintenance evidence."""
    locale = _normalize_locale(locale)
    fallback_name = "customer" if locale == "en" else "cliente"
    display_name = escape(" ".join(str(name or "").split()) or fallback_name)
    safe_id = escape(" ".join(str(request_id or "").split()))
    safe_title = escape(" ".join(str(title or "").split()))
    missing_address = "Not provided" if locale == "en" else "No indicada"
    safe_address = escape(" ".join(str(property_address or "").split()) or missing_address)
    raw_category = _localized_maintenance_label(
        _MAINTENANCE_CATEGORY_LABELS, category, locale, "general")
    raw_priority = _localized_maintenance_label(
        _MAINTENANCE_PRIORITY_LABELS, priority, locale, "normal")
    safe_category = escape(raw_category)
    safe_priority = escape(raw_priority)
    safe_photo_count = max(0, min(int(photo_count or 0), 5))
    timestamp = _format_changed_at(submitted_at, locale)
    raw_name = " ".join(str(name or "").split()) or fallback_name
    raw_title = " ".join(str(title or "").split())
    raw_address = " ".join(str(property_address or "").split()) or missing_address
    if locale == "en":
        subject = f"We received your maintenance request #{safe_id}"
        text = (
            f"Hello {raw_name},\n\n"
            "We received and recorded your maintenance request.\n"
            f"Request number: {request_id}\n"
            f"Title: {raw_title}\n"
            f"Property: {raw_address}\n"
            f"Category: {raw_category}\n"
            f"Priority: {raw_priority}\n"
            f"Photos received: {safe_photo_count}\n"
            f"Date: {timestamp}\n\n"
            "Keep this number for tracking. We will notify you when the status changes.\n\n"
            f"Need help? {SUPPORT_EMAIL} · {SUPPORT_PHONE}\nRoss House Rentals"
        )
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#202124">
          <div style="background:#202124;color:white;padding:22px;border-bottom:4px solid #c8102e">
            <h2 style="margin:0">Maintenance request received</h2>
          </div>
          <div style="padding:24px"><p>Hello {display_name},</p>
            <p>We received and recorded your maintenance request.</p>
            <div style="background:#f7f7f8;border-radius:10px;padding:16px;line-height:1.7">
              <strong>Number:</strong> {safe_id}<br><strong>Title:</strong> {safe_title}<br>
              <strong>Property:</strong> {safe_address}<br><strong>Category:</strong> {safe_category}<br>
              <strong>Priority:</strong> {safe_priority}<br><strong>Photos received:</strong> {safe_photo_count}<br>
              <strong>Date:</strong> {timestamp}
            </div>
            <p>Keep this number for tracking. We will notify you when the status changes.</p>
            <p>Help: <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> · <a href="tel:+18069342018">{SUPPORT_PHONE}</a></p>
            <p>Ross House Rentals</p>
          </div>
        </div>""".strip()
        return {"subject": subject, "text": text, "html": html}

    subject = f"Recibimos tu solicitud de mantenimiento #{safe_id}"
    text = (
        f"Hola {raw_name},\n\n"
        "Recibimos tu solicitud de mantenimiento.\n"
        f"Número de solicitud: {request_id}\n"
        f"Título: {' '.join(str(title or '').split())}\n"
        f"Propiedad: {raw_address}\n"
        f"Categoría: {raw_category}\n"
        f"Prioridad: {raw_priority}\n"
        f"Fotos recibidas: {safe_photo_count}\n"
        f"Fecha: {timestamp}\n\n"
        "Conserva este número para dar seguimiento. Te avisaremos cuando cambie el estado.\n\n"
        f"¿Necesitas ayuda? {SUPPORT_EMAIL} · {SUPPORT_PHONE}\n"
        "Ross House Rentals"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#202124">
      <div style="background:#202124;color:white;padding:22px;border-bottom:4px solid #c8102e">
        <h2 style="margin:0">Solicitud de mantenimiento recibida</h2>
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
        <p>Ayuda: <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> ·
        <a href="tel:+18069342018">{SUPPORT_PHONE}</a></p>
        <p>Ross House Rentals</p>
      </div>
    </div>
    """.strip()
    return {"subject": subject, "text": text, "html": html}


_STATUS_LABELS = {
    "es": {"pending": "Pendiente", "reviewing": "En revisión", "assigned": "Asignada", "scheduled": "Programada", "en_route": "En camino", "in_progress": "En progreso", "waiting_parts": "Esperando piezas", "completed": "Completada", "resolved": "Resuelta", "cancelled": "Cancelada", "closed": "Cerrada"},
    "en": {"pending": "Pending", "reviewing": "Under review", "assigned": "Assigned", "scheduled": "Scheduled", "en_route": "On the way", "in_progress": "In progress", "waiting_parts": "Waiting for parts", "completed": "Completed", "resolved": "Resolved", "cancelled": "Cancelled", "closed": "Closed"},
}


def maintenance_request_label(ticket: dict, locale: str = "es") -> str:
    """Human-facing reference; internal ids remain only in navigation data."""
    locale = _normalize_locale(locale)
    number = str(ticket.get("request_number") or "").strip()
    prefix = "Request" if locale == "en" else "Solicitud"
    # Legacy records can lack the sequential display number entirely.
    if number.isascii() and number.isdecimal() and len(number) <= 12:
        return f"{prefix} #{number.zfill(4)}"
    title = " ".join(str(ticket.get("title") or "").split())
    if title:
        return title if len(title) <= 100 else title[:99] + "…"
    return "Maintenance request" if locale == "en" else "Solicitud de mantenimiento"


def maintenance_update_push(ticket: dict, status: str, locale: str = "es") -> str:
    locale = _normalize_locale(locale)
    label = _STATUS_LABELS[locale].get(status, "Updated" if locale == "en" else "Actualizada")
    return f"{maintenance_request_label(ticket, locale)}: {label}"


def build_maintenance_updated_message(
    *, name: str, request_number: str, title: str, status: str,
    changed_at: datetime, locale: str = "es", assigned_to: str = "",
    scheduled_start: datetime | None = None, scheduled_end: datetime | None = None,
    tenant_visible_note: str = "",
) -> dict:
    locale = _normalize_locale(locale)
    fallback_name = "customer" if locale == "en" else "cliente"
    raw_name = " ".join(str(name or "").split()) or fallback_name
    safe_name = escape(raw_name)
    safe_number = escape(str(request_number or "").strip())
    safe_title = escape(" ".join(str(title or "").split()))
    safe_status = escape(_STATUS_LABELS[locale].get(status, status.replace("_", " ").title()))
    raw_assigned = " ".join(str(assigned_to or "").split())
    raw_note = " ".join(str(tenant_visible_note or "").split())
    safe_assigned = escape(raw_assigned)
    safe_note = escape(raw_note)
    schedule = _format_changed_at(scheduled_start, locale) if scheduled_start else ""
    if scheduled_end:
        schedule += (" – " if schedule else "") + _format_changed_at(scheduled_end, locale)
    changed = _format_changed_at(changed_at, locale)
    if locale == "en":
        subject = f"Maintenance request #{safe_number}: {safe_status}"
        lines = [f"Hello {raw_name},", "", f"Your maintenance request #{request_number} ({title}) was updated.", f"Status: {_STATUS_LABELS[locale].get(status, status)}"]
        details = f"<strong>Status:</strong> {safe_status}<br>"
        if safe_assigned:
            lines.append(f"Assigned to: {raw_assigned}"); details += f"<strong>Assigned to:</strong> {safe_assigned}<br>"
        if schedule:
            lines.append(f"Scheduled for: {schedule}"); details += f"<strong>Scheduled for:</strong> {schedule}<br>"
        if safe_note:
            lines.append(f"Note: {raw_note}"); details += f"<strong>Note:</strong> {safe_note}<br>"
        lines += [f"Updated: {changed}", "", f"Need help? {SUPPORT_EMAIL} · {SUPPORT_PHONE}", "Ross House Rentals"]
        heading, intro = "Maintenance request updated", f"Your request <strong>#{safe_number}</strong> ({safe_title}) was updated."
    else:
        subject = f"Solicitud de mantenimiento #{safe_number}: {safe_status}"
        lines = [f"Hola {raw_name},", "", f"Tu solicitud de mantenimiento #{request_number} ({title}) fue actualizada.", f"Estado: {_STATUS_LABELS[locale].get(status, status)}"]
        details = f"<strong>Estado:</strong> {safe_status}<br>"
        if safe_assigned:
            lines.append(f"Asignada a: {raw_assigned}"); details += f"<strong>Asignada a:</strong> {safe_assigned}<br>"
        if schedule:
            lines.append(f"Programada para: {schedule}"); details += f"<strong>Programada para:</strong> {schedule}<br>"
        if safe_note:
            lines.append(f"Nota: {raw_note}"); details += f"<strong>Nota:</strong> {safe_note}<br>"
        lines += [f"Actualizada: {changed}", "", f"¿Necesitas ayuda? {SUPPORT_EMAIL} · {SUPPORT_PHONE}", "Ross House Rentals"]
        heading, intro = "Solicitud de mantenimiento actualizada", f"Tu solicitud <strong>#{safe_number}</strong> ({safe_title}) fue actualizada."
    greeting = "Hello" if locale == "en" else "Hola"
    updated_label = "Updated" if locale == "en" else "Actualizada"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#202124"><div style="background:#202124;color:white;padding:22px;border-bottom:4px solid #c8102e"><h2 style="margin:0">{heading}</h2></div><div style="padding:24px"><p>{greeting} {safe_name},</p><p>{intro}</p><div style="background:#f7f7f8;border-radius:10px;padding:16px;line-height:1.7">{details}</div><p>{updated_label}: {changed}</p><p>Ross House Rentals</p></div></div>"""
    return {"subject": subject, "text": "\n".join(lines), "html": html}


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


async def _transactional_sendgrid_config(db) -> tuple[str, str]:
    """Load the ordinary customer-notification sender, never the security sender."""
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    from_email = (os.getenv("SENDGRID_FROM_EMAIL") or SUPPORT_EMAIL).strip()
    if not api_key:
        try:
            config = await db.api_config.find_one({"_id": "main"}) or {}
            api_key = str(
                config.get("sendgrid_api_key") or config.get("SENDGRID_API_KEY") or ""
            ).strip()
            from_email = str(
                config.get("sendgrid_from_email")
                or config.get("SENDGRID_FROM_EMAIL")
                or from_email
            ).strip()
        except Exception:
            logger.exception("Could not load transactional SendGrid configuration")
    return api_key, from_email or SUPPORT_EMAIL


async def send_autopay_changed_email(db, *, to_email: str, **kwargs) -> bool:
    """Send a best-effort autopay notice after the authorization is durable."""
    recipient = (to_email or "").strip()
    if not recipient:
        logger.warning("Autopay email skipped: tenant has no email")
        return False
    api_key, from_email = await _transactional_sendgrid_config(db)
    if not api_key:
        logger.warning("Autopay email skipped: SendGrid is not configured")
        return False
    content = build_autopay_changed_message(**kwargs)

    def _send() -> bool:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        response = SendGridAPIClient(api_key).send(Mail(
            from_email=(from_email, TRANSACTIONAL_FROM_NAME),
            to_emails=recipient,
            subject=content["subject"],
            plain_text_content=content["text"],
            html_content=content["html"],
        ))
        return 200 <= int(response.status_code) < 300

    try:
        return await asyncio.to_thread(_send)
    except Exception:
        logger.exception("Autopay notification email failed")
        return False


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
    locale: str = "es",
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
        locale=locale,
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


async def send_maintenance_updated_email(db, *, to_email: str, **kwargs) -> bool:
    """Send a best-effort localized workflow update."""
    recipient = (to_email or "").strip()
    if not recipient:
        return False
    api_key, from_email = await _sendgrid_config(db)
    if not api_key:
        logger.warning("Maintenance update email skipped: SendGrid is not configured")
        return False
    content = build_maintenance_updated_message(**kwargs)

    def _send() -> bool:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        response = SendGridAPIClient(api_key).send(Mail(
            from_email=(from_email, "Ross House Rentals"), to_emails=recipient,
            subject=content["subject"], plain_text_content=content["text"], html_content=content["html"],
        ))
        return 200 <= int(response.status_code) < 300

    try:
        return await asyncio.to_thread(_send)
    except Exception:
        logger.exception("Maintenance update email failed")
        return False
