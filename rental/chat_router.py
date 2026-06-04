"""
Chat Router — Real-Time Direct Messaging
==========================================
Tenant ↔ Admin chat system with support for text, images, and files.

Collections:
  - chat_conversations: { _id, tenant_id, tenant_name, last_message, last_message_at, unread_tenant, unread_admin, created_at }
  - chat_messages: { _id, conversation_id, sender_type (tenant|admin), sender_name, message_type (text|image|file), content, file_name, created_at, read }
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from rental.shared import get_db, serialize, auth_marketplace, auth_admin, send_rental_push_to_admins, send_rental_push_to_user

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)

# AI Brain reference (set from server.py)
_ai_brain = None

def set_ai_brain(brain):
    global _ai_brain
    _ai_brain = brain
    logger.info("🧠 Chat router connected to AI Brain")


# ── Pydantic Models ──

class SendMessageBody(BaseModel):
    content: str
    message_type: str = "text"  # text | image | file
    file_name: Optional[str] = None


class AdminSendMessageBody(BaseModel):
    conversation_id: str
    content: str
    message_type: str = "text"
    file_name: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# TENANT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/conversation")
async def get_or_create_conversation(request: Request):
    """Get the tenant's conversation with admin, or create one."""
    db = get_db()
    user = await auth_marketplace(request)
    user_id = user["_id"]
    user_name = user.get("name", user.get("email", "Inquilino"))

    conv = await db.chat_conversations.find_one({"tenant_id": user_id})

    if not conv:
        conv = {
            "tenant_id": user_id,
            "tenant_name": user_name,
            "tenant_email": user.get("email", ""),
            "tenant_phone": user.get("phone", ""),
            "last_message": "",
            "last_message_at": datetime.now(timezone.utc),
            "unread_tenant": 0,
            "unread_admin": 0,
            "created_at": datetime.now(timezone.utc),
        }
        result = await db.chat_conversations.insert_one(conv)
        conv["_id"] = result.inserted_id

    return {"success": True, "conversation": serialize(conv)}


@router.get("/messages")
async def get_messages(request: Request, limit: int = 50, before: Optional[str] = None):
    """Get messages for the tenant's conversation."""
    db = get_db()
    user = await auth_marketplace(request)
    user_id = user["_id"]

    conv = await db.chat_conversations.find_one({"tenant_id": user_id})
    if not conv:
        return {"success": True, "messages": []}

    conv_id = str(conv["_id"])

    query = {"conversation_id": conv_id}
    if before:
        try:
            query["_id"] = {"$lt": ObjectId(before)}
        except:
            pass

    messages = await db.chat_messages.find(query).sort("_id", -1).limit(limit).to_list(limit)
    messages.reverse()

    # Mark messages from admin as read
    await db.chat_messages.update_many(
        {"conversation_id": conv_id, "sender_type": "admin", "read": False},
        {"$set": {"read": True}}
    )
    await db.chat_conversations.update_one(
        {"_id": conv["_id"]},
        {"$set": {"unread_tenant": 0}}
    )

    return {"success": True, "messages": [serialize(m) for m in messages]}


