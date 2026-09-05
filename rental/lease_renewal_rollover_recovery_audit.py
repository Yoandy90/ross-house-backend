"""Append-only, tamper-evident audit chain for manual rollover recovery."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError


RECOVERY_AUDIT_EVENTS = (
    "recovery_proposed",
    "recovery_execution_started",
    "recovery_record_committed",
    "recovery_completion_ready",
)
_EVENT_SEQUENCE = {event: index + 1 for index, event in enumerate(RECOVERY_AUDIT_EVENTS)}


def _canonical(value: Dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _event_id(rollover_id: str, proposal_id: str, recovery_id: str, event: str) -> str:
    key = {
        "version": 1,
        "rollover_id": rollover_id,
        "proposal_id": proposal_id,
        "recovery_id": recovery_id,
        "event": event,
    }
    return hashlib.sha256(_canonical(key).encode("utf-8")).hexdigest()


def _body(
    rollover_id: str,
    proposal_id: str,
    recovery_id: str,
    event: str,
    actor: str,
    previous_digest: str,
) -> Dict[str, Any]:
    return {
        "version": 1,
        "rollover_id": rollover_id,
        "proposal_id": proposal_id,
        "recovery_id": recovery_id,
        "sequence": _EVENT_SEQUENCE[event],
        "event": event,
        "actor": actor,
        "previous_digest": previous_digest,
    }


def verify_recovery_audit_event(document: Dict[str, Any]) -> bool:
    event = str(document.get("event") or "")
    if event not in _EVENT_SEQUENCE:
        return False
    body = _body(
        str(document.get("rollover_id") or ""),
        str(document.get("proposal_id") or ""),
        str(document.get("recovery_id") or ""),
        event,
        str(document.get("actor") or ""),
        str(document.get("previous_digest") or ""),
    )
    expected = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    stored = str(document.get("integrity_digest") or "")
    return len(stored) == 64 and secrets.compare_digest(stored, expected)


async def append_recovery_audit_event(
    db,
    *,
    rollover_id: str,
    proposal_id: str,
    recovery_id: str,
    event: str,
    actor: str,
) -> Dict[str, Any]:
    if event not in _EVENT_SEQUENCE:
        raise HTTPException(status_code=500, detail="renewal_recovery_audit_event_invalid")
    normalized_actor = str(actor or "").strip().lower()
    if not normalized_actor or len(normalized_actor) > 160:
        raise HTTPException(status_code=500, detail="renewal_recovery_audit_actor_invalid")
    if (
        len(recovery_id) != 32
        or any(char not in "0123456789abcdef" for char in recovery_id.lower())
    ):
        raise HTTPException(status_code=500, detail="renewal_recovery_audit_id_invalid")
    recovery_id = recovery_id.lower()

    sequence = _EVENT_SEQUENCE[event]
    previous_digest = ""
    for previous_event in RECOVERY_AUDIT_EVENTS[: sequence - 1]:
        previous = await db.lease_renewal_rollover_recovery_audit.find_one(
            {"_id": _event_id(rollover_id, proposal_id, recovery_id, previous_event)}
        )
        if (
            not previous
            or not verify_recovery_audit_event(previous)
            or not secrets.compare_digest(
                str(previous.get("previous_digest") or ""), previous_digest
            )
        ):
            raise HTTPException(status_code=409, detail="renewal_recovery_audit_chain_invalid")
        previous_digest = str(previous["integrity_digest"])

    body = _body(
        rollover_id, proposal_id, recovery_id, event, normalized_actor, previous_digest
    )
    digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    document = {
        "_id": _event_id(rollover_id, proposal_id, recovery_id, event),
        **body,
        "integrity_digest": digest,
        "occurred_at": datetime.now(timezone.utc),
    }
    try:
        await db.lease_renewal_rollover_recovery_audit.insert_one(document)
        return {"idempotent": False, "integrity_digest": digest}
    except DuplicateKeyError:
        existing = await db.lease_renewal_rollover_recovery_audit.find_one(
            {"_id": document["_id"]}
        )
        if existing and verify_recovery_audit_event(existing) and all(
            existing.get(key) == value for key, value in body.items()
        ):
            return {"idempotent": True, "integrity_digest": digest}
        raise HTTPException(status_code=409, detail="renewal_recovery_audit_conflict")

def _safe_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return ""


async def inspect_recovery_audit_chain(
    db, *, rollover_id: str, proposal_id: str, recovery_id: str
) -> Dict[str, Any]:
    rows = await db.lease_renewal_rollover_recovery_audit.find({
        "rollover_id": rollover_id,
        "proposal_id": proposal_id,
        "recovery_id": recovery_id,
    }).to_list(len(RECOVERY_AUDIT_EVENTS) + 1)
    rows = sorted(rows, key=lambda row: int(row.get("sequence") or 0))
    valid = len(rows) <= len(RECOVERY_AUDIT_EVENTS)
    previous_digest = ""
    safe_events = []
    for index, row in enumerate(rows):
        expected_event = (
            RECOVERY_AUDIT_EVENTS[index]
            if index < len(RECOVERY_AUDIT_EVENTS)
            else ""
        )
        row_valid = (
            row.get("event") == expected_event
            and row.get("sequence") == index + 1
            and str(row.get("rollover_id") or "") == rollover_id
            and str(row.get("proposal_id") or "") == proposal_id
            and str(row.get("recovery_id") or "") == recovery_id
            and verify_recovery_audit_event(row)
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
    complete = valid and len(rows) == len(RECOVERY_AUDIT_EVENTS)
    return {
        "status": "complete" if complete else "partial" if valid else "invalid",
        "valid": valid,
        "complete": complete,
        "recorded_events": len(rows),
        "expected_events": len(RECOVERY_AUDIT_EVENTS),
        "last_event": safe_events[-1]["event"] if safe_events else None,
        "events": safe_events,
    }

