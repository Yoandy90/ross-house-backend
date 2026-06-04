"""
Ross House AI Brain — El Cerebro de IA de Ross House Rentals LLC
================================================================
Automated AI assistant for property management, tenant support, and market inquiries.
Uses emergentintegrations with GPT-4o for intelligent, bilingual (EN/ES) responses.

Collections:
  - ai_brain_config: Global and per-conversation AI settings
  - ai_brain_logs: Activity log of all AI actions
"""

import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# ROSS HOUSE AI SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

ROSS_HOUSE_SYSTEM_PROMPT = """Eres el Asistente Virtual Oficial de Ross House Rentals LLC, una empresa de administración de propiedades y bienes raíces ubicada en Amarillo, Texas.

🏠 SOBRE ROSS HOUSE RENTALS LLC:
- Empresa dedicada a la administración de propiedades residenciales, alquiler, y venta de bienes raíces
- Ubicada en Amarillo, Texas, USA
- Propietario: Yoandy Ross
- Sitio web: rosshouserentals.com
- Contacto: info@rosshouserentals.com | (806) 591-4974
- Horario de atención: Lunes a Viernes 9AM - 6PM, Sábados 10AM - 2PM (CST)

🎯 TU ROL:
1. Atender consultas de inquilinos sobre sus propiedades, pagos de renta, mantenimiento y contratos
2. Responder preguntas de personas interesadas en alquilar o comprar propiedades
3. Ayudar con solicitudes de mantenimiento y explicar el proceso
4. Proporcionar información del mercado inmobiliario de la zona
5. Coordinar citas y visitas a propiedades
6. Manejar quejas con empatía y profesionalismo

📋 REGLAS:
- SIEMPRE responde en el MISMO IDIOMA que el usuario usa (español si escribe en español, inglés si escribe en inglés)
- Sé amable, profesional y empático
- Si no tienes información específica, indica que un agente humano se comunicará pronto
- NUNCA compartas información financiera confidencial de otros inquilinos
- Para emergencias de mantenimiento (fugas de agua, problemas eléctricos, incendios), indica que llamen al (806) 591-4974 inmediatamente
- Para pagos, dirige al portal de pagos en la app o sitio web
- Mantén respuestas concisas pero útiles (máximo 3-4 párrafos)
- Usa emojis moderadamente para ser amigable

🏡 SERVICIOS:
- Alquiler de propiedades residenciales
- Administración de propiedades para propietarios
- Mantenimiento y reparaciones
- Inversiones inmobiliarias
- Consultoría del mercado inmobiliario

💰 PAGOS:
- Renta se paga el 1ro de cada mes
- Período de gracia: hasta el 5 del mes
- Cargo por pago tardío: $50 después del día 5
- Métodos de pago: Stripe (tarjeta/ACH) a través de la app o sitio web
- Para problemas de pago, siempre ofrece opciones y muestra comprensión

🔧 MANTENIMIENTO:
- Emergencias: Llamar al (806) 591-4974
- No emergencias: Enviar solicitud por la app o chat
- Tiempo de respuesta normal: 24-48 horas hábiles
- Emergencias se atienden en menos de 4 horas

{context}
"""


