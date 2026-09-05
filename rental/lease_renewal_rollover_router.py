"""Fail-closed rollover from an expired active lease to its signed renewal.

Both contracts retain the same durable lifecycle claim throughout the
multi-document transfer. Any partial failure therefore blocks later
property-scoped mutations and is inspected separately; it is never retried by
inference.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from .lease_renewal_contract_generation_router import _renewal_contract_id
from .lease_renewal_security_router import _rent
from .lease_renewal_tenant_response_router import _digest
from .property_mutation_lock import (
    acquire_property_mutation_lock,
    assert_property_lifecycle_recovery_clear,
    release_property_mutation_lock,
)
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewal Rollover"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date(value: Any, detail: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=detail) from exc


async def _load_pair(db, proposal_id: str):
    if not ObjectId.is_valid(proposal_id):
        raise HTTPException(status_code=400, detail="renewal_proposal_id_invalid")
    new_oid = _renewal_contract_id(proposal_id)
    new = await db.rental_contracts.find_one({"_id": new_oid})
    if not new:
        raise HTTPException(status_code=404, detail="renewal_contract_not_found")
    source = new.get("renewal_source") or {}
    if source.get("proposal_id") != proposal_id:
        raise HTTPException(status_code=409, detail="renewal_contract_source_invalid")
    old_id = str(source.get("prior_contract_id") or "")
    if not ObjectId.is_valid(old_id):
        raise HTTPException(status_code=409, detail="renewal_prior_contract_invalid")
    old = await db.rental_contracts.find_one({"_id": ObjectId(old_id)})
    if not old:
        raise HTTPException(status_code=409, detail="renewal_prior_contract_missing")
    for field in ("property_id", "tenant_id", "unit_id"):
        if str(old.get(field) or "") != str(new.get(field) or ""):
            raise HTTPException(status_code=409, detail=f"renewal_rollover_{field}_mismatch")
    return old, new


async def _assert_lineage(
    db, proposal_id: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    proposal = await db.lease_renewal_proposals.find_one(
        {"_id": ObjectId(proposal_id)}
    )
    if not proposal or proposal.get("status") != "approved":
        raise HTTPException(status_code=409, detail="renewal_rollover_proposal_invalid")

    expected = {
        "lease_id": str(old["_id"]),
        "property_id": str(old.get("property_id") or ""),
        "tenant_id": str(old.get("tenant_id") or ""),
    }
    if any(str(proposal.get(key) or "") != value for key, value in expected.items()):
        raise HTTPException(
            status_code=409, detail="renewal_rollover_proposal_binding_changed"
        )
    if str(proposal.get("unit_id") or old.get("unit_id") or "").strip() != str(
        old.get("unit_id") or ""
    ).strip():
        raise HTTPException(
            status_code=409, detail="renewal_rollover_proposal_unit_changed"
        )

    responses = await db.lease_renewal_responses.find(
        {"proposal_id": proposal_id}
    ).limit(2).to_list(2)
    if len(responses) != 1:
        raise HTTPException(
            status_code=409, detail="renewal_rollover_response_cardinality_invalid"
        )
    response = responses[0]
    if response.get("_id") != proposal.get("_id") or response.get("decision") != "accept":
        raise HTTPException(status_code=409, detail="renewal_rollover_response_invalid")
    if any(str(response.get(key) or "") != value for key, value in expected.items()):
        raise HTTPException(
            status_code=409, detail="renewal_rollover_response_binding_changed"
        )

    terms = response.get("terms")
    if not isinstance(terms, dict):
        raise HTTPException(
            status_code=409, detail="renewal_rollover_response_terms_invalid"
        )
    recommendation = str(proposal.get("recommendation") or "").strip().lower()
    if recommendation not in {"renew", "raise"}:
        raise HTTPException(
            status_code=409, detail="renewal_rollover_proposal_recommendation_invalid"
        )
    terms_expected = {
        "proposal_id": proposal_id,
        **expected,
        "recommendation": recommendation,
        "current_rent": f"{_rent(old.get('rent_amount'), 'renewal_rollover_prior_rent_invalid'):.2f}",
        "proposed_rent": f"{_rent(proposal.get('proposed_rent'), 'renewal_rollover_proposal_rent_invalid'):.2f}",
        "lease_end_date": _date(
            old.get("end_date"), "renewal_prior_end_date_invalid"
        ).isoformat(),
    }
    if terms != terms_expected:
        raise HTTPException(
            status_code=409, detail="renewal_rollover_response_terms_invalid"
        )
    digest = _digest(terms)
    stored_digest = str(response.get("terms_digest") or "").lower()
    source = new.get("renewal_source") or {}
    if (
        len(stored_digest) != 64
        or not secrets.compare_digest(stored_digest, digest)
        or str(source.get("response_id") or "") != str(response["_id"])
        or not secrets.compare_digest(
            str(source.get("terms_digest") or "").lower(), digest
        )
    ):
        raise HTTPException(
            status_code=409, detail="renewal_rollover_terms_lineage_changed"
        )

    response_rent = _rent(
        terms.get("proposed_rent"), "renewal_rollover_response_rent_invalid"
    )
    proposal_rent = _rent(
        proposal.get("proposed_rent"), "renewal_rollover_proposal_rent_invalid"
    )
    renewal_rent = _rent(
        new.get("rent_amount"), "renewal_rollover_contract_rent_invalid"
    )
    if (
        abs(response_rent - proposal_rent) > 0.005
        or abs(renewal_rent - proposal_rent) > 0.005
    ):
        raise HTTPException(
            status_code=409, detail="renewal_rollover_rent_lineage_changed"
        )


def _assert_ready(old: Dict[str, Any], new: Dict[str, Any], today: date) -> None:
    if old.get("status") != "active" or new.get("status") != "pending_activation":
        raise HTTPException(status_code=409, detail="renewal_rollover_state_invalid")
    if not new.get("tenant_signature") or not (new.get("landlord_signature") or new.get("admin_signature")):
        raise HTTPException(status_code=409, detail="renewal_rollover_signatures_missing")
    start = _date(new.get("start_date"), "renewal_start_date_invalid")
    end = _date(new.get("end_date"), "renewal_end_date_invalid")
    old_end = _date(old.get("end_date"), "renewal_prior_end_date_invalid")
    if end < start:
        raise HTTPException(status_code=409, detail="renewal_rollover_term_invalid")
    if (start - old_end).days != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_dates_not_contiguous")
    if today < start:
        raise HTTPException(status_code=409, detail="renewal_rollover_too_early")
    if today > end:
        raise HTTPException(status_code=409, detail="renewal_rollover_term_elapsed")


async def _assert_single_active(db, old: Dict[str, Any]) -> None:
    active = await db.rental_contracts.find({
        "tenant_id": str(old.get("tenant_id") or ""), "status": "active"
    }).limit(2).to_list(2)
    if len(active) != 1 or active[0].get("_id") != old.get("_id"):
        raise HTTPException(status_code=409, detail="renewal_rollover_active_authority_changed")

    unit_id = str(old.get("unit_id") or "").strip()
    resource = (
        {"unit_id": unit_id}
        if unit_id
        else {"property_id": str(old.get("property_id") or ""), "unit_id": {"$in": [None, ""]}}
    )
    resource_active = await db.rental_contracts.find({
        **resource, "status": "active"
    }).limit(2).to_list(2)
    if len(resource_active) != 1 or resource_active[0].get("_id") != old.get("_id"):
        raise HTTPException(
            status_code=409, detail="renewal_rollover_resource_active_authority_changed"
        )


async def _transfer_projection(db, old: Dict[str, Any], new: Dict[str, Any], now: datetime) -> None:
    old_id, new_id = str(old["_id"]), str(new["_id"])
    tenant_id = str(old.get("tenant_id") or "")
    property_id = str(old.get("property_id") or "")
    unit_id = str(old.get("unit_id") or "").strip()
    if unit_id:
        result = await db.property_units.update_one(
            {"_id": ObjectId(unit_id), "property_id": property_id, "status": "rented",
             "current_contract_id": old_id, "current_tenant_id": tenant_id},
            {"$set": {"current_contract_id": new_id, "updated_at": now}},
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_unit_projection_changed")
    else:
        result = await db.properties.update_one(
            {"_id": ObjectId(property_id), "status": "rented", "status_manually_set": {"$ne": True},
             "current_contract_id": old_id, "current_tenant_id": tenant_id},
            {"$set": {"current_contract_id": new_id, "updated_at": now}},
        )
        if getattr(result, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_property_projection_changed")

    unit_expected = unit_id if unit_id else {"$in": [None, ""]}
    result = await db.tenants.update_one(
        {"_id": ObjectId(tenant_id), "current_contract_id": old_id,
         "current_property_id": property_id, "current_unit_id": unit_expected},
        {"$set": {"current_contract_id": new_id, "updated_at": now}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_tenant_projection_changed")


async def _rollover_under_lock(db, proposal_id: str, actor: str, today: Optional[date] = None):
    old, new = await _load_pair(db, proposal_id)
    rollover_oid = new["_id"]
    existing = await db.lease_renewal_rollovers.find_one({"_id": rollover_oid})
    if existing:
        if existing.get("state") == "completed" and existing.get("proposal_id") == proposal_id:
            return {"ok": True, "idempotent": True, "state": "completed", "contract_id": str(new["_id"])}
        if (
            existing.get("state") == "committed"
            and existing.get("proposal_id") == proposal_id
            and await _authority_fully_committed(db, old, new)
        ):
            return {"ok": True, "idempotent": True, "state": "committed", "contract_id": str(new["_id"])}
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_pending")
    await _assert_lineage(db, proposal_id, old, new)
    _assert_ready(old, new, today or _now().date())
    await _assert_single_active(db, old)

    claim_id = secrets.token_hex(16)
    now = _now()
    record = {
        "_id": rollover_oid,
        "proposal_id": proposal_id,
        "prior_contract_id": str(old["_id"]),
        "renewal_contract_id": str(new["_id"]),
        "property_id": str(new.get("property_id") or ""),
        "tenant_id": str(new.get("tenant_id") or ""),
        "claim_id": claim_id,
        "state": "claimed",
        "stage": "record_created",
        "created_by": actor,
        "created_at": now,
        "updated_at": now,
        "automatic_retry_allowed": False,
    }
    try:
        await db.lease_renewal_rollovers.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_pending")

    stage = "claim_prior"
    try:
        old_claim = await db.rental_contracts.update_one(
            {"_id": old["_id"], "status": "active",
             "$or": [{"lifecycle_claim_id": {"$exists": False}}, {"lifecycle_claim_id": None}]},
            {"$set": {"lifecycle_claim_id": claim_id, "lifecycle_claim_target": "renewal_rollover",
                      "lifecycle_claimed_at": now, "renewal_rollover_contract_id": str(new["_id"])}},
        )
        if getattr(old_claim, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_prior_claim_changed")

        stage = "claim_renewal"
        new_claim = await db.rental_contracts.update_one(
            {"_id": new["_id"], "status": "pending_activation",
             "$or": [{"lifecycle_claim_id": {"$exists": False}}, {"lifecycle_claim_id": None}]},
            {"$set": {"lifecycle_claim_id": claim_id, "lifecycle_claim_target": "renewal_rollover",
                      "lifecycle_claimed_at": now, "renewal_rollover_prior_contract_id": str(old["_id"])}},
        )
        if getattr(new_claim, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_new_claim_changed")

        transferring = await db.lease_renewal_rollovers.update_one(
            {"_id": rollover_oid, "state": "claimed", "claim_id": claim_id},
            {"$set": {"state": "transferring", "stage": "transfer_projections", "updated_at": _now()}},
        )
        if getattr(transferring, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_record_changed")
        stage = "transfer_projections"
        await _transfer_projection(db, old, new, _now())

        stage = "expire_prior"
        expired = await db.rental_contracts.update_one(
            {"_id": old["_id"], "status": "active", "lifecycle_claim_id": claim_id},
            {"$set": {"status": "expired", "updated_at": _now(),
                      "renewed_to_contract_id": str(new["_id"])}},
        )
        if getattr(expired, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_prior_status_changed")

        stage = "activate_renewal"
        activated = await db.rental_contracts.update_one(
            {"_id": new["_id"], "status": "pending_activation", "lifecycle_claim_id": claim_id},
            {"$set": {"status": "active", "updated_at": _now(),
                      "renewed_from_contract_id": str(old["_id"])}},
        )
        if getattr(activated, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_new_status_changed")

        stage = "commit_record"
        committed = await db.lease_renewal_rollovers.update_one(
            {"_id": rollover_oid, "claim_id": claim_id, "state": "transferring"},
            {"$set": {"state": "committed", "stage": "clear_claims", "committed_at": _now(), "updated_at": _now()}},
        )
        if getattr(committed, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_record_changed")

        stage = "clear_prior_claim"
        cleared_old = await db.rental_contracts.update_one(
            {"_id": old["_id"], "status": "expired", "lifecycle_claim_id": claim_id},
            {"$unset": {"lifecycle_claim_id": "", "lifecycle_claim_target": "", "lifecycle_claimed_at": ""}},
        )
        if getattr(cleared_old, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_prior_claim_clear_changed")

        stage = "clear_renewal_claim"
        cleared_new = await db.rental_contracts.update_one(
            {"_id": new["_id"], "status": "active", "lifecycle_claim_id": claim_id},
            {"$unset": {"lifecycle_claim_id": "", "lifecycle_claim_target": "", "lifecycle_claimed_at": ""}},
        )
        if getattr(cleared_new, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_new_claim_clear_changed")

        stage = "complete"
        completed = await db.lease_renewal_rollovers.update_one(
            {"_id": rollover_oid, "claim_id": claim_id, "state": "committed"},
            {"$set": {"state": "completed", "stage": "complete", "completed_at": _now(), "updated_at": _now()}},
        )
        if getattr(completed, "matched_count", 0) != 1:
            latest = await db.lease_renewal_rollovers.find_one({"_id": rollover_oid, "claim_id": claim_id})
            if (latest or {}).get("state") == "committed" and await _authority_fully_committed(db, old, new):
                return {"ok": True, "idempotent": False, "state": "committed", "contract_id": str(new["_id"])}
            raise HTTPException(status_code=409, detail="renewal_rollover_completion_changed")
    except Exception:
        try:
            await db.lease_renewal_rollovers.update_one(
                {"_id": rollover_oid, "claim_id": claim_id, "state": {"$ne": "completed"}},
                {"$set": {"state": "recovery_required", "stage": stage,
                          "automatic_retry_allowed": False, "updated_at": _now()}},
            )
        except Exception:
            pass
        raise
    return {"ok": True, "idempotent": False, "state": "completed", "contract_id": str(new["_id"])}


async def _projection_view(db, old: Dict[str, Any], new: Dict[str, Any]):
    unit_id = str(new.get("unit_id") or "").strip()
    if unit_id:
        resource = await db.property_units.find_one({"_id": ObjectId(unit_id)})
    else:
        resource = await db.properties.find_one({"_id": ObjectId(str(new.get("property_id") or ""))})
    tenant = await db.tenants.find_one({"_id": ObjectId(str(new.get("tenant_id") or ""))})
    owner = str((resource or {}).get("current_contract_id") or "")
    tenant_owner = str((tenant or {}).get("current_contract_id") or "")
    return {
        "resource_owner": "prior" if owner == str(old["_id"]) else "renewal" if owner == str(new["_id"]) else "other_or_missing",
        "tenant_owner": "prior" if tenant_owner == str(old["_id"]) else "renewal" if tenant_owner == str(new["_id"]) else "other_or_missing",
    }


async def _authority_fully_committed(db, old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    if old.get("status") != "expired" or new.get("status") != "active":
        return False
    if old.get("lifecycle_claim_id") or new.get("lifecycle_claim_id"):
        return False
    observed = await _projection_view(db, old, new)
    return observed == {"resource_owner": "renewal", "tenant_owner": "renewal"}


@router.post("/{proposal_id}/rollover")
async def rollover_renewal(
    proposal_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    if body not in (None, {}):
        raise HTTPException(status_code=400, detail="renewal_rollover_payload_must_be_empty")
    old, new = await _load_pair(db, proposal_id)
    property_id = str(new.get("property_id") or "")
    actor = str(admin.get("_id") or admin.get("email") or "admin") if isinstance(admin, dict) else str(admin)
    token = await acquire_property_mutation_lock(property_id, "renewal_rollover", actor)
    try:
        await assert_property_lifecycle_recovery_clear(property_id)
        return await _rollover_under_lock(db, proposal_id, actor)
    finally:
        await release_property_mutation_lock(property_id, token)


@router.get("/{proposal_id}/rollover-inspection/{rollover_id}")
async def inspect_rollover(
    proposal_id: str,
    rollover_id: str,
    db=Depends(get_db),
    admin=Depends(auth_admin),
):
    del admin
    if not ObjectId.is_valid(rollover_id):
        raise HTTPException(status_code=400, detail="renewal_rollover_id_invalid")
    record = await db.lease_renewal_rollovers.find_one({"_id": ObjectId(rollover_id), "proposal_id": proposal_id})
    if not record:
        raise HTTPException(status_code=404, detail="renewal_rollover_not_found")
    old, new = await _load_pair(db, proposal_id)
    claim = str(record.get("claim_id") or "")
    return {
        "ok": True,
        "read_only": True,
        "automatic_retry_allowed": False,
        "rollover_id": rollover_id,
        "proposal_id": proposal_id,
        "state": record.get("state"),
        "stage": record.get("stage"),
        "prior_status": old.get("status"),
        "renewal_status": new.get("status"),
        "prior_claim_exact": bool(claim and old.get("lifecycle_claim_id") == claim),
        "renewal_claim_exact": bool(claim and new.get("lifecycle_claim_id") == claim),
        "projections": await _projection_view(db, old, new),
    }


async def ensure_indexes(db) -> None:
    await db.lease_renewal_rollovers.create_index("proposal_id", unique=True)
    await db.lease_renewal_rollovers.create_index([("state", 1), ("updated_at", 1)])
