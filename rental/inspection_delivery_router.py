"""Canonical PDF generation and conservative email delivery for inspections."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Response
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .shared import auth_admin, get_db

router = APIRouter(tags=["inspection-delivery"])
MAX_ATTEMPTS = 3
CLAIM_TTL_SECONDS = 300
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ProviderRetryableFailure(Exception):
    pass


class ProviderTerminalFailure(Exception):
    pass


class ProviderAmbiguousResult(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(value: Any) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail="inspection_id_invalid")
    return ObjectId(str(value))


def _signature_bytes(evidence: Dict[str, Any]) -> bytes:
    value = str(evidence.get("signature_data_url") or "")
    if "," not in value:
        return b""
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
    except Exception:
        return b""
    expected = str(evidence.get("signature_sha256") or "")
    if not expected or hashlib.sha256(raw).hexdigest() != expected:
        return b""
    return raw


def build_inspection_pdf(inspection: Dict[str, Any]) -> bytes:
    """Render only canonical inspection fields; signature images must match stored hashes."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    width, height = letter
    y = height - 54

    def line(label: str, value: Any = "") -> None:
        nonlocal y
        safe = str(value or "").replace("\n", " ")[:120]
        pdf.drawString(54, y, f"{label}: {safe}" if label else safe)
        y -= 18

    pdf.setTitle(f"Inspection {inspection.get('_id', '')}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(54, y, "Ross House Rentals - Property Inspection")
    y -= 28
    pdf.setFont("Helvetica", 10)
    line("Inspection ID", inspection.get("_id"))
    line("Status", inspection.get("status"))
    line("Type", inspection.get("type"))
    line("Property", inspection.get("property_name"))
    line("Tenant", inspection.get("tenant_name"))
    line("Scheduled", inspection.get("scheduled_date"))
    line("Inspector", inspection.get("inspector"))
    line("Completed", inspection.get("completed_at"))
    line("Notes", inspection.get("general_notes"))

    rooms = inspection.get("rooms") or {}
    pdf.setFont("Helvetica-Bold", 12)
    line("", "Checklist")
    pdf.setFont("Helvetica", 9)
    iterable = rooms.items() if isinstance(rooms, dict) else enumerate(rooms) if isinstance(rooms, list) else []
    for room_name, room in iterable:
        if y < 100:
            pdf.showPage(); y = height - 54; pdf.setFont("Helvetica", 9)
        line("", str(room_name))
        items = room.get("items", []) if isinstance(room, dict) else []
        for item in items[:50]:
            if isinstance(item, dict):
                line("  " + str(item.get("name") or "Item")[:60], item.get("condition"))

    signatures = inspection.get("signatures") or {}
    for role in ("admin", "tenant"):
        evidence = signatures.get(role) or {}
        raw = _signature_bytes(evidence)
        if y < 130:
            pdf.showPage(); y = height - 54
        pdf.setFont("Helvetica-Bold", 10)
        line(role.title() + " signature", evidence.get("signer_name") or "Not signed")
        pdf.setFont("Helvetica", 8)
        if raw:
            try:
                pdf.drawImage(ImageReader(io.BytesIO(raw)), 54, y - 55, width=180, height=55, preserveAspectRatio=True)
                y -= 62
            except Exception:
                line("", "Signature image unavailable")
        line("Signed at", evidence.get("signed_at"))
        line("Evidence SHA-256", evidence.get("signature_sha256"))

    pdf.save()
    return output.getvalue()


async def _canonical_inspection(db, inspection_id: str) -> Dict[str, Any]:
    row = await db.inspections.find_one({"_id": _oid(inspection_id)})
    if not row:
        raise HTTPException(status_code=404, detail="inspection_not_found")
    if row.get("archived_at"):
        raise HTTPException(status_code=409, detail="inspection_archived")
    return row


@router.get("/admin/inspections/{inspection_id}/pdf")
async def inspection_pdf(inspection_id: str, admin=Depends(auth_admin), db=Depends(get_db)):
    del admin
    row = await _canonical_inspection(db, inspection_id)
    content = build_inspection_pdf(row)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="inspection-{inspection_id}.pdf"'},
    )


