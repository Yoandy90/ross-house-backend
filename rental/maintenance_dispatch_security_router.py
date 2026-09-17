"""Fail-closed provider dispatch for canonically bound maintenance tickets.

Assignment is claimed with CAS before any external notification is attempted.
A provider must be active, terminal tickets cannot be dispatched, and retries do
not silently overwrite an existing provider assignment.  Ticket ownership fields
remain immutable and are revalidated against tenant/contract/property/unit.
"""
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import auth_admin, get_db, send_rental_push_to_user
from rental.maintenance_ownership_security_router import _canonical_status, _load_bound_maintenance_request
from rental.security_email import send_maintenance_updated_email, maintenance_update_push, maintenance_request_label
from rental.service_providers_router import _get_settings, _send_email, _send_sms
from rental._provider_email_templates import dispatch_job_html

router = APIRouter()
_DISPATCHABLE_STATUSES = {"pending", "reviewing", "assigned", "in_progress", "waiting_parts"}


def _provider_query(provider_id: str) -> dict:
    if not provider_id:
        raise HTTPException(status_code=400, detail="maintenance_provider_required")
    return {"_id": ObjectId(provider_id)} if ObjectId.is_valid(provider_id) else {"_id": provider_id}


@router.post('/admin/service-providers/dispatch-maintenance')
async def secure_admin_dispatch_maintenance(request: Request):
    await auth_admin(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="maintenance_dispatch_payload_invalid")

    provider_id = str(data.get("provider_id") or "").strip()
    request_id = str(data.get("request_id") or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="maintenance_request_required")

    extra_note = str(data.get("extra_note") or "").strip()
    if len(extra_note) > 2000:
        raise HTTPException(status_code=400, detail="maintenance_dispatch_note_too_long")
    via_email = bool(data.get("via_email", True))
    via_sms = bool(data.get("via_sms", True))

    db = get_db()
    provider = await db.service_providers.find_one(_provider_query(provider_id))
    if not provider or str(provider.get("status") or "").strip().lower() != "active":
        raise HTTPException(status_code=400, detail="maintenance_provider_not_active")

    ticket = await _load_bound_maintenance_request(request_id)
    raw_status = str(ticket.get("status") or "pending").strip().lower()
    status = _canonical_status(raw_status)
    if status not in _DISPATCHABLE_STATUSES:
        raise HTTPException(status_code=409, detail="maintenance_not_dispatchable")

    existing_provider = str(ticket.get("assigned_provider_id") or "").strip()
    if existing_provider:
        if existing_provider == provider_id:
            return {
                "success": True,
                "already_assigned": True,
                "provider_id": provider_id,
                "email_sent": bool(ticket.get("dispatch_email_sent", False)),
                "sms_sent": bool(ticket.get("dispatch_sms_sent", False)),
            }
        raise HTTPException(status_code=409, detail="maintenance_already_assigned")

    now = datetime.utcnow()
    claim = await db.maintenance_requests.update_one(
        {
            "_id": ticket["_id"],
            "status": ticket.get("status"),
            "$or": [
                {"assigned_provider_id": {"$exists": False}},
                {"assigned_provider_id": None},
                {"assigned_provider_id": ""},
            ],
        },
        {"$set": {
            "assigned_provider_id": provider_id,
            "assigned_provider_name": provider.get("name") or provider.get("company_name") or "",
            "assigned_to": provider.get("name") or provider.get("company_name") or "",
            "assigned_provider_phone": provider.get("phone") or "",
            "assigned_at": now,
            "status": "assigned" if status != "in_progress" else "in_progress",
            "dispatch_notification_state": "pending",
            "updated_at": now,
        }, "$push": {"timeline": {
            "status": "assigned" if status != "in_progress" else "in_progress",
            "at": now,
            "note": "",
        }}},
    )
    if claim.matched_count != 1:
        raise HTTPException(status_code=409, detail="maintenance_dispatch_concurrent_change")

    settings = await _get_settings(db)
    lang = str(provider.get("language_pref") or "es").lower()
    address = ticket.get("property_address") or "—"
    title = ticket.get("title") or "Mantenimiento"
    priority = ticket.get("priority") or "medium"
    tenant_name = ticket.get("tenant_name") or ""
    tenant_phone = ticket.get("tenant_phone") or ""
    job_payload = {
        "title": title,
        "property_address": address,
        "description": ticket.get("description") or "",
        "priority": priority,
        "tenant_name": tenant_name,
        "tenant_phone": tenant_phone,
    }
    template = dispatch_job_html(provider, job_payload, extra_note=extra_note, lang=lang)
    if lang == "es":
        sms_body = (
            f"Ross House: Trabajo disponible en {address} — {title} ({priority}). "
            f"Inquilino: {tenant_name} {tenant_phone}. Responde si puedes tomarlo. (806) 934-2018"
        )
    else:
        sms_body = (
            f"Ross House: Job available at {address} — {title} ({priority}). "
            f"Tenant: {tenant_name} {tenant_phone}. Reply if you can take it. (806) 934-2018"
        )

    email_sent = False
    sms_sent = False
    if via_email and settings.get("email_enabled"):
        email_sent = await _send_email(
            provider.get("email") or "", template["subject"], template["text"], html_body=template["html"]
        )
    if via_sms and settings.get("sms_enabled"):
        sms_sent = await _send_sms(provider.get("phone") or "", sms_body)

    notification_state = "sent" if (email_sent or sms_sent) else "not_sent"
    await db.maintenance_requests.update_one(
        {"_id": ticket["_id"], "assigned_provider_id": provider_id},
        {"$set": {
            "dispatch_notification_state": notification_state,
            "dispatch_email_sent": bool(email_sent),
            "dispatch_sms_sent": bool(sms_sent),
            "dispatch_notified_at": datetime.utcnow() if notification_state == "sent" else None,
            "updated_at": datetime.utcnow(),
        }},
    )
    await db.service_providers.update_one(
        _provider_query(provider_id),
        {"$push": {"dispatch_history": {
            "type": "maintenance_dispatch",
            "sent_at": datetime.utcnow(),
            "job_id": str(ticket["_id"]),
            "email": bool(email_sent),
            "sms": bool(sms_sent),
        }}, "$inc": {"total_jobs": 1}, "$set": {"updated_at": datetime.utcnow()}},
    )

    # A linked app account receives the assignment immediately. Email and SMS
    # remain available for external contractors who do not use the app.
    if provider.get("app_user_id"):
        provider_locale = "en" if str(provider.get("language_pref") or "es").startswith("en") else "es"
        try:
            await send_rental_push_to_user(
                user_id=str(provider["app_user_id"]),
                title="New assigned job" if provider_locale == "en" else "Nuevo trabajo asignado",
                body=f"{maintenance_request_label(ticket, provider_locale)} · {address}",
                data={"type": "maintenance_assignment", "request_id": request_id},
            )
        except Exception:
            pass

    tenant_locale = "en" if str(ticket.get("notification_language") or "es").startswith("en") else "es"
    tenant_status = "assigned" if status != "in_progress" else "in_progress"
    provider_name = provider.get("name") or provider.get("company_name") or ""
    try:
        await send_rental_push_to_user(
            user_id=str(ticket.get("tenant_id") or ""),
            title="📋 Maintenance Update" if tenant_locale == "en" else "📋 Actualización de Mantenimiento",
            body=maintenance_update_push(ticket, tenant_status, tenant_locale),
            data={"type": "maintenance_update", "request_id": request_id, "status": tenant_status},
        )
    except Exception:
        pass
    try:
        await send_maintenance_updated_email(
            db,
            to_email=ticket.get("tenant_email") or "",
            name=ticket.get("tenant_name") or "",
            request_number=ticket.get("request_number") or request_id,
            title=ticket.get("title") or "",
            status=tenant_status,
            changed_at=now,
            locale=tenant_locale,
            assigned_to=provider_name,
            scheduled_start=ticket.get("scheduled_start"),
            scheduled_end=ticket.get("scheduled_end"),
            tenant_visible_note=ticket.get("tenant_visible_note") or "",
        )
    except Exception:
        pass

    return {
        "success": True,
        "already_assigned": False,
        "provider_id": provider_id,
        "email_sent": bool(email_sent),
        "sms_sent": bool(sms_sent),
        "notification_state": notification_state,
    }
