"""Actor-scoped mobile workflow for maintenance technicians.

Technician accounts are explicit ``app_users`` with role ``maintenance`` and a
``service_provider_id``.  Every read and mutation is constrained to that exact
provider assignment; knowing another ticket id never grants access to it.
"""
from datetime import datetime
import re

import bcrypt
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response

from rental.maintenance_security_router import _iso, _validated_photos
from rental.security_email import send_maintenance_updated_email, maintenance_update_push
from rental.shared import (
    auth_admin,
    auth_marketplace,
    get_db,
    send_rental_push_to_user,
)


router = APIRouter()

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TECHNICIAN_TRANSITIONS = {
    "assigned": {"en_route", "in_progress", "waiting_parts", "completed"},
    "scheduled": {"en_route", "in_progress", "waiting_parts", "completed"},
    "en_route": {"in_progress", "waiting_parts", "completed"},
    "in_progress": {"waiting_parts", "completed"},
    "waiting_parts": {"en_route", "in_progress", "completed"},
    "completed": set(),
}


def _id_query(value: str) -> dict:
    return {"_id": ObjectId(value)} if ObjectId.is_valid(value) else {"_id": value}


def _provider_values(provider_id: str) -> list:
    values = [provider_id]
    if ObjectId.is_valid(provider_id):
        values.append(ObjectId(provider_id))
    return values


def _job_query(job_id: str, provider_id: str) -> dict:
    if not ObjectId.is_valid(job_id):
        raise HTTPException(status_code=400, detail="maintenance_job_id_invalid")
    return {
        "_id": ObjectId(job_id),
        "assigned_provider_id": {"$in": _provider_values(provider_id)},
    }


async def _maintenance_identity(request: Request) -> tuple[dict, dict, str]:
    actor = await auth_marketplace(request)
    if str(actor.get("role") or "").strip().lower() != "maintenance":
        raise HTTPException(status_code=403, detail="maintenance_role_required")

    provider_id = str(actor.get("service_provider_id") or "").strip()
    if not provider_id:
        raise HTTPException(status_code=403, detail="maintenance_provider_link_required")

    provider = await get_db().service_providers.find_one(_id_query(provider_id))
    if not provider:
        raise HTTPException(status_code=403, detail="maintenance_provider_link_required")

    linked_user_id = str(provider.get("app_user_id") or "").strip()
    actor_id = str(actor.get("_id") or actor.get("id") or "").strip()
    if linked_user_id and linked_user_id != actor_id:
        raise HTTPException(status_code=403, detail="maintenance_provider_link_mismatch")
    return actor, provider, provider_id


async def _maintenance_actor(request: Request) -> tuple[dict, dict, str]:
    actor, provider, provider_id = await _maintenance_identity(request)
    if str(provider.get("status") or "").strip().lower() != "active":
        raise HTTPException(status_code=403, detail="maintenance_provider_inactive")
    if str(provider.get("worker_type") or "contractor") == "contractor":
        from rental.tax_1099_router import _w9_complete
        if not _w9_complete(provider.get("w9") or {}):
            raise HTTPException(status_code=403, detail="maintenance_w9_required")
    return actor, provider, provider_id


def _technician_job(row: dict, *, detail: bool = False) -> dict:
    item = {
        "id": str(row.get("_id") or ""),
        "request_number": row.get("request_number") or "",
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "general",
        "priority": row.get("priority") or "normal",
        "status": "pending" if row.get("status") == "open" else (row.get("status") or "assigned"),
        "property_address": row.get("property_address") or "",
        "scheduled_start": _iso(row.get("scheduled_start")),
        "scheduled_end": _iso(row.get("scheduled_end")),
        "tenant_presence_required": bool(row.get("tenant_presence_required", False)),
        "tenant_visible_note": row.get("tenant_visible_note") or "",
        "tenant_name": row.get("tenant_name") or "",
        "tenant_phone": row.get("tenant_phone") or "",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "completed_at": _iso(row.get("completed_at")),
    }
    if detail:
        item["photos"] = row.get("photos") or []
        item["technician_photos"] = row.get("technician_photos") or []
        item["materials"] = row.get("materials") or []
        item["total_material_cost"] = float(row.get("total_material_cost") or 0)
        item["technician_notes"] = [
            {
                "note": n.get("note") or "",
                "at": _iso(n.get("at")),
            }
            for n in (row.get("technician_notes") or [])
            if isinstance(n, dict)
        ]
        item["timeline"] = [
            {"status": e.get("status") or "", "at": _iso(e.get("at")), "note": e.get("note") or ""}
            for e in (row.get("timeline") or [])
            if isinstance(e, dict)
        ]
    return item


