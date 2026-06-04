"""
Ross House AI Brain Router — API Endpoints
=============================================
Admin endpoints for controlling the AI Brain, viewing logs, and managing settings.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from rental.shared import get_db, auth_admin, serialize

router = APIRouter(prefix="/admin/ai-brain", tags=["AI Brain"])
logger = logging.getLogger(__name__)

# Global reference to AI Brain instance (set from server.py)
_ai_brain = None


def set_ai_brain(brain):
    global _ai_brain
    _ai_brain = brain
    logger.info("🧠 AI Brain router connected")


def get_brain():
    if not _ai_brain:
        raise HTTPException(status_code=503, detail="AI Brain no está disponible")
    return _ai_brain


# ── Pydantic Models ──

class ToggleBody(BaseModel):
    enabled: bool


class ConversationToggleBody(BaseModel):
    conversation_id: str
    enabled: bool


class EmailDraftBody(BaseModel):
    subject: str
    body: str
    sender_email: str


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_ai_status(request: Request):
    """Get AI Brain status, config, and stats."""
    await auth_admin(request)
    brain = get_brain()

    config = await brain.get_global_config()
    stats = await brain.get_stats()

    return {
        "success": True,
        "config": {
            "ai_enabled": config.get("ai_enabled", False),
            "auto_reply_chat": config.get("auto_reply_chat", True),
            "model": config.get("model", "gpt-4o"),
            "provider": config.get("provider", "openai"),
        },
        "stats": stats,
    }


@router.post("/toggle")
async def toggle_ai_global(request: Request, body: ToggleBody):
    """Toggle AI Brain on/off globally."""
    await auth_admin(request)
    brain = get_brain()
    result = await brain.toggle_global_ai(body.enabled)
    return result


@router.post("/toggle-conversation")
async def toggle_conversation_ai(request: Request, body: ConversationToggleBody):
    """Toggle AI for a specific conversation."""
    await auth_admin(request)
    brain = get_brain()
    result = await brain.toggle_conversation_ai(body.conversation_id, body.enabled)
    return result


@router.get("/conversation-status/{conversation_id}")
async def get_conversation_ai_status(request: Request, conversation_id: str):
    """Check if AI is enabled for a specific conversation."""
    await auth_admin(request)
    brain = get_brain()
    enabled = await brain.is_ai_enabled_for_conversation(conversation_id)
    return {"success": True, "conversation_id": conversation_id, "ai_enabled": enabled}


@router.get("/logs")
async def get_ai_logs(request: Request, limit: int = 50, action_type: Optional[str] = None):
    """Get AI Brain activity logs."""
    await auth_admin(request)
    brain = get_brain()
    logs = await brain.get_activity_log(limit=limit, action_type=action_type)
    return {"success": True, "logs": logs, "count": len(logs)}


@router.post("/draft-email")
async def draft_email_response(request: Request, body: EmailDraftBody):
    """Generate an AI draft response for an email."""
    await auth_admin(request)
    brain = get_brain()
    response = await brain.generate_email_response(body.subject, body.body, body.sender_email)
    if response:
        return {"success": True, "draft": response}
    raise HTTPException(status_code=500, detail="Error generando respuesta AI")


class SetKeyBody(BaseModel):
    key: str


@router.post("/set-key")
async def set_llm_key(request: Request, body: SetKeyBody):
    """Store the EMERGENT_LLM_KEY in the database for production use."""
    await auth_admin(request)
    brain = get_brain()
    db = brain.db
    await db.api_config.update_one(
        {"_id": "main"},
        {"$set": {"EMERGENT_LLM_KEY": body.key}},
        upsert=True
    )
    # Immediately update the brain instance
    brain.llm_key = body.key
    brain._initialized = True
    return {"success": True, "message": "LLM key saved and AI Brain activated"}



@router.post("/test")
async def test_ai_brain(request: Request):
    """Quick test to verify AI Brain is working."""
    await auth_admin(request)
    brain = get_brain()

    # Try to load key from DB if not in env
    await brain._ensure_key()

    if not brain.is_available:
        return {"success": False, "message": "AI Brain no disponible - falta clave LLM. Use el endpoint /set-key para configurarla."}

    try:
        result = await brain._call_openai(
            "You are a test assistant. Respond briefly.",
            "Say 'Ross House AI Brain is working!' in both English and Spanish."
        )
        if result:
            return {"success": True, "response": result}
        return {"success": False, "error": "No response from OpenAI"}
    except Exception as e:
        return {"success": False, "error": str(e)}
