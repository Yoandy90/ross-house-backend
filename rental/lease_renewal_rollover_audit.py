"""Append-only, tamper-evident audit events for lease-renewal rollovers."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError


ROLLOVER_AUDIT_EVENTS = (
    "record_created",
    "prior_claimed",
    "renewal_claimed",
    "transfer_started",
    "projections_transferred",
    "prior_expired",
    "renewal_activated",
    "record_committed",
    "prior_claim_cleared",
    "renewal_claim_cleared",
    "rollover_completed",
)
_EVENT_SEQUENCE = {event: index + 1 for index, event in enumerate(ROLLOVER_AUDIT_EVENTS)}
_ALLOWED_EVIDENCE_KEYS = {"state", "stage"}


def _canonical(value: Dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _event_id(rollover_id: str, proposal_id: str, event: str) -> str:
    key = {"version": 1, "rollover_id": rollover_id, "proposal_id": proposal_id, "event": event}
    return hashlib.sha256(_canonical(key).encode("utf-8")).hexdigest()


def _integrity_body(
    rollover_id: str,
    proposal_id: str,
    event: str,
    actor: str,
    evidence: Dict[str, str],
    previous_digest: str,
) -> Dict[str, Any]:
    return {
        "version": 1,
        "rollover_id": rollover_id,
        "proposal_id": proposal_id,
        "sequence": _EVENT_SEQUENCE[event],
        "event": event,
        "actor": actor,
        "evidence": evidence,
        "previous_digest": previous_digest,
    }


def verify_rollover_audit_event(document: Dict[str, Any]) -> bool:
    event = str(document.get("event") or "")
    if event not in _EVENT_SEQUENCE:
        return False
    body = _integrity_body(
        str(document.get("rollover_id") or ""),
        str(document.get("proposal_id") or ""),
        event,
        str(document.get("actor") or ""),
        document.get("evidence") if isinstance(document.get("evidence"), dict) else {},
        str(document.get("previous_digest") or ""),
    )
    expected = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    stored = str(document.get("integrity_digest") or "")
    return len(stored) == 64 and secrets.compare_digest(stored, expected)


async def append_rollover_audit_event(
    db,
    *,
    rollover_id: str,
    proposal_id: str,
    event: str,
    actor: str,
    evidence: Dict[str, str],
) -> Dict[str, Any]:
    if event not in _EVENT_SEQUENCE:
        raise HTTPException(status_code=500, detail="renewal_rollover_audit_event_invalid")
    normalized_actor = str(actor or "").strip().lower()
    if not normalized_actor or len(normalized_actor) > 160:
        raise HTTPException(status_code=500, detail="renewal_rollover_audit_actor_invalid")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != _ALLOWED_EVIDENCE_KEYS
        or any(not isinstance(value, str) or len(value) > 80 for value in evidence.values())
    ):
        raise HTTPException(status_code=500, detail="renewal_rollover_audit_evidence_invalid")

    sequence = _EVENT_SEQUENCE[event]
    previous_digest = ""
    for previous_event in ROLLOVER_AUDIT_EVENTS[: sequence - 1]:
        previous = await db.lease_renewal_rollover_audit.find_one(
            {"_id": _event_id(rollover_id, proposal_id, previous_event)}
        )
        if (
            not previous
            or not verify_rollover_audit_event(previous)
            or not secrets.compare_digest(
                str(previous.get("previous_digest") or ""), previous_digest
            )
        ):
            raise HTTPException(status_code=409, detail="renewal_rollover_audit_chain_invalid")
        previous_digest = str(previous["integrity_digest"])

    body = _integrity_body(
        rollover_id, proposal_id, event, normalized_actor, dict(evidence), previous_digest
    )
    digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    document = {
        "_id": _event_id(rollover_id, proposal_id, event),
        **body,
        "integrity_digest": digest,
        "occurred_at": datetime.now(timezone.utc),
    }
    try:
        await db.lease_renewal_rollover_audit.insert_one(document)
        return {"idempotent": False, "integrity_digest": digest}
    except DuplicateKeyError:
        existing = await db.lease_renewal_rollover_audit.find_one({"_id": document["_id"]})
        if existing and verify_rollover_audit_event(existing) and all(
            existing.get(key) == value for key, value in body.items()
        ):
            return {"idempotent": True, "integrity_digest": digest}
        raise HTTPException(status_code=409, detail="renewal_rollover_audit_conflict")

def _safe_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return ""


def _safe_sequence(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


async def inspect_rollover_audit_chain(
    db, *, rollover_id: str, proposal_id: str
) -> Dict[str, Any]:
    rows = await db.lease_renewal_rollover_audit.find(
        {"rollover_id": rollover_id, "proposal_id": proposal_id}
    ).to_list(len(ROLLOVER_AUDIT_EVENTS) + 1)
    rows = sorted(rows, key=lambda row: _safe_sequence(row.get("sequence")))
    valid = len(rows) <= len(ROLLOVER_AUDIT_EVENTS)
    previous_digest = ""
    safe_events = []
    for index, row in enumerate(rows):
        expected_event = (
            ROLLOVER_AUDIT_EVENTS[index]
            if index < len(ROLLOVER_AUDIT_EVENTS)
            else ""
        )
        row_valid = (
            row.get("event") == expected_event
            and row.get("sequence") == index + 1
            and str(row.get("rollover_id") or "") == rollover_id
            and str(row.get("proposal_id") or "") == proposal_id
            and verify_rollover_audit_event(row)
            and secrets.compare_digest(
                str(row.get("previous_digest") or ""), previous_digest
            )
        )
        valid = valid and row_valid
        previous_digest = str(row.get("integrity_digest") or "")
        safe_events.append({
            "sequence": row.get("sequence"),
            "event": str(row.get("event") or ""),
            "occurred_at": _safe_timestamp(row.get("occurred_at")),
        })
    complete = valid and len(rows) == len(ROLLOVER_AUDIT_EVENTS)
    return {
        "status": "complete" if complete else "partial" if valid else "invalid",
        "valid": valid,
        "complete": complete,
        "recorded_events": len(rows),
        "expected_events": len(ROLLOVER_AUDIT_EVENTS),
        "last_event": safe_events[-1]["event"] if safe_events else None,
        "events": safe_events,
    }


async def ensure_indexes(db) -> None:
    """Keep audit inspection bounded and enforce one event per chain step."""
    await db.lease_renewal_rollover_audit.create_index(
        [("rollover_id", 1), ("proposal_id", 1), ("sequence", 1)],
        unique=True,
    )
    await db.lease_renewal_rollover_audit.create_index(
        [("occurred_at", -1), ("rollover_id", 1)]
    )
