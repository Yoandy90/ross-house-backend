"""
AI Actions Service — Phase 2 AI Brain (Write/Execution Permissions)
====================================================================
Lets the AI Brain propose *actions* (writes, sends, updates) that an
admin must explicitly approve before they execute. Every approved
action is auditable.

# Collection: ai_pending_actions
  _id, conversation_id, action_type, payload, summary, status,
  proposed_at, decided_at, decided_by, executed_at, result, error

# Action types supported (whitelist):
  - send_email_to_lead
  - update_lead_status
  - add_lead_note
  - update_lead_priority
  - notify_property_match
  - rescore_lead
  - send_briefing_now

# Proposal protocol:
The AI Brain emits ACTION blocks in its assistant responses using this format:

  <ACTION>
  {
    "type": "send_email_to_lead",
    "summary": "Enviar email de bienvenida a María Pérez",
    "payload": {
      "lead_id": "abc123",
      "subject": "Bienvenida a Ross House",
      "body": "Hola María..."
    }
  }
  </ACTION>

The chat handler extracts these blocks, removes them from the streamed
text shown to the user, persists them as pending, and the UI renders
them as approval cards.
"""
from __future__ import annotations

import re
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

ACTION_BLOCK_RE = re.compile(r"<ACTION>\s*(\{.*?\})\s*</ACTION>", re.DOTALL)

ALLOWED_ACTION_TYPES = {
    "send_email_to_lead",
    "update_lead_status",
    "add_lead_note",
    "update_lead_priority",
    "notify_property_match",
    "rescore_lead",
    "send_briefing_now",
}

VALID_LEAD_STATUSES = {"new", "contacted", "qualified", "applied", "rented", "rejected"}
VALID_PRIORITIES = {"low", "medium", "high"}


