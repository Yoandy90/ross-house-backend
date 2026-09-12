"""Best-effort maintenance request email notifications.

A ticket is persisted before this module runs. Email failures are reported to
the caller and must never roll back or hide a valid maintenance request.
"""
import asyncio
import html
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _safe(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _category_label(value: str) -> str:
    return {
        "plumbing": "Plomería",
        "electrical": "Electricidad",
        "hvac": "A/C - Calefacción",
        "appliance": "Electrodoméstico",
        "general": "General",
        "structural": "Estructura",
        "pest": "Plagas",
        "other": "Otro",
    }.get(value, value.capitalize() or "General")


def _priority_label(value: str) -> str:
    return {
        "low": "Baja",
        "normal": "Normal",
        "medium": "Normal",
        "high": "Alta",
        "urgent": "Urgente",
    }.get(value, value.capitalize() or "Normal")


def _build_messages(ticket: dict, request_id: str, admin_url: str) -> dict:
    title = _safe(ticket.get("title"))
    tenant_name = _safe(ticket.get("tenant_name") or "Inquilino")
    property_address = _safe(ticket.get("property_address") or "No asignada")
    description = _safe(ticket.get("description"))
    category = _safe(_category_label(str(ticket.get("category") or "general")))
    priority = _safe(_priority_label(str(ticket.get("priority") or "normal")))
    photo_count = len(ticket.get("photos") or [])
    request_code = _safe(request_id)
    admin_link = _safe(admin_url.rstrip("/") + "/admin/mantenimiento")

    tenant_subject = f"Recibimos tu solicitud de mantenimiento — {ticket.get('title') or request_id}"
    tenant_text = (
        "Hola,\n\n"
        "Recibimos tu solicitud de mantenimiento.\n"
        f"ID: {request_id}\n"
        f"Propiedad: {ticket.get('property_address') or 'No asignada'}\n"
        f"Categoría: {_category_label(str(ticket.get('category') or 'general'))}\n"
        f"Prioridad: {_priority_label(str(ticket.get('priority') or 'normal'))}\n"
        f"Estado: Pendiente\n"
        f"Fotos recibidas: {photo_count}\n\n"
        "Nuestro equipo revisará la solicitud y te notificará cuando cambie su estado. "
        "Si existe peligro inmediato para la vida, llama al 911.\n\n"
        "Ross House Rentals"
    )
    tenant_html = f"""<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;background:#f4f4f5;padding:20px">
<div style="max-width:640px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
<div style="background:#1f2937;color:#fff;padding:22px;border-bottom:4px solid #c8102e">
<h1 style="margin:0;font-size:21px">Solicitud recibida</h1>
<p style="margin:6px 0 0;color:#d1d5db">Ross House Rentals · Mantenimiento</p>
</div>
<div style="padding:24px;color:#111827">
<p>Hola <strong>{tenant_name}</strong>,</p>
<p>Recibimos tu solicitud de mantenimiento y nuestro equipo la revisará.</p>
<h2 style="color:#c8102e;font-size:19px">{title}</h2>
<table style="width:100%;border-collapse:collapse;background:#f9fafb;border-radius:8px">
<tr><td style="padding:9px 12px;color:#6b7280">ID</td><td style="padding:9px 12px"><strong>{request_code}</strong></td></tr>
<tr><td style="padding:9px 12px;color:#6b7280">Propiedad</td><td style="padding:9px 12px">{property_address}</td></tr>
<tr><td style="padding:9px 12px;color:#6b7280">Categoría</td><td style="padding:9px 12px">{category}</td></tr>
<tr><td style="padding:9px 12px;color:#6b7280">Prioridad</td><td style="padding:9px 12px">{priority}</td></tr>
<tr><td style="padding:9px 12px;color:#6b7280">Estado</td><td style="padding:9px 12px">Pendiente</td></tr>
<tr><td style="padding:9px 12px;color:#6b7280">Fotos recibidas</td><td style="padding:9px 12px">{photo_count}</td></tr>
</table>
<p style="margin-top:20px">Te notificaremos cuando cambie el estado de la solicitud.</p>
<p style="background:#fff7ed;border-left:4px solid #f59e0b;padding:12px">Si existe peligro inmediato para la vida, llama al 911.</p>
</div></div></body></html>"""

    admin_subject = f"Nueva solicitud de mantenimiento — {ticket.get('title') or request_id}"
    admin_text = (
        "Nueva solicitud de mantenimiento.\n"
        f"ID: {request_id}\n"
        f"Inquilino: {ticket.get('tenant_name') or 'N/A'}\n"
        f"Email: {ticket.get('tenant_email') or 'N/A'}\n"
        f"Propiedad: {ticket.get('property_address') or 'N/A'}\n"
        f"Categoría: {_category_label(str(ticket.get('category') or 'general'))}\n"
        f"Prioridad: {_priority_label(str(ticket.get('priority') or 'normal'))}\n"
        f"Fotos recibidas: {photo_count}\n"
        f"Descripción: {ticket.get('description') or ''}\n"
        f"Panel: {admin_url.rstrip('/')}/admin/mantenimiento"
    )
    admin_html = f"""<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;background:#f4f4f5;padding:20px">
<div style="max-width:680px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
<div style="background:#1f2937;color:#fff;padding:22px;border-bottom:4px solid #c8102e">
<h1 style="margin:0;font-size:21px">Nueva solicitud de mantenimiento</h1></div>
<div style="padding:24px;color:#111827">
<h2 style="color:#c8102e;font-size:19px">{title}</h2>
<p><strong>Inquilino:</strong> {tenant_name}<br><strong>Propiedad:</strong> {property_address}</p>
<p><strong>ID:</strong> {request_code}<br><strong>Categoría:</strong> {category}<br>
<strong>Prioridad:</strong> {priority}<br><strong>Fotos recibidas:</strong> {photo_count}</p>
<div style="background:#f9fafb;padding:14px;border-radius:8px;white-space:pre-wrap">{description}</div>
<p style="text-align:center;margin-top:24px"><a href="{admin_link}" style="background:#c8102e;color:#fff;padding:12px 22px;border-radius:7px;text-decoration:none;font-weight:700">Abrir panel administrativo</a></p>
<p style="color:#6b7280;font-size:12px">Las fotos se consultan de forma segura en el panel; no se adjuntan a este correo.</p>
</div></div></body></html>"""
    return {
        "tenant": {"subject": tenant_subject, "text": tenant_text, "html": tenant_html},
        "admin": {"subject": admin_subject, "text": admin_text, "html": admin_html},
    }


async def send_maintenance_created_emails(db, ticket: dict, request_id: str) -> dict:
    result = {"tenant_sent": False, "admin_sent": 0, "photo_count": len(ticket.get("photos") or [])}
    config = {}
    try:
        config = await db.api_config.find_one({"_id": "main"}) or {}
    except Exception as exc:
        logger.warning("maintenance email config lookup failed: %s", exc)

    api_key = os.getenv("SENDGRID_API_KEY") or config.get("sendgrid_api_key") or config.get("SENDGRID_API_KEY")
    sender = (
        os.getenv("MAINTENANCE_FROM_EMAIL")
        or config.get("maintenance_from_email")
        or os.getenv("SENDGRID_FROM_EMAIL")
        or config.get("sendgrid_from_email")
        or "info@rosshouserentals.com"
    )
    sender_name = os.getenv("MAINTENANCE_FROM_NAME") or config.get("maintenance_from_name") or "Ross House Maintenance"
    admin_url = os.getenv("ADMIN_FRONTEND_URL") or config.get("admin_frontend_url") or "https://rosshouserentals.com"
    if not api_key:
        result["error"] = "sendgrid_not_configured"
        return result

    admin_emails = set()
    try:
        admins = await db.app_users.find({
            "$or": [
                {"is_admin": True},
                {"role": {"$in": ["admin", "super_admin"]}},
                {"is_super_admin": True},
            ]
        }).to_list(20)
        admin_emails.update(str(a.get("email") or "").strip().lower() for a in admins if a.get("email"))
    except Exception as exc:
        logger.warning("maintenance admin recipient lookup failed: %s", exc)

    owner_email = str((ticket.get("property") or {}).get("owner_email") or "").strip().lower()
    if owner_email:
        admin_emails.add(owner_email)
    tenant_email = str(ticket.get("tenant_email") or "").strip().lower()
    messages = _build_messages(ticket, request_id, admin_url)

    def _deliver():
        import sendgrid
        from sendgrid.helpers.mail import Content, Email, Mail, To

        sg = sendgrid.SendGridAPIClient(api_key=api_key)

        def send_one(recipient: str, message: dict):
            mail = Mail(
                from_email=Email(sender, sender_name),
                to_emails=To(recipient),
                subject=message["subject"],
                plain_text_content=Content("text/plain", message["text"]),
                html_content=Content("text/html", message["html"]),
            )
            response = sg.client.mail.send.post(request_body=mail.get())
            return 200 <= int(getattr(response, "status_code", 202)) < 300

        delivered_admin = 0
        for recipient in sorted(admin_emails):
            try:
                delivered_admin += int(send_one(recipient, messages["admin"]))
            except Exception as exc:
                logger.warning("maintenance admin email failed for %s: %s", recipient, exc)

        delivered_tenant = False
        if tenant_email:
            try:
                delivered_tenant = send_one(tenant_email, messages["tenant"])
            except Exception as exc:
                logger.warning("maintenance tenant email failed for %s: %s", tenant_email, exc)
        return delivered_tenant, delivered_admin

    result["tenant_sent"], result["admin_sent"] = await asyncio.to_thread(_deliver)
    return result
