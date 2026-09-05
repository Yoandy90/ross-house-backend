"""Canonical lifecycle boundary for property inspections.

Inspection evidence is retained: DELETE archives a record and completed
inspections cannot be edited. Property, unit and tenant identity are derived
from canonical rental records rather than accepted from the client.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from .communications_router import INSPECTION_ITEMS, INSPECTION_ROOMS
from .shared import auth_admin, get_db

router = APIRouter(tags=["inspection-security"])

_TYPES = {"move_in", "move_out", "routine"}
_TRANSITIONS = {"pending": {"pending", "in_progress"}, "in_progress": {"in_progress", "completed"}}
_MAX_ROOMS_BYTES = 100_000
_MAX_SIGNATURE_BYTES = 350_000
_SIGNATURE_RE = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$")


def _oid(value: Any, detail: str) -> ObjectId:
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=400, detail=detail)
    return ObjectId(str(value))


def _text(value: Any, *, limit: int, detail: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise HTTPException(status_code=400, detail=detail)
    return value.strip()


def _rooms(value: Any) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="inspection_rooms_invalid")
    try:
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="inspection_rooms_invalid") from exc
    if len(encoded.encode("utf-8")) > _MAX_ROOMS_BYTES:
        raise HTTPException(status_code=413, detail="inspection_rooms_too_large")
    return value


async def _location(db, property_id: Any, unit_id: Any = None):
    property_oid = _oid(property_id, "inspection_property_id_invalid")
    prop = await db.properties.find_one({"_id": property_oid})
    if not prop or prop.get("archived_at"):
        raise HTTPException(status_code=409, detail="inspection_property_unavailable")
    normalized_unit = ""
    if unit_id not in (None, ""):
        unit_oid = _oid(unit_id, "inspection_unit_id_invalid")
        unit = await db.property_units.find_one({"_id": unit_oid, "property_id": str(property_oid)})
        if not unit:
            raise HTTPException(status_code=409, detail="inspection_unit_property_mismatch")
        normalized_unit = str(unit_oid)
    contract_query = {"property_id": str(property_oid), "status": "active"}
    if normalized_unit:
        contract_query["unit_id"] = normalized_unit
    contract = await db.rental_contracts.find_one(contract_query)
    return prop, normalized_unit, contract


def _view(document: dict) -> dict:
    result = dict(document)
    result["_id"] = str(result["_id"])
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


@router.get('/admin/inspections')
async def list_inspections(request: Request):
    await auth_admin(request)
    db = get_db()
    rows = await db.inspections.find({"archived_at": {"$exists": False}}).sort("created_at", -1).limit(200).to_list(200)
    return {"success": True, "inspections": [_view(row) for row in rows], "count": len(rows),
            "rooms": INSPECTION_ROOMS, "items": INSPECTION_ITEMS}


@router.post('/admin/inspections')
async def create_inspection(request: Request):
    admin = await auth_admin(request)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="inspection_payload_invalid")
    db = get_db()
    prop, unit_id, contract = await _location(db, data.get("property_id"), data.get("unit_id"))
    inspection_type = str(data.get("type") or "routine").strip().lower()
    if inspection_type not in _TYPES:
        raise HTTPException(status_code=400, detail="inspection_type_invalid")
    now = datetime.now(timezone.utc)
    actor = str(admin.get("email") or admin.get("_id") or "admin") if isinstance(admin, dict) else str(admin)
    document = {
        "property_id": str(prop["_id"]), "unit_id": unit_id,
        "property_name": str(prop.get("name") or prop.get("address") or ""),
        "tenant_id": str((contract or {}).get("tenant_id") or ""),
        "tenant_name": str((contract or {}).get("tenant_name") or ""),
        "type": inspection_type, "status": "pending",
        "scheduled_date": _text(data.get("scheduled_date"), limit=40, detail="inspection_date_invalid"),
        "rooms": {},
        "general_notes": _text(data.get("general_notes"), limit=4000, detail="inspection_notes_invalid"),
        "inspector": _text(data.get("inspector") or actor, limit=160, detail="inspection_inspector_invalid"),
        "created_by": actor, "created_at": now, "updated_at": now,
    }
    result = await db.inspections.insert_one(document)
    document["_id"] = result.inserted_id
    return {"success": True, "inspection": _view(document)}


@router.get('/admin/inspections/{inspection_id}')
async def get_inspection(inspection_id: str, request: Request):
    await auth_admin(request)
    row = await get_db().inspections.find_one({"_id": _oid(inspection_id, "inspection_id_invalid")})
    if not row:
        raise HTTPException(status_code=404, detail="inspection_not_found")
    return {"success": True, "inspection": _view(row), "rooms": INSPECTION_ROOMS, "items": INSPECTION_ITEMS}


@router.put('/admin/inspections/{inspection_id}')
async def update_inspection(inspection_id: str, request: Request):
    await auth_admin(request)
    db = get_db()
    oid = _oid(inspection_id, "inspection_id_invalid")
    current = await db.inspections.find_one({"_id": oid})
    if not current:
        raise HTTPException(status_code=404, detail="inspection_not_found")
    if current.get("archived_at"):
        raise HTTPException(status_code=409, detail="inspection_archived")
    if current.get("status") == "completed":
        raise HTTPException(status_code=409, detail="inspection_completed_immutable")
    data = await request.json()
    if not isinstance(data, dict) or any(key in data for key in ("property_id", "unit_id", "tenant_id", "tenant_name")):
        raise HTTPException(status_code=400, detail="inspection_identity_immutable")
    update = {"updated_at": datetime.now(timezone.utc)}
    if "rooms" in data:
        update["rooms"] = _rooms(data["rooms"])
    for field, limit, detail in (
        ("general_notes", 4000, "inspection_notes_invalid"),
        ("scheduled_date", 40, "inspection_date_invalid"),
        ("inspector", 160, "inspection_inspector_invalid"),
    ):
        if field in data:
            update[field] = _text(data[field], limit=limit, detail=detail)
    target = str(data.get("status") or current.get("status") or "")
    if target not in _TRANSITIONS.get(str(current.get("status") or ""), set()):
        raise HTTPException(status_code=409, detail="inspection_status_transition_invalid")
    update["status"] = target
    if target == "completed":
        update["completed_at"] = update["updated_at"]
    result = await db.inspections.update_one(
        {"_id": oid, "status": current.get("status"), "archived_at": {"$exists": False}}, {"$set": update}
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="inspection_state_changed")
    return {"success": True, "inspection": _view({**current, **update})}


def _signature_image(value: Any) -> tuple[str, str]:
    if not isinstance(value, str) or len(value) > 500_000:
        raise HTTPException(status_code=413, detail="inspection_signature_too_large")
    match = _SIGNATURE_RE.fullmatch(value)
    if not match:
        raise HTTPException(status_code=400, detail="inspection_signature_format_invalid")
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="inspection_signature_format_invalid") from exc
    if not decoded or len(decoded) > _MAX_SIGNATURE_BYTES:
        raise HTTPException(status_code=413, detail="inspection_signature_too_large")
    return value, hashlib.sha256(decoded).hexdigest()


@router.post('/admin/inspections/{inspection_id}/signatures/{signer}')
async def sign_inspection(inspection_id: str, signer: str, request: Request):
    admin = await auth_admin(request)
    if signer not in {"admin", "tenant"}:
        raise HTTPException(status_code=400, detail="inspection_signer_invalid")
    data = await request.json()
    if not isinstance(data, dict) or data.get("consent_acknowledged") is not True:
        raise HTTPException(status_code=400, detail="inspection_signature_consent_required")
    signer_name = _text(data.get("signer_name"), limit=160, detail="inspection_signer_name_invalid")
    if not signer_name:
        raise HTTPException(status_code=400, detail="inspection_signer_name_required")
    image_data_url, image_sha256 = _signature_image(data.get("signature_data_url"))

    db = get_db()
    oid = _oid(inspection_id, "inspection_id_invalid")
    current = await db.inspections.find_one({"_id": oid})
    if not current:
        raise HTTPException(status_code=404, detail="inspection_not_found")
    if current.get("archived_at"):
        raise HTTPException(status_code=409, detail="inspection_archived")
    if current.get("status") != "in_progress":
        raise HTTPException(status_code=409, detail="inspection_signature_requires_in_progress")
    if (current.get("signatures") or {}).get(signer):
        raise HTTPException(status_code=409, detail="inspection_signature_immutable")

    now = datetime.now(timezone.utc)
    actor = str(admin.get("email") or admin.get("_id") or "admin") if isinstance(admin, dict) else str(admin)
    client = getattr(request, "client", None)
    evidence_id = uuid.uuid4().hex
    evidence = {
        "evidence_id": evidence_id,
        "signer_role": signer,
        "signer_name": signer_name,
        "consent_acknowledged": True,
        "signature_data_url": image_data_url,
        "signature_sha256": image_sha256,
        "signed_at": now.isoformat(),
        "captured_by": actor,
        "source_ip": str(getattr(client, "host", "") or "")[:64],
    }
    event = {key: value for key, value in evidence.items() if key != "signature_data_url"}
    result = await db.inspections.update_one(
        {
            "_id": oid,
            "status": "in_progress",
            "archived_at": {"$exists": False},
            f"signatures.{signer}": {"$exists": False},
        },
        {
            "$set": {f"signatures.{signer}": evidence, "updated_at": now},
            "$push": {"signature_events": event},
        },
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="inspection_signature_state_changed")
    return {"success": True, "signature": event}


@router.delete('/admin/inspections/{inspection_id}')
async def archive_inspection(inspection_id: str, request: Request):
    admin = await auth_admin(request)
    db = get_db()
    oid = _oid(inspection_id, "inspection_id_invalid")
    actor = str(admin.get("email") or admin.get("_id") or "admin") if isinstance(admin, dict) else str(admin)
    now = datetime.now(timezone.utc)
    result = await db.inspections.update_one(
        {"_id": oid, "archived_at": {"$exists": False}},
        {"$set": {"archived_at": now, "archived_by": actor, "updated_at": now}},
    )
    if getattr(result, "matched_count", 0) != 1:
        existing = await db.inspections.find_one({"_id": oid})
        if not existing:
            raise HTTPException(status_code=404, detail="inspection_not_found")
        raise HTTPException(status_code=409, detail="inspection_already_archived")
    return {"success": True, "archived": True}


async def ensure_indexes(db) -> None:
    await db.inspections.create_index([("archived_at", 1), ("created_at", -1)])
    await db.inspections.create_index([("property_id", 1), ("unit_id", 1), ("scheduled_date", 1)])
    await db.inspections.create_index([("status", 1), ("scheduled_date", 1)])
