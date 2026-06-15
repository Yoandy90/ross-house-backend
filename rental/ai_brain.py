"""
Ross House AI Brain — El Cerebro de IA de Ross House Rentals LLC
================================================================
Automated AI assistant for property management, tenant support, and market inquiries.
Supports multiple LLM providers: OpenAI (direct), Gemini, or Emergent LLM Key.

Collections:
  - ai_brain_config: Global and per-conversation AI settings
  - ai_brain_logs: Activity log of all AI actions
"""

import os
import logging
import uuid
import httpx
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
        self.llm_key = os.getenv('OPENAI_API_KEY', '') or os.getenv('EMERGENT_LLM_KEY', '')
        self._initialized = bool(self.llm_key)
        self._provider = "openai"

        if self._initialized:
            logger.info("🧠 Ross House AI Brain initialized with API key (env)")
        else:
            logger.warning("⚠️ No LLM key in env - will check DB config on first request")

    @property
    def is_available(self) -> bool:
        return self._initialized

    async def _ensure_key(self):
        """Try to load the LLM key from database if not in env."""
        if self._initialized:
            return True
        try:
            config_doc = await self.db.api_config.find_one({"_id": "main"})
            if config_doc:
                key = (config_doc.get("OPENAI_API_KEY") or
                       config_doc.get("openai_api_key") or
                       config_doc.get("EMERGENT_LLM_KEY") or
                       config_doc.get("emergent_llm_key", ""))
                if key:
                    self.llm_key = key
                    self._initialized = True
                    logger.info("🧠 AI Brain loaded LLM key from database config")
                    return True
        except Exception as e:
            logger.warning(f"Error loading LLM key from DB: {e}")
        return False

    async def _call_openai(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Call OpenAI GPT-4o directly using httpx (no SDK dependency)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.llm_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 800,
                        "temperature": 0.7,
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    logger.error(f"OpenAI API error {response.status_code}: {response.text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"OpenAI call failed: {e}")
            return None

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
        """Generate an AI response for a chat message.

        Backward-compatible: returns just the text.
        Use `generate_chat_response_with_actions` to also get suggested
        action buttons (PDFs, deep links, etc.)."""
        result = await self.generate_chat_response_with_actions(conversation_id, user_message, sender_name)
        if not result:
            return None
        return result.get("content")

    async def generate_chat_response_with_actions(
        self,
        conversation_id: str,
        user_message: str,
        sender_name: str = "Usuario",
    ) -> Optional[Dict[str, Any]]:
        """Generate an AI response AND a list of contextual action buttons.

        Returns a dict like:
            { "content": "Tu recibo está listo.",
              "actions": [
                {"type":"download_pdf","label":"Descargar PDF","payload":{"invoice_id":"..."}},
                {"type":"open_screen","label":"Ver historial","payload":{"route":"/invoices"}}
              ]
            }
        """
        if not await self._ensure_key():
            logger.warning("AI Brain not available - missing LLM key")
            return None
        if not await self.is_ai_enabled_for_conversation(conversation_id):
            logger.info(f"AI disabled for conversation {conversation_id}")
            return None

        try:
            context = await self._build_context(conversation_id, sender_name)
            system_prompt = ROSS_HOUSE_SYSTEM_PROMPT.format(context=context)

            recent_messages = await self._get_recent_messages(conversation_id, limit=10)
            conversation_text = ""
            for msg in recent_messages:
                role = "Inquilino" if msg["sender_type"] in ("tenant", "guest") else "Admin"
                if msg.get("is_ai"):
                    role = "Asistente IA"
                conversation_text += f"{role}: {msg['content']}\n"
            conversation_text += f"Inquilino: {user_message}\n"

            prompt = f"""Historial de la conversación:
{conversation_text}

Responde al último mensaje del inquilino de manera útil y profesional. Si la pregunta requiere información específica que no tienes, indica que un agente humano responderá pronto.

IMPORTANTE: Si el usuario pide un recibo, contrato o factura específica, NO inventes números ni links. El sistema añadirá automáticamente botones de descarga al final de tu respuesta. Solo confirma con un mensaje corto."""

            ai_text = await self._call_openai(system_prompt, prompt)
            if not ai_text:
                return None
            ai_text = self._sanitize_response(ai_text)

            # Build contextual actions based on the user's intent
            actions = await self._detect_actions(conversation_id, user_message)

            await self._log_action("chat_response", {
                "conversation_id": conversation_id,
                "user_message": user_message[:200],
                "ai_response": ai_text[:200],
                "actions_count": len(actions),
                "sender_name": sender_name,
            })

            return {"content": ai_text, "actions": actions}

        except Exception as e:
            logger.error(f"AI Brain error generating response: {e}")
            await self._log_action("error", {"conversation_id": conversation_id, "error": str(e)[:300]})
            return None

    async def _detect_actions(self, conversation_id: str, user_message: str) -> List[Dict[str, Any]]:
        """Detect intent from the user's message and generate contextual action buttons.

        Returns a list of action dicts that the frontend will render as buttons
        attached to the AI message bubble. Actions are intentionally lightweight
        (no PDFs embedded in chat history) — they're just deep-links/buttons
        that resolve to the existing PDF endpoints or screen routes."""
        actions: List[Dict[str, Any]] = []
        msg = (user_message or "").lower()

        # Resolve tenant context once
        from bson import ObjectId
        import re as _re

        try:
            conv = await self.db.chat_conversations.find_one({"_id": ObjectId(conversation_id)})
        except Exception:
            return actions
        if not conv:
            return actions

        app_user_id = conv.get("tenant_id")
        tenant = None
        if app_user_id:
            try:
                tenant = await self.db.tenants.find_one({"_id": ObjectId(app_user_id)})
            except Exception:
                tenant = None
            if not tenant:
                tenant = await self.db.tenants.find_one({"app_user_id": app_user_id})
            if not tenant and app_user_id:
                au = await self.db.app_users.find_one({"_id": ObjectId(app_user_id)})
                if au and au.get("email"):
                    em = au["email"].strip().lower()
                    tenant = await self.db.tenants.find_one({
                        "email": {"$regex": f"^{_re.escape(em)}$", "$options": "i"}
                    })

        tenant_ids_to_try = []
        if tenant:
            tenant_ids_to_try.append(str(tenant["_id"]))
        if app_user_id:
            tenant_ids_to_try.append(app_user_id)

        contract = None
        for tid in tenant_ids_to_try:
            contract = await self.db.rental_contracts.find_one({
                "tenant_id": tid, "status": {"$in": ["active", "activo"]},
            })
            if contract:
                break

        # ─── Intent: pagar / cuánto debo / cuándo pago ───
        if any(k in msg for k in ["pagar", "pago", "cuanto debo", "cuánto debo", "cuándo pago", "cuando pago"]):
            actions.append({
                "type": "open_screen",
                "label": "💳 Pagar renta",
                "payload": {"route": "/(tabs)"},
                "style": "primary",
            })

        # ─── Intent: recibo / receipt ───
        if any(k in msg for k in ["recibo", "receipt", "comprobante de pago"]):
            # Find a specific receipt if a period is mentioned (e.g., "junio", "mayo")
            specific_id = await self._find_payment_id_for_period(msg, tenant_ids_to_try)
            if specific_id:
                actions.append({
                    "type": "download_pdf",
                    "label": "📥 Descargar recibo PDF",
                    "payload": {
                        "endpoint": f"/api/tenant/invoices/{specific_id}/pdf",
                        "filename_hint": f"Recibo_{specific_id[:8]}.pdf",
                    },
                    "style": "primary",
                })
            actions.append({
                "type": "open_screen",
                "label": "📋 Ver historial de pagos",
                "payload": {"route": "/invoices"},
                "style": "secondary",
            })

        # ─── Intent: contrato / lease ───
        if any(k in msg for k in ["contrato", "contract", "lease"]):
            if contract:
                actions.append({
                    "type": "open_screen",
                    "label": "📄 Ver mi contrato",
                    "payload": {"route": "/(tabs)"},
                    "style": "primary",
                })

        # ─── Intent: factura de servicios / utility bill ───
        if any(k in msg for k in ["factura", "luz", "electricidad", "agua", "gas", "servicios"]):
            actions.append({
                "type": "open_screen",
                "label": "⚡ Mis servicios",
                "payload": {"route": "/services"},
                "style": "primary",
            })
            actions.append({
                "type": "open_screen",
                "label": "📋 Historial de facturas",
                "payload": {"route": "/invoices"},
                "style": "secondary",
            })

        # ─── Intent: mantenimiento / repair ───
        if any(k in msg for k in ["mantenimiento", "reparar", "reparación", "arreglar", "maintenance", "fix"]):
            actions.append({
                "type": "open_screen",
                "label": "🔧 Solicitar mantenimiento",
                "payload": {"route": "/maintenance"},
                "style": "primary",
            })

        # Dedupe by label and cap at 3 to keep UI clean
        seen = set()
        unique = []
        for a in actions:
            if a["label"] in seen:
                continue
            seen.add(a["label"])
            unique.append(a)
            if len(unique) >= 3:
                break
        return unique

    async def _find_payment_id_for_period(self, msg: str, tenant_ids: List[str]) -> Optional[str]:
        """If the user mentions a month name, try to find the matching paid
        rent receipt and return its id. Returns None if no match."""
        if not tenant_ids:
            return None

        # Spanish month names → month number
        months = {
            "enero":1, "febrero":2, "marzo":3, "abril":4, "mayo":5, "junio":6,
            "julio":7, "agosto":8, "septiembre":9, "setiembre":9, "octubre":10,
            "noviembre":11, "diciembre":12,
        }
        target_month: Optional[int] = None
        for name, num in months.items():
            if name in msg:
                target_month = num
                break

        # If no month found, return the most recent payment
        query = {"tenant_id": {"$in": tenant_ids}}
        cursor = self.db.rental_payments.find(query).sort("payment_date", -1).limit(20)
        payments = await cursor.to_list(length=20)
        if not payments:
            return None

        if target_month:
            for p in payments:
                period = p.get("period") or ""
                if period and len(period) >= 7:
                    try:
                        m = int(period.split("-")[1])
                        if m == target_month:
                            return str(p["_id"])
                    except Exception:
                        continue
                # Try payment_date month
                d = p.get("payment_date")
                if hasattr(d, "month") and d.month == target_month:
                    return str(p["_id"])
            return None

        # No month specified → return most recent
        return str(payments[0]["_id"])

    async def generate_email_response(self, subject: str, body: str, sender_email: str) -> Optional[str]:
        """Generate an AI response for an email inquiry."""
        if not await self._ensure_key():
            return None

        try:
            system_prompt = ROSS_HOUSE_SYSTEM_PROMPT.format(context="") + """

INSTRUCCIONES ADICIONALES PARA EMAILS:
- Formato tu respuesta como un email profesional
- Incluye un saludo apropiado y despedida
- Firma como "Equipo Ross House Rentals LLC"
- Mantén el tono profesional pero cálido
"""
            prompt = f"""Email recibido:
De: {sender_email}
Asunto: {subject}
Mensaje: {body}

Genera una respuesta profesional a este email."""

            ai_text = await self._call_openai(system_prompt, prompt)

            if ai_text:
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
        """Build context about the tenant and their property.

        Resolves the linked tenant doc via direct id / app_user_id / email /
        phone so the AI works for marketplace users (app_users collection)
        and not only legacy tenant logins.
        """
        context_parts = []

        try:
            from bson import ObjectId
            import re as _re
            from datetime import datetime, timezone

            # Get conversation info
            try:
                conv = await self.db.chat_conversations.find_one({"_id": ObjectId(conversation_id)})
            except Exception:
                conv = None
            if not conv:
                return ""

            display_name = conv.get('tenant_name', sender_name)
            context_parts.append(f"📌 CONVERSACIÓN CON: {display_name}")

            # `tenant_id` in chat_conversations is actually the app_user_id (see
            # chat_router.py). We need to find the matching tenants doc to query
            # contracts (which key by tenants._id).
            app_user_id = conv.get("tenant_id")  # actually the app_user id
            app_user = None
            if app_user_id:
                try:
                    app_user = await self.db.app_users.find_one({"_id": ObjectId(app_user_id)})
                except Exception:
                    app_user = None

            if app_user:
                context_parts.append(f"👤 Email: {app_user.get('email', 'N/A')}")
                context_parts.append(f"📱 Teléfono: {app_user.get('phone', 'N/A')}")

            # ── Resolve the linked tenant doc ──
            tenant = None
            if app_user_id:
                # 1) direct id (legacy tenant login)
                try:
                    tenant = await self.db.tenants.find_one({"_id": ObjectId(app_user_id)})
                except Exception:
                    tenant = None
                # 2) app_user_id link
                if not tenant:
                    tenant = await self.db.tenants.find_one({"app_user_id": app_user_id})
                # 3) email match
                if not tenant and app_user and app_user.get("email"):
                    em = app_user["email"].strip().lower()
                    tenant = await self.db.tenants.find_one({
                        "email": {"$regex": f"^{_re.escape(em)}$", "$options": "i"}
                    })

            tenant_ids_to_try = []
            if tenant:
                tenant_ids_to_try.append(str(tenant["_id"]))
            if app_user_id:
                tenant_ids_to_try.append(app_user_id)

            # ── Active contract ──
            contract = None
            for tid in tenant_ids_to_try:
                contract = await self.db.rental_contracts.find_one({
                    "tenant_id": tid,
                    "status": {"$in": ["active", "activo"]},
                })
                if contract:
                    break
                # ObjectId variant
                try:
                    contract = await self.db.rental_contracts.find_one({
                        "tenant_id": ObjectId(tid),
                        "status": {"$in": ["active", "activo"]},
                    })
                    if contract:
                        break
                except Exception:
                    pass

            if contract:
                # Property address
                prop_address = contract.get("property_address")
                if not prop_address and contract.get("property_id"):
                    try:
                        prop = await self.db.properties.find_one({"_id": ObjectId(contract["property_id"])})
                        if prop:
                            prop_address = prop.get("address") or prop.get("name")
                    except Exception:
                        pass
                if prop_address:
                    context_parts.append(f"📄 Propiedad alquilada: {prop_address}")

                rent = contract.get("monthly_rent") or contract.get("rent_amount") or 0
                context_parts.append(f"💰 Renta mensual: ${rent:.2f}")

                # Contract dates
                start = contract.get("start_date") or contract.get("lease_start")
                end = contract.get("end_date") or contract.get("lease_end")
                if start:
                    s = start.isoformat()[:10] if hasattr(start, 'isoformat') else str(start)[:10]
                    context_parts.append(f"📅 Inicio del contrato: {s}")
                if end:
                    e = end.isoformat()[:10] if hasattr(end, 'isoformat') else str(end)[:10]
                    context_parts.append(f"📅 Fin del contrato: {e}")

                # Next payment computation
                now_utc = datetime.now(timezone.utc)
                # Rent is due on the 1st of each month, grace period until day 5
                if now_utc.day <= 5:
                    # Current period still due
                    due_year = now_utc.year
                    due_month = now_utc.month
                else:
                    # Next period
                    due_month = now_utc.month + 1
                    due_year = now_utc.year
                    if due_month > 12:
                        due_month = 1
                        due_year += 1
                due_date_str = f"{due_year}-{due_month:02d}-01"
                period_label = f"{due_year}-{due_month:02d}"
                context_parts.append(f"🗓️ Próximo pago: ${rent:.2f} con fecha límite {due_date_str} (período {period_label})")
                if now_utc.day > 5:
                    context_parts.append("⏰ El período actual ya tiene cargo por mora ($50) si no se paga.")

                # Recent payments (last 3)
                contract_id_str = str(contract["_id"])
                payments_cursor = self.db.rental_payments.find({
                    "$or": [
                        {"contract_id": contract_id_str},
                        {"tenant_id": {"$in": tenant_ids_to_try}},
                    ]
                }).sort("payment_date", -1).limit(3)
                recent_payments = await payments_cursor.to_list(length=3)
                if recent_payments:
                    context_parts.append("💳 ÚLTIMOS PAGOS DE RENTA:")
                    for p in recent_payments:
                        amt = p.get("amount", 0)
                        when = p.get("payment_date") or p.get("created_at")
                        when_str = when.isoformat()[:10] if hasattr(when, 'isoformat') else str(when)[:10]
                        per = p.get("period", "")
                        context_parts.append(f"  • ${amt:.2f} pagado el {when_str} (período {per})")
                else:
                    context_parts.append("💳 Sin pagos registrados en el sistema todavía.")
            else:
                context_parts.append("⚠️ Este usuario no tiene un contrato activo registrado.")

            # ── Pending utility bills ──
            unpaid_bills = []
            for tid in tenant_ids_to_try:
                async for b in self.db.tenant_utility_bills.find({
                    "tenant_id": tid,
                    "status": {"$ne": "paid"},
                }).sort("due_date", 1).limit(5):
                    unpaid_bills.append(b)
                if unpaid_bills:
                    break
            if unpaid_bills:
                context_parts.append("💡 FACTURAS DE SERVICIOS PENDIENTES:")
                for b in unpaid_bills:
                    bt = b.get("type", "servicio")
                    amt = b.get("amount", 0)
                    per = b.get("period", "")
                    context_parts.append(f"  • {bt.title()} ${amt:.2f} ({per})")

            # ── Maintenance requests ──
            for tid in tenant_ids_to_try:
                maintenance_cursor = self.db.maintenance_requests.find({
                    "tenant_id": tid,
                    "status": {"$in": ["pending", "in_progress", "pendiente", "en_progreso"]}
                }).sort("created_at", -1).limit(3)
                maintenance = await maintenance_cursor.to_list(length=3)
                if maintenance:
                    context_parts.append("🔧 SOLICITUDES DE MANTENIMIENTO ACTIVAS:")
                    for m in maintenance:
                        context_parts.append(f"  - {m.get('description', 'N/A')[:60]} ({m.get('status', 'N/A')})")
                    break

            # ── Available properties (only show if no active contract) ──
            if not contract:
                active_props = await self.db.properties.find({"status": "active"}).to_list(length=10)
                if active_props:
                    context_parts.append("\n🏠 PROPIEDADES DISPONIBLES:")
                    for p in active_props:
                        context_parts.append(
                            f"  - {p.get('address', 'N/A')}: ${p.get('rent_amount', 'N/A')}/mes, "
                            f"{p.get('bedrooms', '?')} hab, {p.get('bathrooms', '?')} baños"
                        )

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
        await self._ensure_key()
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