@router.post("/admin/inspections/{inspection_id}/send-email")
async def queue_inspection_email(inspection_id: str, admin=Depends(auth_admin), db=Depends(get_db)):
    row = await _canonical_inspection(db, inspection_id)
    if row.get("status") != "completed":
        raise HTTPException(status_code=409, detail="inspection_email_requires_completed")
    signatures = row.get("signatures") or {}
    if not signatures.get("admin") or not signatures.get("tenant"):
        raise HTTPException(status_code=409, detail="inspection_email_requires_signatures")
    tenant_id = str(row.get("tenant_id") or "")
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=409, detail="inspection_tenant_binding_invalid")
    actor = str(admin.get("email") or admin.get("_id") or "admin") if isinstance(admin, dict) else str(admin)
    now = _now()
    intent = {
        "dedupe_key": f"inspection:{inspection_id}:completed",
        "inspection_id": inspection_id,
        "tenant_id": tenant_id,
        "status": "pending",
        "attempts": 0,
        "created_by": actor,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await db.inspection_delivery_outbox.insert_one(intent)
        intent_id = str(result.inserted_id)
        duplicate = False
    except DuplicateKeyError:
        existing = await db.inspection_delivery_outbox.find_one({"dedupe_key": intent["dedupe_key"]})
        intent_id = str((existing or {}).get("_id") or "")
        duplicate = True
    return {"success": True, "queued": True, "duplicate": duplicate, "intent_id": intent_id}


async def claim_next(db, worker_id: str) -> Optional[Dict[str, Any]]:
    """Atomically claim fresh work or a stale claim that never reached the provider."""
    now = _now()
    stale_before = now - timedelta(seconds=CLAIM_TTL_SECONDS)
    claim_id = uuid.uuid4().hex
    return await db.inspection_delivery_outbox.find_one_and_update(
        {
            "attempts": {"$lt": MAX_ATTEMPTS},
            "$or": [
                {"status": {"$in": ["pending", "retryable_failure"]}},
                {
                    "status": "claimed",
                    "provider_started_at": {"$exists": False},
                    "claimed_at": {"$lt": stale_before},
                },
            ],
        },
        {"$set": {"status": "claimed", "claim_id": claim_id, "claimed_by": str(worker_id)[:80],
                  "claimed_at": now, "updated_at": now}, "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def _delivery(db, intent: Dict[str, Any]) -> Dict[str, Any]:
    inspection_id = str(intent.get("inspection_id") or "")
    row = await db.inspections.find_one({"_id": _oid(inspection_id), "status": "completed"})
    if not row or row.get("archived_at"):
        raise ProviderTerminalFailure("inspection_not_completed_or_available")
    if str(row.get("tenant_id") or "") != str(intent.get("tenant_id") or ""):
        raise ProviderTerminalFailure("tenant_binding_changed")
    signatures = row.get("signatures") or {}
    if not signatures.get("admin") or not signatures.get("tenant"):
        raise ProviderTerminalFailure("inspection_signatures_missing")
    tenant_id = str(row.get("tenant_id") or "")
    tenants = await db.tenants.find({"_id": ObjectId(tenant_id)}).limit(2).to_list(2)
    if len(tenants) != 1:
        raise ProviderTerminalFailure("canonical_tenant_missing_or_ambiguous")
    email = str(tenants[0].get("email_normalized") or tenants[0].get("email") or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        raise ProviderTerminalFailure("canonical_tenant_email_invalid")
    pdf = build_inspection_pdf(row)
    return {
        "email": email,
        "subject": f"Ross House Rentals inspection - {str(row.get('property_name') or '')[:120]}",
        "message": "Attached is the completed property inspection report.",
        "filename": f"inspection-{inspection_id}.pdf",
        "pdf": pdf,
    }


def _sendgrid_sync(delivery: Dict[str, Any]) -> Dict[str, Any]:
    key = os.getenv("SENDGRID_API_KEY")
    sender = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not key:
        raise ProviderRetryableFailure("provider_not_configured")
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Attachment, Disposition, FileContent, FileName, FileType, Mail
        message = Mail(from_email=(sender, "Ross House Rentals"), to_emails=delivery["email"],
                       subject=delivery["subject"], plain_text_content=delivery["message"])
        message.attachment = Attachment(
            FileContent(base64.b64encode(delivery["pdf"]).decode()),
            FileName(delivery["filename"]),
            FileType("application/pdf"),
            Disposition("attachment"),
        )
        response = SendGridAPIClient(key).send(message)
    except ProviderRetryableFailure:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None:
            raise ProviderAmbiguousResult("provider_transport_or_timeout") from exc
        if int(status) == 429 or int(status) >= 500:
            raise ProviderRetryableFailure(f"provider_http_{status}") from exc
        raise ProviderTerminalFailure(f"provider_http_{status}") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status not in (200, 201, 202):
        if status == 429 or status >= 500:
            raise ProviderRetryableFailure(f"provider_http_{status}")
        raise ProviderTerminalFailure(f"provider_http_{status}")
    headers = getattr(response, "headers", {}) or {}
    return {"provider": "sendgrid", "provider_message_id": str(headers.get("X-Message-Id") or "")[:200] or None}


async def send_via_provider(delivery: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_sendgrid_sync, delivery)


async def _finish(db, intent: Dict[str, Any], status: str, **fields: Any) -> bool:
    result = await db.inspection_delivery_outbox.update_one(
        {"_id": intent["_id"], "status": "claimed", "claim_id": intent["claim_id"]},
        {"$set": {"status": status, "updated_at": _now(), **fields}},
    )
    return getattr(result, "matched_count", 0) == 1


async def process_claimed(db, intent: Dict[str, Any],
                          sender: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]] = send_via_provider) -> str:
    try:
        delivery = await _delivery(db, intent)
    except (ProviderTerminalFailure, HTTPException) as exc:
        code = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        await _finish(db, intent, "failed", failure_code=code, automatic_retry_allowed=False)
        return "failed"

    marked = await db.inspection_delivery_outbox.update_one(
        {"_id": intent["_id"], "status": "claimed", "claim_id": intent["claim_id"]},
        {"$set": {"provider_started_at": _now(), "updated_at": _now()}},
    )
    if getattr(marked, "matched_count", 0) != 1:
        return "claim_lost"
    try:
        confirmation = await sender(delivery)
    except ProviderAmbiguousResult as exc:
        await _finish(db, intent, "ambiguous_provider_result", failure_code=str(exc), automatic_retry_allowed=False)
        return "ambiguous_provider_result"
    except ProviderRetryableFailure as exc:
        exhausted = int(intent.get("attempts") or 0) >= MAX_ATTEMPTS
        await _finish(db, intent, "failed" if exhausted else "retryable_failure",
                      failure_code=str(exc), automatic_retry_allowed=not exhausted)
        return "failed" if exhausted else "retryable_failure"
    except ProviderTerminalFailure as exc:
        await _finish(db, intent, "failed", failure_code=str(exc), automatic_retry_allowed=False)
        return "failed"
    except Exception:
        await _finish(db, intent, "ambiguous_provider_result",
                      failure_code="provider_unclassified_exception", automatic_retry_allowed=False)
        return "ambiguous_provider_result"
    saved = await _finish(db, intent, "sent", sent_at=_now(), automatic_retry_allowed=False,
                          provider=confirmation.get("provider"),
                          provider_message_id=confirmation.get("provider_message_id"))
    return "sent" if saved else "claim_lost_after_provider_confirmation"


@router.post("/admin/inspections/delivery-outbox/process-next")
async def process_next(admin=Depends(auth_admin), db=Depends(get_db)):
    actor = admin.get("_id") or admin.get("email") if isinstance(admin, dict) else admin
    intent = await claim_next(db, f"admin:{actor}")
    return {"success": True, "outcome": "idle" if not intent else await process_claimed(db, intent)}


async def ensure_indexes(db) -> None:
    await db.inspection_delivery_outbox.create_index("dedupe_key", unique=True)
    await db.inspection_delivery_outbox.create_index([("status", 1), ("created_at", 1)])