def _validated_materials(value) -> tuple[list[dict], float]:
    if value in (None, ""):
        return [], 0.0
    if not isinstance(value, list) or len(value) > 25:
        raise HTTPException(status_code=400, detail="maintenance_materials_invalid")
    materials = []
    total = 0.0
    for raw in value:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="maintenance_material_invalid")
        name = str(raw.get("name") or "").strip()
        if not name or len(name) > 120:
            raise HTTPException(status_code=400, detail="maintenance_material_name_invalid")
        try:
            quantity = float(raw.get("quantity", 1))
            unit_cost = float(raw.get("unit_cost", 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="maintenance_material_amount_invalid") from exc
        if isinstance(raw.get("quantity"), bool) or isinstance(raw.get("unit_cost"), bool):
            raise HTTPException(status_code=400, detail="maintenance_material_amount_invalid")
        if quantity <= 0 or quantity > 10000 or unit_cost < 0 or unit_cost > 100000:
            raise HTTPException(status_code=400, detail="maintenance_material_amount_invalid")
        line_total = round(quantity * unit_cost, 2)
        total += line_total
        materials.append({
            "name": name,
            "quantity": quantity,
            "unit_cost": round(unit_cost, 2),
            "line_total": line_total,
        })
    return materials, round(total, 2)


@router.post('/admin/maintenance-users')
async def create_maintenance_user(request: Request):
    admin = await auth_admin(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="maintenance_user_payload_invalid")

    name = str(data.get("name") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    phone = str(data.get("phone") or "").strip()
    password = str(data.get("password") or "")
    provider_id = str(data.get("provider_id") or "").strip()
    requested_worker_type = str(data.get("worker_type") or "").strip().lower()
    if len(name) < 3 or len(name) > 160:
        raise HTTPException(status_code=400, detail="maintenance_user_name_invalid")
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="maintenance_user_email_invalid")
    if not phone:
        raise HTTPException(status_code=400, detail="maintenance_user_phone_required")
    if len(password) < 10 or len(password) > 128:
        raise HTTPException(status_code=400, detail="maintenance_user_password_invalid")
    if not provider_id:
        raise HTTPException(status_code=400, detail="maintenance_provider_required")
    db = get_db()
    provider = await db.service_providers.find_one(_id_query(provider_id))
    if not provider or str(provider.get("status") or "").strip().lower() != "active":
        raise HTTPException(status_code=400, detail="maintenance_provider_not_active")
    worker_type = requested_worker_type or str(provider.get("worker_type") or "contractor").strip().lower()
    if worker_type not in {"contractor", "employee"}:
        raise HTTPException(status_code=400, detail="maintenance_worker_type_invalid")
    if provider.get("app_user_id"):
        raise HTTPException(status_code=409, detail="maintenance_provider_already_linked")
    if await db.app_users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="maintenance_user_email_exists")

    now = datetime.utcnow()
    user_doc = {
        "name": name,
        "email": email,
        "phone": phone,
        "password_hash": bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        "role": "maintenance",
        "service_provider_id": provider_id,
        "maintenance_worker_type": worker_type,
        "status": "active",
        "verified": True,
        "created_at": now,
        "updated_at": now,
        "created_by": str(admin.get("email") or "admin"),
    }
    result = await db.app_users.insert_one(user_doc)
    user_id = str(result.inserted_id)
    linked = await db.service_providers.update_one(
        {**_id_query(provider_id), "$or": [{"app_user_id": {"$exists": False}}, {"app_user_id": ""}, {"app_user_id": None}]},
        {"$set": {"app_user_id": user_id, "app_access_enabled": True, "worker_type": worker_type, "updated_at": now}},
    )
    if linked.matched_count != 1:
        await db.app_users.delete_one({"_id": result.inserted_id})
        raise HTTPException(status_code=409, detail="maintenance_provider_link_concurrent_change")

    return {
        "success": True,
        "user": {"id": user_id, "name": name, "email": email, "role": "maintenance"},
        "provider_id": provider_id,
    }


