"""Admin-only synthetic inspection delivery fixture for isolated staging smoke tests."""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .shared import auth_admin, get_db
from .staging_fixture_policy import StagingFixturePolicyError, assert_staging_fixture_allowed, validate_fixture_marker

router = APIRouter(prefix="/admin/staging-fixtures", tags=["staging-fixtures"])
_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


class FixtureConfirmation(BaseModel):
    confirm_marker: str


def _guard(marker: str, confirmation: str | None = None) -> str:
    try:
        assert_staging_fixture_allowed(os.environ, database_name=os.getenv("DB_NAME", ""))
        value = validate_fixture_marker(marker)
    except StagingFixturePolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if confirmation is not None and confirmation != value:
        raise HTTPException(status_code=400, detail="fixture_confirmation_mismatch")
    return value


async def _delete_exact(db, marker: str) -> dict[str, int]:
    outbox_rows = await db.inspection_delivery_outbox.find(
        {"staging_fixture_marker": marker, "synthetic": True}
    ).to_list(10)
    outbox_ids = [str(row["_id"]) for row in outbox_rows]
    audit_filter = {
        "action": "inspection_delivery_manual_retry",
        "resource_id": {"$in": outbox_ids},
    } if outbox_ids else {"_id": {"$in": []}}
    results = {
        "audit_logs": (await db.admin_audit_logs.delete_many(audit_filter)).deleted_count,
        "outbox": (await db.inspection_delivery_outbox.delete_many(
            {"staging_fixture_marker": marker, "synthetic": True}
        )).deleted_count,
        "inspections": (await db.inspections.delete_many(
            {"staging_fixture_marker": marker, "synthetic": True}
        )).deleted_count,
        "tenants": (await db.tenants.delete_many(
            {"staging_fixture_marker": marker, "synthetic": True}
        )).deleted_count,
    }
    return results


@router.post("/inspection-delivery/{marker}")
async def create_inspection_delivery_fixture(
    marker: str,
    payload: FixtureConfirmation,
    admin=Depends(auth_admin),
    db=Depends(get_db),
):
    del admin
    value = _guard(marker, payload.confirm_marker)
    collections = (db.tenants, db.inspections, db.inspection_delivery_outbox)
    if any([await collection.find_one({"staging_fixture_marker": value}) for collection in collections]):
        raise HTTPException(status_code=409, detail="fixture_marker_already_exists")

    now = datetime.now(timezone.utc)
    tenant_id, inspection_id, outbox_id = ObjectId(), ObjectId(), ObjectId()
    signature_raw = base64.b64decode(_PNG_B64)
    signature = {
        "signer_name": "STAGING SYNTHETIC SIGNER",
        "signature_data_url": f"data:image/png;base64,{_PNG_B64}",
        "signature_sha256": hashlib.sha256(signature_raw).hexdigest(),
        "signed_at": now,
        "consent_acknowledged": True,
    }
    tenant = {
        "_id": tenant_id,
        "name": "STAGING INSPECTION TENANT",
        "email": f"{value}@invalid.example",
        "email_normalized": f"{value}@invalid.example",
        "status": "active",
        "synthetic": True,
        "staging_fixture_marker": value,
        "created_at": now,
    }
    inspection = {
        "_id": inspection_id,
        "property_id": str(ObjectId()),
        "property_name": "STAGING INSPECTION PROPERTY",
        "tenant_id": str(tenant_id),
        "tenant_name": tenant["name"],
        "type": "routine",
        "status": "completed",
        "rooms": {},
        "signatures": {"admin": dict(signature), "tenant": dict(signature)},
        "completed_at": now,
        "synthetic": True,
        "staging_fixture_marker": value,
        "created_at": now,
        "updated_at": now,
    }
    outbox = {
        "_id": outbox_id,
        "dedupe_key": f"staging-inspection:{value}",
        "inspection_id": str(inspection_id),
        "tenant_id": str(tenant_id),
        "status": "failed",
        "attempts": 3,
        "failure_code": "provider_not_configured",
        "automatic_retry_allowed": False,
        "created_by": "staging-smoke",
        "synthetic": True,
        "staging_fixture_marker": value,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.tenants.insert_one(tenant)
        await db.inspections.insert_one(inspection)
        await db.inspection_delivery_outbox.insert_one(outbox)
    except Exception as exc:
        await _delete_exact(db, value)
        raise HTTPException(status_code=500, detail="fixture_create_rolled_back") from exc
    return {
        "success": True,
        "marker": value,
        "inspection_id": str(inspection_id),
        "intent_id": str(outbox_id),
    }


@router.get("/inspection-delivery/{marker}")
async def inspect_inspection_delivery_fixture(marker: str, admin=Depends(auth_admin), db=Depends(get_db)):
    del admin
    value = _guard(marker)
    tenant = await db.tenants.find_one({"staging_fixture_marker": value, "synthetic": True})
    inspection = await db.inspections.find_one({"staging_fixture_marker": value, "synthetic": True})
    outbox = await db.inspection_delivery_outbox.find_one({"staging_fixture_marker": value, "synthetic": True})
    return {
        "success": True,
        "marker": value,
        "present": bool(tenant or inspection or outbox),
        "complete": bool(tenant and inspection and outbox),
        "intent_id": str(outbox["_id"]) if outbox else None,
        "inspection_id": str(inspection["_id"]) if inspection else None,
        "delivery_status": outbox.get("status") if outbox else None,
        "attempts": outbox.get("attempts") if outbox else None,
    }


@router.delete("/inspection-delivery/{marker}")
async def delete_inspection_delivery_fixture(
    marker: str,
    payload: FixtureConfirmation,
    admin=Depends(auth_admin),
    db=Depends(get_db),
):
    del admin
    value = _guard(marker, payload.confirm_marker)
    return {"success": True, "marker": value, "deleted": await _delete_exact(db, value)}