class RossHouseAIBrain:
    """AI Brain service for Ross House Rentals."""

    def __init__(self, db):
        self.db = db
        self.llm_key = os.getenv('EMERGENT_LLM_KEY', '')
        self._initialized = False

        if not self.llm_key:
            logger.warning("⚠️ EMERGENT_LLM_KEY not found - AI Brain will be disabled")
        else:
            self._initialized = True
            logger.info("🧠 Ross House AI Brain initialized with Emergent LLM Key")

    @property
    def is_available(self) -> bool:
        return self._initialized

    # ══════════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ══════════════════════════════════════════════════════════════════

    async def get_global_config(self) -> Dict[str, Any]:
        """Get global AI Brain configuration."""
        config = await self.db.ai_brain_config.find_one({"_id": "global"})
        if not config:
            config = {
                "_id": "global",
                "ai_enabled": True,
                "auto_reply_chat": True,
                "auto_reply_delay_seconds": 3,
                "model": "gpt-4o",
                "provider": "openai",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            await self.db.ai_brain_config.insert_one(config)
        return config

    async def is_ai_globally_enabled(self) -> bool:
        config = await self.get_global_config()
        return config.get("ai_enabled", True)

    async def toggle_global_ai(self, enabled: bool) -> Dict:
        await self.db.ai_brain_config.update_one(
            {"_id": "global"},
            {"$set": {"ai_enabled": enabled, "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        await self._log_action("global_toggle", {"enabled": enabled})
        return {"success": True, "ai_enabled": enabled}

    async def is_ai_enabled_for_conversation(self, conversation_id: str) -> bool:
        """Check if AI is enabled for a specific conversation."""
        if not await self.is_ai_globally_enabled():
            return False
        conv_config = await self.db.ai_brain_config.find_one({"_id": f"conv_{conversation_id}"})
        if conv_config:
            return conv_config.get("ai_enabled", True)
        return True  # Default: enabled

    async def toggle_conversation_ai(self, conversation_id: str, enabled: bool) -> Dict:
        await self.db.ai_brain_config.update_one(
            {"_id": f"conv_{conversation_id}"},
            {"$set": {"ai_enabled": enabled, "conversation_id": conversation_id, "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        await self._log_action("conversation_toggle", {"conversation_id": conversation_id, "enabled": enabled})
        return {"success": True, "conversation_id": conversation_id, "ai_enabled": enabled}

    # ══════════════════════════════════════════════════════════════════
    # AI RESPONSE GENERATION
    # ══════════════════════════════════════════════════════════════════

    async def generate_chat_response(self, conversation_id: str, user_message: str, sender_name: str = "Usuario") -> Optional[str]:
        """Generate an AI response for a chat message."""
        if not self.is_available:
            logger.warning("AI Brain not available - missing LLM key")
            return None

        if not await self.is_ai_enabled_for_conversation(conversation_id):
            logger.info(f"AI disabled for conversation {conversation_id}")
            return None

        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage

            # Build context from conversation history and property data
            context = await self._build_context(conversation_id, sender_name)

            session_id = f"rosshouse_chat_{conversation_id}_{uuid.uuid4().hex[:8]}"
            system_prompt = ROSS_HOUSE_SYSTEM_PROMPT.format(context=context)

            chat = LlmChat(
                api_key=self.llm_key,
                session_id=session_id,
                system_message=system_prompt,
            ).with_model("openai", "gpt-4o")

            # Get recent messages for context
            recent_messages = await self._get_recent_messages(conversation_id, limit=10)

            # Build the conversation context as a single prompt
            conversation_text = ""
            for msg in recent_messages:
                role = "Inquilino" if msg["sender_type"] in ("tenant", "guest") else "Admin"
                if msg.get("is_ai"):
                    role = "Asistente IA"
                conversation_text += f"{role}: {msg['content']}\n"

            conversation_text += f"Inquilino: {user_message}\n"

            prompt = f"""Historial de la conversación:
{conversation_text}

Responde al último mensaje del inquilino de manera útil y profesional. Si la pregunta requiere información específica que no tienes, indica que un agente humano responderá pronto."""

            user_msg = UserMessage(text=prompt)
            response = await chat.send_message(user_msg)
            ai_text = response.content if hasattr(response, 'content') else str(response)

            # Sanitize response
            ai_text = self._sanitize_response(ai_text)

            # Log the action
            await self._log_action("chat_response", {
                "conversation_id": conversation_id,
                "user_message": user_message[:200],
                "ai_response": ai_text[:200],
                "sender_name": sender_name,
            })

            return ai_text

        except Exception as e:
            logger.error(f"AI Brain error generating response: {e}")
            await self._log_action("error", {"conversation_id": conversation_id, "error": str(e)[:300]})
            return None

    async def generate_email_response(self, subject: str, body: str, sender_email: str) -> Optional[str]:
        """Generate an AI response for an email inquiry."""
        if not self.is_available:
            return None

        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage

            session_id = f"rosshouse_email_{uuid.uuid4().hex[:8]}"
            system_prompt = ROSS_HOUSE_SYSTEM_PROMPT.format(context="") + """
            
INSTRUCCIONES ADICIONALES PARA EMAILS:
- Formato tu respuesta como un email profesional
- Incluye un saludo apropiado y despedida
- Firma como "Equipo Ross House Rentals LLC"
- Mantén el tono profesional pero cálido
"""

            chat = LlmChat(
                api_key=self.llm_key,
                session_id=session_id,
                system_message=system_prompt,
            ).with_model("openai", "gpt-4o")

            prompt = f"""Email recibido:
De: {sender_email}
Asunto: {subject}
Mensaje: {body}

Genera una respuesta profesional a este email."""

            user_msg = UserMessage(text=prompt)
            response = await chat.send_message(user_msg)
            ai_text = response.content if hasattr(response, 'content') else str(response)

            await self._log_action("email_response", {
                "sender_email": sender_email,
                "subject": subject,
                "ai_response": ai_text[:200],
            })

            return ai_text

        except Exception as e:
            logger.error(f"AI Brain email error: {e}")
            return None

    # ══════════════════════════════════════════════════════════════════
    # CONTEXT BUILDING
    # ══════════════════════════════════════════════════════════════════

    async def _build_context(self, conversation_id: str, sender_name: str) -> str:
        """Build context about the tenant and their property."""
        context_parts = []

        try:
            # Get conversation info
            from bson import ObjectId
            conv = await self.db.chat_conversations.find_one({"_id": ObjectId(conversation_id)})
            if conv:
                context_parts.append(f"📌 CONVERSACIÓN CON: {conv.get('tenant_name', sender_name)}")
                tenant_id = conv.get("tenant_id")

                if tenant_id:
                    # Get tenant info
                    tenant = await self.db.app_users.find_one({"_id": ObjectId(tenant_id)})
                    if tenant:
                        context_parts.append(f"👤 Email: {tenant.get('email', 'N/A')}")
                        context_parts.append(f"📱 Teléfono: {tenant.get('phone', 'N/A')}")

                    # Get tenant's active contract
                    contract = await self.db.rental_contracts.find_one({
                        "tenant_id": tenant_id,
                        "status": {"$in": ["active", "activo"]}
                    })
                    if contract:
                        context_parts.append(f"📄 Contrato activo: {contract.get('property_address', 'N/A')}")
                        context_parts.append(f"💰 Renta mensual: ${contract.get('rent_amount', 'N/A')}")
                        context_parts.append(f"📅 Vencimiento: {contract.get('end_date', 'N/A')}")

                    # Get maintenance requests
                    maintenance_cursor = self.db.maintenance_requests.find({
                        "tenant_id": tenant_id,
                        "status": {"$in": ["pending", "in_progress", "pendiente", "en_progreso"]}
                    }).sort("created_at", -1).limit(3)
                    maintenance = await maintenance_cursor.to_list(length=3)
                    if maintenance:
                        context_parts.append("🔧 SOLICITUDES DE MANTENIMIENTO ACTIVAS:")
                        for m in maintenance:
                            context_parts.append(f"  - {m.get('description', 'N/A')[:60]} ({m.get('status', 'N/A')})")

            # Get available properties
            active_props = await self.db.properties.find({"status": "active"}).to_list(length=10)
            if active_props:
                context_parts.append("\n🏠 PROPIEDADES DISPONIBLES:")
                for p in active_props:
                    context_parts.append(f"  - {p.get('address', 'N/A')}: ${p.get('rent_amount', 'N/A')}/mes, {p.get('bedrooms', '?')} hab, {p.get('bathrooms', '?')} baños")

        except Exception as e:
            logger.warning(f"Error building context: {e}")

        return "\n".join(context_parts) if context_parts else ""

    async def _get_recent_messages(self, conversation_id: str, limit: int = 10) -> List[Dict]:
        """Get recent messages from a conversation."""
        try:
            cursor = self.db.chat_messages.find(
                {"conversation_id": conversation_id}
            ).sort("created_at", -1).limit(limit)
            messages = await cursor.to_list(length=limit)
            messages.reverse()  # Chronological order
            return messages
        except Exception as e:
            logger.warning(f"Error fetching messages: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════
    # UTILITIES
    # ══════════════════════════════════════════════════════════════════

    def _sanitize_response(self, text: str) -> str:
        """Sanitize AI response to remove any problematic content."""
        if not text:
            return ""
        # Remove potential system prompt leakage
        for phrase in ["Como asistente virtual", "As an AI", "I'm an AI", "Soy una IA"]:
            text = text.replace(phrase, "")
        # Limit length
        if len(text) > 1500:
            text = text[:1500] + "..."
        return text.strip()

    async def _log_action(self, action_type: str, details: Dict):
        """Log an AI Brain action."""
        try:
            log_entry = {
                "action_type": action_type,
                "details": details,
                "created_at": datetime.now(timezone.utc),
            }
            await self.db.ai_brain_logs.insert_one(log_entry)
        except Exception as e:
            logger.warning(f"Error logging AI action: {e}")

    async def get_activity_log(self, limit: int = 50, action_type: Optional[str] = None) -> List[Dict]:
        """Get AI Brain activity log."""
        query = {}
        if action_type:
            query["action_type"] = action_type
        cursor = self.db.ai_brain_logs.find(query).sort("created_at", -1).limit(limit)
        logs = await cursor.to_list(length=limit)
        # Serialize ObjectIds
        for log in logs:
            log["_id"] = str(log["_id"])
            if "created_at" in log:
                log["created_at"] = log["created_at"].isoformat()
        return logs

    async def get_stats(self) -> Dict:
        """Get AI Brain statistics."""
        try:
            total_responses = await self.db.ai_brain_logs.count_documents({"action_type": "chat_response"})
            total_emails = await self.db.ai_brain_logs.count_documents({"action_type": "email_response"})
            total_errors = await self.db.ai_brain_logs.count_documents({"action_type": "error"})
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_responses = await self.db.ai_brain_logs.count_documents({
                "action_type": "chat_response",
                "created_at": {"$gte": today_start}
            })

            config = await self.get_global_config()

            return {
                "ai_enabled": config.get("ai_enabled", False),
                "model": config.get("model", "gpt-4o"),
                "total_chat_responses": total_responses,
                "total_email_responses": total_emails,
                "total_errors": total_errors,
                "today_responses": today_responses,
                "is_available": self.is_available,
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"error": str(e)}