@router.get('/maintenance/onboarding')
async def maintenance_onboarding(request: Request):
    """Return a contractor's safe onboarding state without exposing the TIN."""
    _actor, provider, _provider_id = await _maintenance_identity(request)
    if str(provider.get("worker_type") or "contractor") != "contractor":
        raise HTTPException(status_code=403, detail="contractor_onboarding_not_applicable")
    from rental.tax_1099_router import _w9_complete, _w9_masked, W9_REVISION

    w9 = provider.get("w9") or {}
    return {
        "success": True,
        "provider": {
            "name": provider.get("name") or "",
            "company_name": provider.get("company_name") or "",
            "email": provider.get("email") or "",
            "phone": provider.get("phone") or "",
            "services": provider.get("services") or [],
            "status": provider.get("status") or "pending_review",
        },
        "w9": {
            "legal_name": w9.get("legal_name") or "",
            "business_name": w9.get("business_name") or "",
            "tax_classification": w9.get("tax_classification") or "individual",
            "tin_type": w9.get("tin_type") or "ssn",
            "tin_masked": _w9_masked(w9),
            "address": w9.get("address") or "",
            "city": w9.get("city") or "",
            "state": w9.get("state") or "",
            "zip": w9.get("zip") or "",
            "signed_at": _iso(w9.get("signed_at")),
        },
        "w9_revision": W9_REVISION,
        "w9_complete": _w9_complete(w9),
        "can_receive_jobs": provider.get("status") == "active" and _w9_complete(w9),
    }


@router.post('/maintenance/onboarding/w9')
async def submit_maintenance_w9(request: Request):
    """Accept an authenticated, signed W-9 and retain only encrypted TIN data."""
    _actor, provider, provider_id = await _maintenance_identity(request)
    if str(provider.get("worker_type") or "contractor") != "contractor":
        raise HTTPException(status_code=403, detail="contractor_onboarding_not_applicable")
    from rental.tax_1099_router import (
        archive_w9_document, build_w9_submission, record_w9_audit,
        _send_admin_email, _w9_masked,
    )

    w9 = build_w9_submission(await request.json(), request, source="authenticated_mobile_wizard")
    now = datetime.utcnow()
    # Never mark onboarding complete unless its immutable official W-9 exists.
    tax_document = await archive_w9_document(get_db(), provider_id, provider, w9)
    await get_db().service_providers.update_one(
        _id_query(provider_id),
        {"$set": {
            "w9": w9,
            "w9_request.completed_at": now,
            "onboarding.w9_complete": True,
            "onboarding.submitted_at": now,
            "onboarding.updated_at": now,
            "updated_at": now,
        }},
    )
    await record_w9_audit(get_db(), provider_id, "authenticated_w9_submitted", w9, request)
    try:
        await _send_admin_email(
            get_db(),
            f"W-9 listo para revisar: {provider.get('name', '')}",
            f"<p><b>{provider.get('name', '')}</b> completó su incorporación fiscal.</p>"
            f"<p>TIN: {_w9_masked(w9)} · Estado: pendiente de aprobación.</p>",
        )
    except Exception:
        pass
    return {
        "success": True,
        "w9_complete": True,
        "status": provider.get("status") or "pending_review",
        "tax_document_id": str(tax_document["_id"]),
    }