@router.post("/send")
async def send_message(request: Request, body: SendMessageBody):
    """Tenant sends a message to admin."""
    db = get_db()
    user = await auth_marketplace(request)
    user_id = user["_id"]
    user_name = user.get("name", user.get("email", "Inquilino"))

    # Get or create conversation
    conv = await db.chat_conversations.find_one({"tenant_id": user_id})
    if not conv:
        conv = {
            "tenant_id": user_id,
            "tenant_name": user_name,
            "tenant_email": user.get("email", ""),
            "tenant_phone": user.get("phone", ""),
            "last_message": "",
            "last_message_at": datetime.now(timezone.utc),
            "unread_tenant": 0,
            "unread_admin": 0,
            "created_at": datetime.now(timezone.utc),
        }
        result = await db.chat_conversations.insert_one(conv)
        conv["_id"] = result.inserted_id

    conv_id = str(conv["_id"])

    # Create message
    msg = {
        "conversation_id": conv_id,
        "sender_type": "tenant",
        "sender_id": user_id,
        "sender_name": user_name,
        "message_type": body.message_type,
        "content": body.content,
        "file_name": body.file_name,
        "read": False,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.chat_messages.insert_one(msg)
    msg["_id"] = result.inserted_id

    # Update conversation
    preview = body.content[:80] if body.message_type == "text" else f"📎 {body.file_name or 'Archivo'}"
    await db.chat_conversations.update_one(
        {"_id": conv["_id"]},
        {
            "$set": {
                "last_message": preview,
                "last_message_at": datetime.now(timezone.utc),
                "tenant_name": user_name,
            },
            "$inc": {"unread_admin": 1},
        }
    )

    # Push notification to admins
    try:
        await send_rental_push_to_admins(
            title=f"💬 {user_name}",
            body=preview,
            data={"type": "chat_message", "conversation_id": conv_id}
        )
    except Exception as e:
        logger.warning(f"Push to admin failed: {e}")

    # 🧠 AI Brain Auto-Reply (runs in background)
    if _ai_brain and body.message_type == "text":
        asyncio.create_task(_ai_auto_reply(conv_id, body.content, user_name))

    return {"success": True, "message": serialize(msg)}


@router.get("/unread-count")
async def get_unread_count(request: Request):
    """Get the unread message count for the tenant."""
    db = get_db()
    user = await auth_marketplace(request)
    user_id = user["_id"]

    conv = await db.chat_conversations.find_one({"tenant_id": user_id})
    count = conv.get("unread_tenant", 0) if conv else 0

    return {"success": True, "unread_count": count}


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/conversations")
async def admin_get_conversations(request: Request, search: Optional[str] = None):
    """Admin: List all conversations, sorted by most recent."""
    db = get_db()
    await auth_admin(request)

    query = {}
    if search:
        query["$or"] = [
            {"tenant_name": {"$regex": search, "$options": "i"}},
            {"tenant_email": {"$regex": search, "$options": "i"}},
        ]

    convs = await db.chat_conversations.find(query).sort("last_message_at", -1).to_list(100)

    return {"success": True, "conversations": [serialize(c) for c in convs]}


@router.get("/admin/messages/{conversation_id}")
async def admin_get_messages(request: Request, conversation_id: str, limit: int = 50, before: Optional[str] = None):
    """Admin: Get messages for a specific conversation."""
    db = get_db()
    await auth_admin(request)

    query = {"conversation_id": conversation_id}
    if before:
        try:
            query["_id"] = {"$lt": ObjectId(before)}
        except:
            pass

    messages = await db.chat_messages.find(query).sort("_id", -1).limit(limit).to_list(limit)
    messages.reverse()

    # Mark tenant messages as read
    await db.chat_messages.update_many(
        {"conversation_id": conversation_id, "sender_type": "tenant", "read": False},
        {"$set": {"read": True}}
    )
    await db.chat_conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {"$set": {"unread_admin": 0}}
    )

    return {"success": True, "messages": [serialize(m) for m in messages]}