def extract_actions(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Strip <ACTION> blocks from `text` and return (cleaned_text, actions).

    Actions that fail to parse or use unknown types are silently dropped.
    """
    actions: List[Dict[str, Any]] = []

    def _replace(m: re.Match) -> str:
        raw = m.group(1).strip()
        try:
            parsed = json.loads(raw)
        except Exception as e:
            logger.warning(f"[ai_actions] could not parse action JSON: {e} | raw={raw[:120]}")
            return ""
        t = parsed.get("type")
        if t not in ALLOWED_ACTION_TYPES:
            logger.warning(f"[ai_actions] unknown action type rejected: {t}")
            return ""
        summary = (parsed.get("summary") or t).strip()[:240]
        payload = parsed.get("payload") or {}
        if not isinstance(payload, dict):
            return ""
        actions.append({"type": t, "summary": summary, "payload": payload})
        return ""

    cleaned = ACTION_BLOCK_RE.sub(_replace, text)
    # Collapse extra blank lines left by removal
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, actions


async def store_pending_actions(
    db, conversation_id: str, actions: List[Dict[str, Any]], proposed_by: str = "ai",
) -> List[Dict[str, Any]]:
    """Persist each proposed action as `pending` in ai_pending_actions."""
    if not actions:
        return []
    docs = []
    now = datetime.now(timezone.utc).isoformat()
    for a in actions:
        doc = {
            "_id": str(uuid4()),
            "conversation_id": conversation_id,
            "action_type": a["type"],
            "summary": a["summary"],
            "payload": a["payload"],
            "status": "pending",
            "proposed_at": now,
            "proposed_by": proposed_by,
            "decided_at": None,
            "decided_by": None,
            "executed_at": None,
            "result": None,
            "error": None,
        }
        docs.append(doc)
    await db.ai_pending_actions.insert_many(docs)
    return docs


async def list_pending_actions(db, conversation_id: Optional[str] = None,
                                status: Optional[str] = None,
                                limit: int = 100) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {}
    if conversation_id:
        query["conversation_id"] = conversation_id
    if status:
        query["status"] = status
    cursor = db.ai_pending_actions.find(query).sort("proposed_at", -1).limit(limit)
    return await cursor.to_list(None)


# ── Executors ────────────────────────────────────────────────────────────────

async def _exec_send_email_to_lead(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    from .tenant_leads_router import _send_email  # type: ignore
    lead_id = payload.get("lead_id")
    if not lead_id:
        raise ValueError("lead_id required")
    lead = await db.tenant_leads.find_one({"_id": lead_id})
    if not lead:
        raise ValueError("Lead not found")
    subject = payload.get("subject") or "Ross House Rentals"
    body = payload.get("body") or ""
    if not body.strip():
        raise ValueError("body required")
    ok = await _send_email(lead["email"], subject, body)
    # Track on the lead too
    await db.tenant_leads.update_one(
        {"_id": lead_id},
        {"$push": {"notifications_sent": {
            "type": "ai_action_email",
            "sent_at": datetime.utcnow(),
            "subject": subject,
            "email": ok,
        }}, "$set": {"updated_at": datetime.utcnow()}},
    )
    return {"sent": ok, "to": lead["email"], "subject": subject}


async def _exec_update_lead_status(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    lead_id = payload.get("lead_id")
    new_status = payload.get("status") or payload.get("new_status")
    if not lead_id or new_status not in VALID_LEAD_STATUSES:
        raise ValueError(f"lead_id and status (one of {sorted(VALID_LEAD_STATUSES)}) required")
    update = {"status": new_status, "updated_at": datetime.utcnow()}
    if new_status == "contacted":
        update["last_contacted_at"] = datetime.utcnow()
    res = await db.tenant_leads.update_one({"_id": lead_id}, {"$set": update})
    if res.matched_count == 0:
        raise ValueError("Lead not found")
    return {"lead_id": lead_id, "status": new_status}


async def _exec_add_lead_note(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    lead_id = payload.get("lead_id")
    note = (payload.get("note") or "").strip()
    if not lead_id or not note:
        raise ValueError("lead_id and note required")
    lead = await db.tenant_leads.find_one({"_id": lead_id})
    if not lead:
        raise ValueError("Lead not found")
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    existing = lead.get("admin_notes") or ""
    sep = "\n\n" if existing else ""
    new_notes = f"{existing}{sep}[AI · {stamp}] {note}"
    await db.tenant_leads.update_one(
        {"_id": lead_id},
        {"$set": {"admin_notes": new_notes, "updated_at": datetime.utcnow()}},
    )
    return {"lead_id": lead_id, "note_appended": True}


async def _exec_update_lead_priority(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    lead_id = payload.get("lead_id")
    priority = payload.get("priority")
    if not lead_id or priority not in VALID_PRIORITIES:
        raise ValueError(f"lead_id and priority (one of {sorted(VALID_PRIORITIES)}) required")
    res = await db.tenant_leads.update_one(
        {"_id": lead_id},
        {"$set": {"priority": priority, "updated_at": datetime.utcnow()}},
    )
    if res.matched_count == 0:
        raise ValueError("Lead not found")
    return {"lead_id": lead_id, "priority": priority}


async def _exec_notify_property_match(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Re-uses the existing /admin/tenant-leads/notify-property logic inline."""
    from .tenant_leads_router import _get_settings, _send_email, _send_sms, _match_lead_to_property
    property_id = payload.get("property_id")
    target_lead_ids = payload.get("lead_ids")
    custom_message = payload.get("message")
    via_email = payload.get("email", True)
    via_sms = payload.get("sms", True)

    prop = await db.properties.find_one({"_id": property_id})
    if not prop:
        raise ValueError("Property not found")
    settings = await _get_settings(db)

    if target_lead_ids:
        leads = [ld async for ld in db.tenant_leads.find(
            {"_id": {"$in": target_lead_ids}, "status": {"$nin": ["rented", "rejected"]}}
        )]
    else:
        all_leads = [ld async for ld in db.tenant_leads.find({"status": {"$nin": ["rented", "rejected"]}})]
        leads = [ld for ld in all_leads if _match_lead_to_property(ld, prop)]

    sent = 0
    for lead in leads:
        lang = lead.get("language_pref", "es")
        addr = prop.get("address") or prop.get("name") or "Propiedad"
        rent = prop.get("monthly_rent") or prop.get("rent") or 0
        beds = prop.get("bedrooms") or "-"
        if lang == "es":
            subject = "🏡 ¡Tenemos una casa disponible para ti!"
            body = custom_message or f"Hola {lead['name']},\n\n¡Disponible! {addr} - {beds} hab - ${rent:,.0f}/mes.\nLlámanos al (806) 934-2018.\n\n— Ross House Rentals"
            sms_body = f"Ross House: ¡Disponible! {addr} - {beds}hab - ${rent:,.0f}/mes. (806) 934-2018"
        else:
            subject = "🏡 A home matching your search is available!"
            body = custom_message or f"Hi {lead['name']},\n\nAvailable! {addr} - {beds} BR - ${rent:,.0f}/mo.\nCall us at (806) 934-2018.\n\n— Ross House Rentals"
            sms_body = f"Ross House: Available! {addr} - {beds}BR - ${rent:,.0f}/mo. (806) 934-2018"
        e_ok = s_ok = False
        if via_email and settings.get("email_enabled"):
            e_ok = await _send_email(lead["email"], subject, body)
        if via_sms and settings.get("sms_enabled"):
            s_ok = await _send_sms(lead["phone"], sms_body)
        await db.tenant_leads.update_one(
            {"_id": lead["_id"]},
            {"$push": {"notifications_sent": {
                "type": "ai_property_match",
                "property_id": property_id,
                "sent_at": datetime.utcnow(),
                "email": e_ok,
                "sms": s_ok,
            }}, "$addToSet": {"matched_properties": property_id},
             "$set": {"updated_at": datetime.utcnow()}},
        )
        if e_ok or s_ok:
            sent += 1
    return {"matched": len(leads), "sent": sent, "property_id": property_id}


async def _exec_rescore_lead(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    from .lead_scoring import score_and_persist
    lead_id = payload.get("lead_id")
    if not lead_id:
        raise ValueError("lead_id required")
    result = await score_and_persist(db, lead_id)
    if not result:
        raise ValueError("Lead not found")
    return {"lead_id": lead_id, "score": result["score"], "label": result["score_label"]}


async def _exec_send_briefing_now(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    from .ai_brain_router import _generate_and_send_briefing
    recipient = payload.get("recipient_email")
    result = await _generate_and_send_briefing(db, recipient=recipient)
    return {"ok": result.get("ok"), "recipient": result.get("recipient")}


EXECUTORS = {
    "send_email_to_lead": _exec_send_email_to_lead,
    "update_lead_status": _exec_update_lead_status,
    "add_lead_note": _exec_add_lead_note,
    "update_lead_priority": _exec_update_lead_priority,
    "notify_property_match": _exec_notify_property_match,
    "rescore_lead": _exec_rescore_lead,
    "send_briefing_now": _exec_send_briefing_now,
}


async def execute_action(db, action_id: str, approver: str) -> Dict[str, Any]:
    """Mark an action approved and run its executor. Returns the updated doc."""
    doc = await db.ai_pending_actions.find_one({"_id": action_id})
    if not doc:
        raise ValueError("Action not found")
    if doc.get("status") != "pending":
        raise ValueError(f"Action already {doc.get('status')}")
    t = doc["action_type"]
    fn = EXECUTORS.get(t)
    if not fn:
        raise ValueError(f"No executor for action type: {t}")

    now = datetime.now(timezone.utc).isoformat()
    update: Dict[str, Any] = {
        "status": "executing",
        "decided_at": now,
        "decided_by": approver,
    }
    await db.ai_pending_actions.update_one({"_id": action_id}, {"$set": update})

    try:
        result = await fn(db, doc.get("payload") or {})
        finish_update = {
            "status": "executed",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        await db.ai_pending_actions.update_one({"_id": action_id}, {"$set": finish_update})
        return {**doc, **update, **finish_update}
    except Exception as e:
        logger.exception(f"[ai_actions] execution failed: {e}")
        err_update = {
            "status": "failed",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)[:500],
        }
        await db.ai_pending_actions.update_one({"_id": action_id}, {"$set": err_update})
        return {**doc, **update, **err_update}


async def reject_action(db, action_id: str, decider: str, reason: str = "") -> Dict[str, Any]:
    doc = await db.ai_pending_actions.find_one({"_id": action_id})
    if not doc:
        raise ValueError("Action not found")
    if doc.get("status") != "pending":
        raise ValueError(f"Action already {doc.get('status')}")
    upd = {
        "status": "rejected",
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "decided_by": decider,
        "error": reason[:500] if reason else None,
    }
    await db.ai_pending_actions.update_one({"_id": action_id}, {"$set": upd})
    return {**doc, **upd}