@router.get('/maintenance/tax-documents')
async def maintenance_tax_documents(request: Request):
    """List only the authenticated contractor's fiscal documents."""
    _actor, provider, provider_id = await _maintenance_identity(request)
    if str(provider.get("worker_type") or "contractor") != "contractor":
        raise HTTPException(status_code=403, detail="contractor_tax_documents_not_applicable")
    from rental.tax_1099_router import _safe_tax_document
    cursor = get_db().contractor_tax_documents.find({"provider_id": provider_id}).sort("created_at", -1)
    return {"success": True, "documents": [_safe_tax_document(row) async for row in cursor]}


@router.get('/maintenance/tax-documents/{document_id}/download')
async def maintenance_tax_document_download(document_id: str, request: Request):
    """Download a contractor-owned document; cross-provider IDs never work."""
    _actor, provider, provider_id = await _maintenance_identity(request)
    if str(provider.get("worker_type") or "contractor") != "contractor":
        raise HTTPException(status_code=403, detail="contractor_tax_documents_not_applicable")
    document = await get_db().contractor_tax_documents.find_one({
        "_id": document_id, "provider_id": provider_id,
    })
    if not document:
        raise HTTPException(status_code=404, detail="tax_document_not_found")
    from rental.tax_1099_router import _tax_document_pdf
    filename = re.sub(r"[^A-Za-z0-9_.-]", "-", document.get("filename") or "tax-document.pdf")
    return Response(
        content=_tax_document_pdf(document), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get('/maintenance/dashboard')
async def maintenance_dashboard(request: Request):
    _actor, provider, provider_id = await _maintenance_actor(request)
    query = {"assigned_provider_id": {"$in": _provider_values(provider_id)}}
    cursor = get_db().maintenance_requests.find(query).sort("updated_at", -1).limit(100)
    jobs = [_technician_job(row) async for row in cursor]
    active = [j for j in jobs if j["status"] not in {"completed", "resolved", "cancelled", "closed"}]
    response = {
        "success": True,
        "provider": {
            "id": provider_id,
            "name": provider.get("name") or provider.get("company_name") or "",
            "company_name": provider.get("company_name") or "",
            "worker_type": provider.get("worker_type") or "contractor",
        },
        "stats": {
            "assigned": sum(1 for j in active if j["status"] in {"assigned", "scheduled"}),
            "en_route": sum(1 for j in active if j["status"] == "en_route"),
            "in_progress": sum(1 for j in active if j["status"] == "in_progress"),
            "waiting_parts": sum(1 for j in active if j["status"] == "waiting_parts"),
            "completed": sum(1 for j in jobs if j["status"] in {"completed", "resolved", "closed"}),
        },
        "jobs": jobs,
    }
    if str(provider.get("worker_type") or "contractor") == "contractor":
        year = datetime.utcnow().year
        start, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
        paid = 0.0
        pending = 0.0
        async for payment in get_db().provider_payments.find({
            "provider_id": {"$in": _provider_values(provider_id)},
            "$or": [
                {"paid_at": {"$gte": start, "$lt": end}},
                {"paid_at": {"$exists": False}, "created_at": {"$gte": start, "$lt": end}},
            ],
        }):
            amount = float(payment.get("amount") or 0)
            if payment.get("status") == "paid":
                paid += amount
            else:
                pending += amount
        response["contractor"] = {
            "tax_year": year,
            "paid_ytd": round(paid, 2),
            "pending_payments": round(pending, 2),
            "tax_documents_count": await get_db().contractor_tax_documents.count_documents({"provider_id": provider_id}),
        }
    return response


@router.get('/maintenance/jobs/{job_id}')
async def maintenance_job_detail(job_id: str, request: Request):
    _actor, _provider, provider_id = await _maintenance_actor(request)
    row = await get_db().maintenance_requests.find_one(_job_query(job_id, provider_id))
    if not row:
        raise HTTPException(status_code=404, detail="maintenance_job_not_found")
    return {"success": True, "job": _technician_job(row, detail=True)}


@router.patch('/maintenance/jobs/{job_id}')
async def update_maintenance_job(job_id: str, request: Request):
    actor, _provider, provider_id = await _maintenance_actor(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="maintenance_job_update_invalid")
    allowed = {"status", "technician_note", "tenant_visible_note", "materials", "photos"}
    if set(data) - allowed:
        raise HTTPException(status_code=400, detail="maintenance_job_fields_forbidden")

    db = get_db()
    base_query = _job_query(job_id, provider_id)
    row = await db.maintenance_requests.find_one(base_query)
    if not row:
        raise HTTPException(status_code=404, detail="maintenance_job_not_found")
    old_status = str(row.get("status") or "assigned").strip().lower()
    if old_status not in _TECHNICIAN_TRANSITIONS:
        raise HTTPException(status_code=409, detail="maintenance_job_status_not_technician_managed")

    new_status = str(data.get("status") or old_status).strip().lower()
    if new_status != old_status and new_status not in _TECHNICIAN_TRANSITIONS[old_status]:
        raise HTTPException(status_code=409, detail="maintenance_job_transition_invalid")
    tenant_note = str(data.get("tenant_visible_note") or "").strip()
    technician_note = str(data.get("technician_note") or "").strip()
    if len(tenant_note) > 2000 or len(technician_note) > 4000:
        raise HTTPException(status_code=400, detail="maintenance_job_note_too_long")

    now = datetime.utcnow()
    update_fields = {"updated_at": now, "status": new_status}
    push_fields = {}
    if "tenant_visible_note" in data:
        update_fields["tenant_visible_note"] = tenant_note
    if "materials" in data:
        materials, total = _validated_materials(data.get("materials"))
        update_fields["materials"] = materials
        update_fields["total_material_cost"] = total
    if "photos" in data:
        update_fields["technician_photos"] = _validated_photos(data.get("photos"))
    if new_status == "completed":
        update_fields["completed_at"] = now
    if technician_note:
        push_fields["technician_notes"] = {
            "note": technician_note,
            "at": now,
            "actor_id": str(actor.get("_id") or actor.get("id") or ""),
        }
    if new_status != old_status or tenant_note:
        push_fields["timeline"] = {"status": new_status, "at": now, "note": tenant_note}

    update_doc = {"$set": update_fields}
    if push_fields:
        update_doc["$push"] = push_fields
    cas_query = {**base_query, "status": row.get("status")}
    result = await db.maintenance_requests.update_one(cas_query, update_doc)
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="maintenance_job_concurrent_update")

    if new_status != old_status or tenant_note:
        locale = "en" if str(row.get("notification_language") or "es").startswith("en") else "es"
        try:
            await send_rental_push_to_user(
                user_id=str(row.get("tenant_id") or ""),
                title="Maintenance update" if locale == "en" else "Actualización de mantenimiento",
                body=maintenance_update_push(row, new_status, locale),
                data={"type": "maintenance_update", "request_id": job_id, "status": new_status},
            )
        except Exception:
            pass
        try:
            await send_maintenance_updated_email(
                db,
                to_email=row.get("tenant_email") or "",
                name=row.get("tenant_name") or "",
                request_number=row.get("request_number") or job_id,
                title=row.get("title") or "",
                status=new_status,
                changed_at=now,
                locale=locale,
                assigned_to=row.get("assigned_provider_name") or row.get("assigned_to") or "",
                scheduled_start=row.get("scheduled_start"),
                scheduled_end=row.get("scheduled_end"),
                tenant_visible_note=tenant_note or row.get("tenant_visible_note") or "",
            )
        except Exception:
            pass

    return {
        "success": True,
        "job_id": job_id,
        "status": new_status,
        "updated_at": now.isoformat(),
    }
