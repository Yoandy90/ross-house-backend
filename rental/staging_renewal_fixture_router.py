"""Admin-only synthetic renewal source fixtures for the isolated staging DB."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from rental.shared import auth_admin, get_db
from rental.lease_renewal_contract_generation_router import _renewal_contract_id
from rental.staging_fixture_policy import (
    StagingFixturePolicyError,
    assert_staging_fixture_allowed,
    validate_fixture_marker,
)

router = APIRouter(prefix="/admin/staging-fixtures", tags=["staging-fixtures"])
_CREATE_CONFIRMATION = "CREATE_SYNTHETIC_RENEWAL"
_DELETE_CONFIRMATION = "DELETE_SYNTHETIC_RENEWAL"


def _assert_allowed() -> None:
    try:
        assert_staging_fixture_allowed(os.environ, database_name=os.getenv("DB_NAME", ""))
    except StagingFixturePolicyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _marker_or_400(marker: str) -> str:
    try:
        return validate_fixture_marker(marker)
    except StagingFixturePolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _cleanup_source(db, marker: str) -> dict[str, int]:
    collections = ("rental_contracts", "tenants", "properties")
    deleted: dict[str, int] = {}
    for name in collections:
        result = await getattr(db, name).delete_many(
            {"staging_fixture_marker": marker, "synthetic": True}
        )
        deleted[name] = int(result.deleted_count)
    return deleted


@router.post("/renewal-source")
async def create_renewal_source(
    body: dict = Body(...),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    """Create exactly one synthetic property/tenant/active-contract source set."""
    del admin
    _assert_allowed()
    if set(body) != {"confirmation"} or body.get("confirmation") != _CREATE_CONFIRMATION:
        raise HTTPException(status_code=400, detail="fixture_create_confirmation_required")

    marker = f"staging-renewal-{uuid4().hex}"
    property_id, tenant_id, contract_id = ObjectId(), ObjectId(), ObjectId()
    now = datetime.now(timezone.utc)
    lease_end = now + timedelta(days=30)

    prop = {
        "_id": property_id,
        "address": f"STAGING FIXTURE {marker}",
        "city": "Dumas",
        "state": "TX",
        "zip": "79029",
        "status": "rented",
        "status_manually_set": False,
        "current_contract_id": str(contract_id),
        "current_tenant_id": str(tenant_id),
        "synthetic": True,
        "staging_fixture_marker": marker,
        "created_at": now,
    }
    tenant = {
        "_id": tenant_id,
        "name": "STAGING TEST TENANT",
        "email": f"{marker}@invalid.example",
        "phone": "+18065550199",
        "status": "active",
        "current_contract_id": str(contract_id),
        "current_property_id": str(property_id),
        "current_unit_id": None,
        "synthetic": True,
        "staging_fixture_marker": marker,
        "created_at": now,
    }
    contract = {
        "_id": contract_id,
        "status": "active",
        "property_id": str(property_id),
        "tenant_id": str(tenant_id),
        "unit_id": None,
        "tenant_name": tenant["name"],
        "tenant_email": tenant["email"],
        "tenant_phone": tenant["phone"],
        "property_address": prop["address"],
        "rent_amount": 1200.0,
        "deposit_amount": 500.0,
        "payment_due_day": 1,
        "start_date": (now - timedelta(days=335)).date().isoformat(),
        "end_date": lease_end.isoformat(),
        "synthetic": True,
        "staging_fixture_marker": marker,
        "created_at": now,
    }

    try:
        await db.properties.insert_one(prop)
        await db.tenants.insert_one(tenant)
        await db.rental_contracts.insert_one(contract)
    except Exception:
        await _cleanup_source(db, marker)
        raise HTTPException(status_code=500, detail="fixture_create_rolled_back")

    return {
        "ok": True,
        "synthetic": True,
        "marker": marker,
        "property_id": str(property_id),
        "tenant_id": str(tenant_id),
        "contract_id": str(contract_id),
        "lease_end_date": lease_end.date().isoformat(),
    }


@router.get("/renewal-source/{marker}")
async def inspect_renewal_source(
    marker: str,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    """Return presence/integrity only; never return fixture contact fields."""
    del admin
    _assert_allowed()
    marker = _marker_or_400(marker)
    prop = await db.properties.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    tenant = await db.tenants.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    contract = await db.rental_contracts.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    consistent = bool(
        prop
        and tenant
        and contract
        and prop.get("current_contract_id") == str(contract["_id"])
        and tenant.get("current_contract_id") == str(contract["_id"])
        and contract.get("property_id") == str(prop["_id"])
        and contract.get("tenant_id") == str(tenant["_id"])
    )
    return {
        "ok": True,
        "synthetic": True,
        "marker": marker,
        "present": {
            "property": bool(prop),
            "tenant": bool(tenant),
            "contract": bool(contract),
        },
        "consistent": consistent,
    }


@router.delete("/renewal-source/{marker}")
async def delete_renewal_source(
    marker: str,
    confirmation: str = Query(default=""),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    """Delete only an exact synthetic source marker, and never derived lifecycle data."""
    del admin
    _assert_allowed()
    marker = _marker_or_400(marker)
    if confirmation != _DELETE_CONFIRMATION:
        raise HTTPException(status_code=400, detail="fixture_delete_confirmation_required")

    contract = await db.rental_contracts.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    if contract and await db.lease_renewal_proposals.find_one(
        {"lease_id": str(contract["_id"])}
    ):
        raise HTTPException(status_code=409, detail="fixture_has_derived_lifecycle_data")

    deleted = await _cleanup_source(db, marker)
    return {
        "ok": True,
        "synthetic": True,
        "marker": marker,
        "deleted": deleted,
        "clean": sum(deleted.values()) == 3,
    }


def _binding_mismatch(doc: dict | None, expected: dict) -> bool:
    return bool(doc) and any(str(doc.get(key) or "") != str(value) for key, value in expected.items())


@router.delete("/renewal-lifecycle/{marker}")
async def delete_renewal_lifecycle(
    marker: str,
    confirmation: str = Query(default=""),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    """Delete an exact synthetic renewal graph after verifying every binding."""
    del admin
    _assert_allowed()
    marker = _marker_or_400(marker)
    if confirmation != _DELETE_CONFIRMATION:
        raise HTTPException(status_code=400, detail="fixture_delete_confirmation_required")

    old = await db.rental_contracts.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    prop = await db.properties.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    tenant = await db.tenants.find_one(
        {"staging_fixture_marker": marker, "synthetic": True}
    )
    if not old or not prop or not tenant:
        raise HTTPException(status_code=409, detail="fixture_source_incomplete")

    old_id, property_id, tenant_id = str(old["_id"]), str(prop["_id"]), str(tenant["_id"])
    if _binding_mismatch(
        old, {"property_id": property_id, "tenant_id": tenant_id}
    ):
        raise HTTPException(status_code=409, detail="fixture_source_binding_changed")

    proposal = await db.lease_renewal_proposals.find_one({"lease_id": old_id})
    proposal_id = str(proposal.get("_id") or "") if proposal else ""
    expected = {
        "lease_id": old_id,
        "property_id": property_id,
        "tenant_id": tenant_id,
    }
    if _binding_mismatch(proposal, expected):
        raise HTTPException(status_code=409, detail="fixture_proposal_binding_changed")

    response = outbox = renewal = rollover = None
    renewal_id = None
    if proposal:
        if not ObjectId.is_valid(proposal_id):
            raise HTTPException(status_code=409, detail="fixture_proposal_id_invalid")
        renewal_id = _renewal_contract_id(proposal_id)
        response = await db.lease_renewal_responses.find_one({"proposal_id": proposal_id})
        outbox = await db.lease_renewal_notification_outbox.find_one({"proposal_id": proposal_id})
        renewal = await db.rental_contracts.find_one({"_id": renewal_id})
        rollover = await db.lease_renewal_rollovers.find_one({"_id": renewal_id})

        if _binding_mismatch(response, {"proposal_id": proposal_id, **expected}):
            raise HTTPException(status_code=409, detail="fixture_response_binding_changed")
        if _binding_mismatch(outbox, {"proposal_id": proposal_id, "tenant_id": tenant_id}):
            raise HTTPException(status_code=409, detail="fixture_outbox_binding_changed")
        renewal_source = (renewal or {}).get("renewal_source") or {}
        if renewal and (
            _binding_mismatch(renewal, {"property_id": property_id, "tenant_id": tenant_id})
            or _binding_mismatch(
                renewal_source,
                {"proposal_id": proposal_id, "prior_contract_id": old_id},
            )
        ):
            raise HTTPException(status_code=409, detail="fixture_renewal_contract_binding_changed")
        if _binding_mismatch(
            rollover,
            {
                "proposal_id": proposal_id,
                "prior_contract_id": old_id,
                "renewal_contract_id": str(renewal_id),
                "property_id": property_id,
                "tenant_id": tenant_id,
            },
        ):
            raise HTTPException(status_code=409, detail="fixture_rollover_binding_changed")

    deleted: dict[str, int] = {}
    if proposal:
        targets = (
            ("lease_renewal_rollovers", {"_id": renewal_id, "proposal_id": proposal_id}),
            ("rental_contracts", {"_id": renewal_id, "renewal_source.proposal_id": proposal_id}),
            ("lease_renewal_responses", {"proposal_id": proposal_id, "lease_id": old_id}),
            ("lease_renewal_notification_outbox", {"proposal_id": proposal_id, "tenant_id": tenant_id}),
            ("lease_renewal_proposals", {"_id": proposal["_id"], "lease_id": old_id}),
        )
        for name, query in targets:
            result = await getattr(db, name).delete_many(query)
            deleted[name] = int(result.deleted_count)

    source_deleted = await _cleanup_source(db, marker)
    for name, count in source_deleted.items():
        deleted[f"source_{name}"] = count
    clean = all(count == 1 for count in source_deleted.values())
    return {
        "ok": True,
        "synthetic": True,
        "marker": marker,
        "deleted": deleted,
        "clean": clean,
    }
