"""Two-admin, completion-only recovery for partial lease-renewal rollovers.

Recovery never guesses a rollback. An administrator first records the exact
sanitized observation digest and a different administrator confirms it. The
confirmation runs under the property mutation lock and can only converge the
already-signed renewal toward the normal completed authority state.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException

from .lease_renewal_rollover_router import _assert_lineage, _load_pair
from .property_mutation_lock import acquire_property_mutation_lock, release_property_mutation_lock
from .shared import auth_admin, get_db

router = APIRouter(prefix="/admin/lease-renewals", tags=["Lease Renewal Rollover Recovery"])

_RECOVERY_STAGES_BY_STATE = {
    "recovery_required": {
        "claim_prior", "claim_renewal", "transfer_projections", "expire_prior",
        "activate_renewal", "commit_record", "clear_prior_claim",
        "clear_renewal_claim", "complete", "normalize_projections",
        "manual_recovery_confirmed",
    },
    "committed": {
        "clear_claims", "manual_recovery_clear_claims",
        "manual_recovery_finalize_record", "manual_recovery_confirmed",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_keys(admin: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(admin, dict):
        if admin.get("_id"):
            keys.add("id:" + str(admin["_id"]).strip().lower())
        if admin.get("email"):
            keys.add("email:" + str(admin["email"]).strip().lower())
    else:
        value = str(admin or "").strip().lower()
        if value:
            keys.add(value if value.startswith(("id:", "email:", "actor:")) else "actor:" + value)
    if not keys:
        raise HTTPException(status_code=403, detail="renewal_rollover_recovery_admin_identity_missing")
    return keys


def _actor_key(admin: Any) -> str:
    return sorted(_actor_keys(admin))[0]


def _relation(value: Any, expected: str) -> str:
    if value in (None, ""):
        return "missing"
    return "exact" if str(value) == expected else "foreign"


def _owner(value: Any, old_id: str, new_id: str) -> str:
    value = str(value or "")
    return "prior" if value == old_id else "renewal" if value == new_id else "other_or_missing"


async def _record_and_pair(db, proposal_id: str, rollover_id: str):
    if not ObjectId.is_valid(rollover_id):
        raise HTTPException(status_code=400, detail="renewal_rollover_id_invalid")
    record = await db.lease_renewal_rollovers.find_one(
        {"_id": ObjectId(rollover_id), "proposal_id": proposal_id}
    )
    if not record:
        raise HTTPException(status_code=404, detail="renewal_rollover_not_found")
    old, new = await _load_pair(db, proposal_id)
    await _assert_lineage(db, proposal_id, old, new)
    if (
        record.get("_id") != new["_id"]
        or
        str(record.get("prior_contract_id") or "") != str(old["_id"])
        or str(record.get("renewal_contract_id") or "") != str(new["_id"])
        or str(record.get("property_id") or "") != str(new.get("property_id") or "")
        or str(record.get("tenant_id") or "") != str(new.get("tenant_id") or "")
    ):
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_record_mismatch")
    return record, old, new


async def _observation(db, record: Dict[str, Any], old: Dict[str, Any], new: Dict[str, Any]):
    old_id, new_id = str(old["_id"]), str(new["_id"])
    property_id = str(new.get("property_id") or "")
    tenant_id = str(new.get("tenant_id") or "")
    unit_id = str(new.get("unit_id") or "").strip()
    resource = await (
        db.property_units.find_one({"_id": ObjectId(unit_id)})
        if unit_id
        else db.properties.find_one({"_id": ObjectId(property_id)})
    )
    tenant = await db.tenants.find_one({"_id": ObjectId(tenant_id)})
    claim = str(record.get("claim_id") or "")
    if not claim:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_claim_missing")
    return {
        "record_state": str(record.get("state") or ""),
        "record_stage": str(record.get("stage") or ""),
        "automatic_retry_disabled": record.get("automatic_retry_allowed") is False,
        "prior_status": str(old.get("status") or ""),
        "renewal_status": str(new.get("status") or ""),
        "prior_claim": _relation(old.get("lifecycle_claim_id"), claim),
        "renewal_claim": _relation(new.get("lifecycle_claim_id"), claim),
        "prior_claim_target_exact": old.get("lifecycle_claim_id") in (None, "") or old.get("lifecycle_claim_target") == "renewal_rollover",
        "renewal_claim_target_exact": new.get("lifecycle_claim_id") in (None, "") or new.get("lifecycle_claim_target") == "renewal_rollover",
        "resource_owner": _owner((resource or {}).get("current_contract_id"), old_id, new_id),
        "resource_status": str((resource or {}).get("status") or ""),
        "resource_manually_set": bool((resource or {}).get("status_manually_set")) if not unit_id else False,
        "resource_tenant_exact": str((resource or {}).get("current_tenant_id") or "") == tenant_id,
        "tenant_owner": _owner((tenant or {}).get("current_contract_id"), old_id, new_id),
        "tenant_property_exact": str((tenant or {}).get("current_property_id") or "") == property_id,
        "tenant_unit_exact": str((tenant or {}).get("current_unit_id") or "").strip() == unit_id,
    }


def _digest(observation: Dict[str, Any]) -> str:
    canonical = json.dumps(observation, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pending_confirmation(record: Dict[str, Any], current_digest: str):
    recovery = record.get("manual_recovery") or {}
    if recovery.get("status") != "proposed":
        return None
    recovery_id = str(recovery.get("recovery_id") or "").lower()
    observed_digest = str(recovery.get("observed_digest") or "").lower()
    if (
        recovery.get("action") != "complete"
        or len(recovery_id) != 32
        or any(c not in "0123456789abcdef" for c in recovery_id)
        or len(observed_digest) != 64
        or any(c not in "0123456789abcdef" for c in observed_digest)
    ):
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_proposal_invalid")
    matches = secrets.compare_digest(observed_digest, current_digest)
    return {
        "status": "pending_confirmation",
        "action": "complete",
        "recovery_id": recovery_id,
        "observed_digest": observed_digest,
        "observation_matches": matches,
        "confirmable": matches,
        "requires_second_admin": True,
    }


def _assert_recoverable(observed: Dict[str, Any]) -> None:
    if not observed["automatic_retry_disabled"]:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_fence_invalid")
    allowed_stages = _RECOVERY_STAGES_BY_STATE.get(observed["record_state"])
    if allowed_stages is None:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_state_invalid")
    if observed["record_stage"] not in allowed_stages:
        raise HTTPException(
            status_code=409, detail="renewal_rollover_recovery_state_stage_invalid"
        )
    if observed["prior_claim"] == "foreign" or observed["renewal_claim"] == "foreign":
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_foreign_claim")
    if not observed["prior_claim_target_exact"] or not observed["renewal_claim_target_exact"]:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_claim_target_changed")
    fully_committed = (
        observed["prior_status"] == "expired"
        and observed["renewal_status"] == "active"
        and observed["resource_owner"] == "renewal"
        and observed["tenant_owner"] == "renewal"
        and observed["prior_claim"] == "missing"
        and observed["renewal_claim"] == "missing"
    )
    if not fully_committed and "exact" not in {observed["prior_claim"], observed["renewal_claim"]}:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_authority_unclaimed")
    if observed["prior_status"] not in {"active", "expired"}:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_prior_status_invalid")
    if observed["renewal_status"] not in {"pending_activation", "active"}:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_new_status_invalid")
    if observed["resource_owner"] not in {"prior", "renewal"} or observed["tenant_owner"] not in {"prior", "renewal"}:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_projection_foreign")
    if observed["resource_status"] != "rented" or observed["resource_manually_set"] or not all(
        (observed["resource_tenant_exact"], observed["tenant_property_exact"], observed["tenant_unit_exact"])
    ):
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_projection_context_changed")


async def _assert_no_foreign_property_claim(
    db, property_id: str, claim: str, allowed_contract_ids: set[ObjectId]
) -> None:
    rows = await db.rental_contracts.find({"property_id": property_id}).to_list(1000)
    for row in rows:
        row_claim = row.get("lifecycle_claim_id")
        if row_claim in (None, ""):
            continue
        if row.get("_id") not in allowed_contract_ids or row_claim != claim:
            raise HTTPException(status_code=409, detail="renewal_rollover_recovery_property_foreign_claim")


async def _set_projection_to_renewal(db, old: Dict[str, Any], new: Dict[str, Any], now: datetime) -> None:
    old_id, new_id = str(old["_id"]), str(new["_id"])
    property_id = str(new.get("property_id") or "")
    tenant_id = str(new.get("tenant_id") or "")
    unit_id = str(new.get("unit_id") or "").strip()
    if unit_id:
        result = await db.property_units.update_one(
            {"_id": ObjectId(unit_id), "property_id": property_id, "status": "rented",
             "current_tenant_id": tenant_id, "current_contract_id": {"$in": [old_id, new_id]}},
            {"$set": {"current_contract_id": new_id, "updated_at": now}},
        )
    else:
        result = await db.properties.update_one(
            {"_id": ObjectId(property_id), "status": "rented", "status_manually_set": {"$ne": True},
             "current_tenant_id": tenant_id, "current_contract_id": {"$in": [old_id, new_id]}},
            {"$set": {"current_contract_id": new_id, "updated_at": now}},
        )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_resource_changed")
    result = await db.tenants.update_one(
        {"_id": ObjectId(tenant_id), "current_property_id": property_id,
         "current_unit_id": unit_id if unit_id else {"$in": [None, ""]},
         "current_contract_id": {"$in": [old_id, new_id]}},
        {"$set": {"current_contract_id": new_id, "updated_at": now}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_tenant_changed")


async def _set_status(db, contract: Dict[str, Any], allowed: list[str], target: str, claim: str, link: Dict[str, str]) -> None:
    relation = _relation(contract.get("lifecycle_claim_id"), claim)
    claim_query: Dict[str, Any]
    if relation == "exact":
        claim_query = {"lifecycle_claim_id": claim}
    elif relation == "missing":
        claim_query = {"$or": [{"lifecycle_claim_id": {"$exists": False}}, {"lifecycle_claim_id": None}, {"lifecycle_claim_id": ""}]}
    else:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_foreign_claim")
    result = await db.rental_contracts.update_one(
        {"_id": contract["_id"], "status": {"$in": allowed}, **claim_query},
        {"$set": {"status": target, "updated_at": _now(), **link}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_contract_changed")


async def _clear_exact_claim(db, contract_id: ObjectId, claim: str, target_status: str) -> None:
    current = await db.rental_contracts.find_one({"_id": contract_id})
    relation = _relation((current or {}).get("lifecycle_claim_id"), claim)
    if relation == "missing":
        return
    if relation == "foreign":
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_foreign_claim")
    result = await db.rental_contracts.update_one(
        {"_id": contract_id, "status": target_status, "lifecycle_claim_id": claim},
        {"$unset": {"lifecycle_claim_id": "", "lifecycle_claim_target": "", "lifecycle_claimed_at": ""}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_claim_clear_changed")


async def _complete(
    db, proposal_id: str, rollover_id: str, recovery_id: str, confirmer: str,
    confirmer_keys: set[str] | None = None,
):
    record, old, new = await _record_and_pair(db, proposal_id, rollover_id)
    recovery = record.get("manual_recovery") or {}
    status = recovery.get("status")
    if recovery.get("recovery_id") != recovery_id or status not in {"proposed", "confirming"}:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_proposal_changed")
    actor_keys = confirmer_keys or {confirmer}
    proposed_keys = set(recovery.get("proposed_by_keys") or [recovery.get("proposed_by")])
    if proposed_keys.intersection(actor_keys):
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_second_admin_required")
    observed = await _observation(db, record, old, new)
    _assert_recoverable(observed)
    claim = str(record["claim_id"])

    if status == "proposed":
        if _digest(observed) != recovery.get("observed_digest"):
            raise HTTPException(status_code=409, detail="renewal_rollover_recovery_observation_changed")
        started = await db.lease_renewal_rollovers.update_one(
            {"_id": record["_id"], "proposal_id": proposal_id, "claim_id": claim,
             "state": record["state"], "stage": record["stage"],
             "manual_recovery.recovery_id": recovery_id, "manual_recovery.status": "proposed",
             "manual_recovery.observed_digest": recovery["observed_digest"]},
            {"$set": {"manual_recovery.status": "confirming",
                      "manual_recovery.confirmed_by": confirmer,
                      "manual_recovery.confirmed_by_keys": sorted(actor_keys),
                      "manual_recovery.confirmed_at": _now(),
                      "stage": "manual_recovery_confirmed", "updated_at": _now()}},
        )
        if getattr(started, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_recovery_proposal_changed")
    else:
        confirmed_keys = set(
            recovery.get("confirmed_by_keys") or [recovery.get("confirmed_by")]
        )
        if not confirmed_keys.intersection(actor_keys):
            raise HTTPException(
                status_code=409, detail="renewal_rollover_recovery_confirmer_changed"
            )

    await _assert_no_foreign_property_claim(
        db, str(record["property_id"]), claim, {old["_id"], new["_id"]}
    )
    stage = "normalize_projections"
    try:
        await _set_projection_to_renewal(db, old, new, _now())
        stage = "expire_prior"
        await _set_status(db, old, ["active", "expired"], "expired", claim,
                          {"renewed_to_contract_id": str(new["_id"])})
        stage = "activate_renewal"
        await _set_status(db, new, ["pending_activation", "active"], "active", claim,
                          {"renewed_from_contract_id": str(old["_id"])})
        stage = "commit_record"
        committed = await db.lease_renewal_rollovers.update_one(
            {"_id": record["_id"], "claim_id": claim, "manual_recovery.recovery_id": recovery_id,
             "manual_recovery.status": "confirming"},
            {"$set": {"state": "committed", "stage": "manual_recovery_clear_claims",
                      "committed_at": _now(), "updated_at": _now()}},
        )
        if getattr(committed, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_recovery_record_changed")
        stage = "clear_prior_claim"
        await _clear_exact_claim(db, old["_id"], claim, "expired")
        stage = "clear_renewal_claim"
        await _clear_exact_claim(db, new["_id"], claim, "active")
        stage = "complete_record"
        completed = await db.lease_renewal_rollovers.update_one(
            {"_id": record["_id"], "claim_id": claim, "state": "committed",
             "manual_recovery.recovery_id": recovery_id, "manual_recovery.status": "confirming"},
            {"$set": {"state": "completed", "stage": "complete", "completed_at": _now(),
                      "updated_at": _now(), "manual_recovery.status": "confirmed"}},
        )
        if getattr(completed, "matched_count", 0) != 1:
            raise HTTPException(status_code=409, detail="renewal_rollover_recovery_completion_changed")
    except Exception:
        latest_old = await db.rental_contracts.find_one({"_id": old["_id"]}) or {}
        latest_new = await db.rental_contracts.find_one({"_id": new["_id"]}) or {}
        latest_record = await db.lease_renewal_rollovers.find_one({"_id": record["_id"]}) or {}
        latest_observed = await _observation(db, latest_record, latest_old, latest_new)
        authority_complete = (
            latest_observed["prior_status"] == "expired" and latest_observed["renewal_status"] == "active"
            and latest_observed["resource_owner"] == "renewal" and latest_observed["tenant_owner"] == "renewal"
            and latest_observed["prior_claim"] == "missing" and latest_observed["renewal_claim"] == "missing"
        )
        await db.lease_renewal_rollovers.update_one(
            {"_id": record["_id"], "claim_id": claim, "state": {"$ne": "completed"}},
            {"$set": {"state": "committed" if authority_complete else "recovery_required",
                      "stage": "manual_recovery_finalize_record" if authority_complete else stage,
                      "automatic_retry_allowed": False, "manual_recovery.status": "failed", "updated_at": _now()}},
        )
        raise
    return {"ok": True, "state": "completed", "contract_id": str(new["_id"]), "recovery_id": recovery_id}


@router.get("/{proposal_id}/rollover-recovery/{rollover_id}")
async def observe_recovery(proposal_id: str, rollover_id: str, db=Depends(get_db), admin=Depends(auth_admin)):
    del admin
    record, old, new = await _record_and_pair(db, proposal_id, rollover_id)
    observed = await _observation(db, record, old, new)
    _assert_recoverable(observed)
    observed_digest = _digest(observed)
    return {"ok": True, "read_only": True, "automatic_retry_allowed": False,
            "rollover_id": rollover_id, "proposal_id": proposal_id,
            "observation": observed, "observed_digest": observed_digest,
            "pending_confirmation": _pending_confirmation(record, observed_digest)}


@router.post("/{proposal_id}/rollover-recovery/{rollover_id}/propose")
async def propose_recovery(proposal_id: str, rollover_id: str, body: Dict[str, Any] = Body(...),
                           db=Depends(get_db), admin=Depends(auth_admin)):
    if set(body) != {"action", "observed_digest"} or body.get("action") != "complete":
        raise HTTPException(status_code=400, detail="renewal_rollover_recovery_payload_invalid")
    supplied = str(body.get("observed_digest") or "").lower()
    if len(supplied) != 64 or any(c not in "0123456789abcdef" for c in supplied):
        raise HTTPException(status_code=400, detail="renewal_rollover_recovery_digest_invalid")
    record, old, new = await _record_and_pair(db, proposal_id, rollover_id)
    observed = await _observation(db, record, old, new)
    _assert_recoverable(observed)
    if _digest(observed) != supplied:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_observation_changed")
    current = record.get("manual_recovery") or {}
    if current.get("status") in {"proposed", "confirming"}:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_already_proposed")
    recovery_id = secrets.token_hex(16)
    result = await db.lease_renewal_rollovers.update_one(
        {"_id": record["_id"], "proposal_id": proposal_id, "claim_id": record["claim_id"],
         "state": record["state"], "stage": record["stage"],
         "$or": [{"manual_recovery": {"$exists": False}},
                 {"manual_recovery.status": {"$nin": ["proposed", "confirming"]}}]},
        {"$set": {"manual_recovery": {"recovery_id": recovery_id, "action": "complete",
                                      "observed_digest": supplied, "status": "proposed",
                                      "proposed_by": _actor_key(admin),
                                      "proposed_by_keys": sorted(_actor_keys(admin)), "proposed_at": _now()},
                  "updated_at": _now()}},
    )
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_observation_changed")
    return {"ok": True, "state": "pending_confirmation", "action": "complete", "recovery_id": recovery_id}


@router.post("/{proposal_id}/rollover-recovery/{rollover_id}/confirm")
async def confirm_recovery(proposal_id: str, rollover_id: str, body: Dict[str, Any] = Body(...),
                           db=Depends(get_db), admin=Depends(auth_admin)):
    if set(body) != {"recovery_id", "observed_digest"}:
        raise HTTPException(status_code=400, detail="renewal_rollover_recovery_payload_invalid")
    recovery_id = str(body.get("recovery_id") or "")
    digest = str(body.get("observed_digest") or "").lower()
    record, _old, new = await _record_and_pair(db, proposal_id, rollover_id)
    recovery = record.get("manual_recovery") or {}
    if recovery.get("recovery_id") != recovery_id or recovery.get("observed_digest") != digest:
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_proposal_changed")
    actor_keys = _actor_keys(admin)
    actor = sorted(actor_keys)[0]
    proposed_keys = set(recovery.get("proposed_by_keys") or [recovery.get("proposed_by")])
    if proposed_keys.intersection(actor_keys):
        raise HTTPException(status_code=409, detail="renewal_rollover_recovery_second_admin_required")
    property_id = str(new.get("property_id") or "")
    token = await acquire_property_mutation_lock(property_id, "renewal_rollover_recovery", actor, db=db)
    try:
        return await _complete(db, proposal_id, rollover_id, recovery_id, actor, actor_keys)
    finally:
        await release_property_mutation_lock(property_id, token, db=db)