@router.post("/admin/send")
async def admin_send_message(request: Request, body: AdminSendMessageBody):
    """Admin sends a message to a tenant."""
    db = get_db()
    admin = await auth_admin(request)
    admin_name = admin.get("name", admin.get("email", "Ross House Admin"))

    conv = await db.chat_conversations.find_one({"_id": ObjectId(body.conversation_id)})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")

    conv_id = str(conv["_id"])

    msg = {
        "conversation_id": conv_id,
        "sender_type": "admin",
        "sender_id": str(admin["_id"]),
        "sender_name": admin_name,
        "message_type": body.message_type,
        "content": body.content,
        "file_name": body.file_name,
        "read": False,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.chat_messages.insert_one(msg)
    msg["_id"] = result.inserted_id

    preview = body.content[:80] if body.message_type == "text" else f"📎 {body.file_name or 'Archivo'}"
    await db.chat_conversations.update_one(
        {"_id": conv["_id"]},
        {
            "$set": {
                "last_message": preview,
                "last_message_at": datetime.now(timezone.utc),
            },
            "$inc": {"unread_tenant": 1},
        }
    )

    # Push notification to tenant
    tenant_id = conv.get("tenant_id", "")
    try:
        await send_rental_push_to_user(
            user_id=tenant_id,
            title="💬 Ross House Rentals",
            body=preview,
            data={"type": "chat_message", "conversation_id": conv_id}
        )
    except Exception as e:
        logger.warning(f"Push to tenant failed: {e}")

    return {"success": True, "message": serialize(msg)}


@router.get("/admin/unread-total")
async def admin_unread_total(request: Request):
    """Admin: Total unread messages across all conversations."""
    db = get_db()
    await auth_admin(request)

    pipeline = [
        {"$group": {"_id": None, "total": {"$sum": "$unread_admin"}}}
    ]
    result = await db.chat_conversations.aggregate(pipeline).to_list(1)
    total = result[0]["total"] if result else 0

    return {"success": True, "total_unread": total}



# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC CHAT ENDPOINTS (No auth required — for website visitors)
# ═══════════════════════════════════════════════════════════════════════════════

class PublicChatStartBody(BaseModel):
    name: str
    phone: str = ""
    email: str = ""


class PublicChatSendBody(BaseModel):
    conversation_id: str
    session_token: str
    content: str


@router.post("/public/start")
async def public_start_conversation(body: PublicChatStartBody):
    """Start a public chat conversation (no auth needed). Returns a session token."""
    import hashlib, secrets
    db = get_db()

    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Nombre es requerido")

    session_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(session_token.encode()).hexdigest()

    conv = {
        "tenant_id": f"guest_{token_hash[:16]}",
        "tenant_name": body.name.strip(),
        "tenant_email": body.email.strip(),
        "tenant_phone": body.phone.strip(),
        "is_guest": True,
        "session_token_hash": token_hash,
        "last_message": "",
        "last_message_at": datetime.now(timezone.utc),
        "unread_tenant": 0,
        "unread_admin": 0,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.chat_conversations.insert_one(conv)
    conv["_id"] = result.inserted_id

    logger.info(f"Public chat started by {body.name} ({body.phone or body.email})")

    return {
        "success": True,
        "conversation_id": str(conv["_id"]),
        "session_token": session_token,
    }


@router.post("/public/send")
async def public_send_message(body: PublicChatSendBody):
    """Guest sends a message (verified by session token)."""
    import hashlib
    db = get_db()

    token_hash = hashlib.sha256(body.session_token.encode()).hexdigest()
    conv = await db.chat_conversations.find_one({
        "_id": ObjectId(body.conversation_id),
        "session_token_hash": token_hash,
    })
    if not conv:
        raise HTTPException(status_code=403, detail="Sesión no válida")

    conv_id = str(conv["_id"])
    sender_name = conv.get("tenant_name", "Visitante")

    msg = {
        "conversation_id": conv_id,
        "sender_type": "tenant",
        "sender_id": conv.get("tenant_id", ""),
        "sender_name": sender_name,
        "message_type": "text",
        "content": body.content.strip(),
        "read": False,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.chat_messages.insert_one(msg)
    msg["_id"] = result.inserted_id

    preview = body.content[:80]
    await db.chat_conversations.update_one(
        {"_id": conv["_id"]},
        {
            "$set": {
                "last_message": preview,
                "last_message_at": datetime.now(timezone.utc),
            },
            "$inc": {"unread_admin": 1},
        }
    )

    try:
        await send_rental_push_to_admins(
            title=f"💬 {sender_name} (web)",
            body=preview,
            data={"type": "chat_message", "conversation_id": conv_id}
        )
    except Exception as e:
        logger.warning(f"Push to admin failed: {e}")

    # 🧠 AI Brain Auto-Reply for public chat
    if _ai_brain:
        asyncio.create_task(_ai_auto_reply(conv_id, body.content, sender_name))

    return {"success": True, "message": serialize(msg)}


@router.get("/public/messages")
async def public_get_messages(conversation_id: str, session_token: str, limit: int = 50):
    """Guest retrieves messages for their conversation."""
    import hashlib
    db = get_db()

    token_hash = hashlib.sha256(session_token.encode()).hexdigest()
    conv = await db.chat_conversations.find_one({
        "_id": ObjectId(conversation_id),
        "session_token_hash": token_hash,
    })
    if not conv:
        raise HTTPException(status_code=403, detail="Sesión no válida")

    conv_id = str(conv["_id"])
    messages = await db.chat_messages.find({"conversation_id": conv_id}).sort("_id", -1).limit(limit).to_list(limit)
    messages.reverse()

    # Mark admin messages as read
    await db.chat_messages.update_many(
        {"conversation_id": conv_id, "sender_type": "admin", "read": False},
        {"$set": {"read": True}}
    )
    await db.chat_conversations.update_one(
        {"_id": conv["_id"]},
        {"$set": {"unread_tenant": 0}}
    )

    return {"success": True, "messages": [serialize(m) for m in messages]}



# ═══════════════════════════════════════════════════════════════════════════════
# AI AUTO-REPLY (Background Task)
# ═══════════════════════════════════════════════════════════════════════════════

async def _ai_auto_reply(conversation_id: str, user_message: str, sender_name: str):
    """Generate and send an AI auto-reply in the background."""
    try:
        if not _ai_brain:
            return

        # Small delay to feel more natural
        await asyncio.sleep(2)

        # Generate AI response
        ai_text = await _ai_brain.generate_chat_response(conversation_id, user_message, sender_name)
        if not ai_text:
            return

        db = get_db()

        # Save AI response as a message
        ai_msg = {
            "conversation_id": conversation_id,
            "sender_type": "admin",
            "sender_id": "ai_brain",
            "sender_name": "Ross House AI",
            "message_type": "text",
            "content": ai_text,
            "is_ai": True,
            "read": False,
            "created_at": datetime.now(timezone.utc),
        }
        await db.chat_messages.insert_one(ai_msg)

        # Update conversation
        preview = f"🤖 {ai_text[:70]}"
        await db.chat_conversations.update_one(
            {"_id": ObjectId(conversation_id)},
            {
                "$set": {
                    "last_message": preview,
                    "last_message_at": datetime.now(timezone.utc),
                },
                "$inc": {"unread_tenant": 1},
            }
        )

        # Push notification to tenant
        try:
            conv = await db.chat_conversations.find_one({"_id": ObjectId(conversation_id)})
            if conv and conv.get("tenant_id"):
                await send_rental_push_to_user(
                    user_id=conv["tenant_id"],
                    title="🏠 Ross House Rentals",
                    body=ai_text[:100],
                    data={"type": "chat_message", "conversation_id": conversation_id}
                )
        except Exception as e:
            logger.warning(f"Push to tenant failed: {e}")

        logger.info(f"🧠 AI auto-replied to conversation {conversation_id}")

    except Exception as e:
        logger.error(f"❌ AI auto-reply error: {e}")
