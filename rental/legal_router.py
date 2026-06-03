"""
Rental Legal Documents Router
==============================
Public & Admin endpoints for Terms and Privacy Policy.
Content is stored per-language in MongoDB `legal_documents` collection.
"""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin, serialize

router = APIRouter()

# ── Default seed content ──────────────────────────────────────────────────────

DEFAULT_TERMS_ES = """# Términos y Condiciones

**Última actualización: Junio 2026**

## 1. Aceptación de los Términos
Al acceder y utilizar la aplicación de Ross House Rentals LLC ("la Aplicación"), usted acepta cumplir con estos Términos y Condiciones.

## 2. Descripción del Servicio
Ross House Rentals LLC proporciona una plataforma de gestión de propiedades que permite a inquilinos, propietarios y administradores gestionar rentas, mantenimientos y pagos.

## 3. Cuentas de Usuario
- Usted es responsable de mantener la confidencialidad de su cuenta.
- Toda la información proporcionada debe ser precisa y actualizada.
- Nos reservamos el derecho de suspender cuentas que violen estos términos.

## 4. Pagos y Facturación
- Los pagos de renta se procesan a través de proveedores seguros de pago.
- Las fechas de vencimiento y montos se establecen en su contrato de arrendamiento.
- Cargos por pago tardío pueden aplicar según lo estipulado en su contrato.

## 5. Mantenimiento y Reparaciones
- Las solicitudes de mantenimiento deben reportarse a través de la Aplicación.
- Emergencias deben reportarse inmediatamente por teléfono.
- El tiempo de respuesta varía según la urgencia de la solicitud.

## 6. Privacidad
Su privacidad es importante para nosotros. Consulte nuestra Política de Privacidad para más detalles.

## 7. Limitación de Responsabilidad
Ross House Rentals LLC no será responsable por daños indirectos, incidentales o consecuentes.

## 8. Modificaciones
Nos reservamos el derecho de modificar estos términos en cualquier momento. Los cambios serán notificados a través de la Aplicación.

## 9. Contacto
Para preguntas sobre estos términos, contáctenos:
- 📞 (806) 934-2018
- 📧 info@rosshouserentals.com
"""

DEFAULT_TERMS_EN = """# Terms and Conditions

**Last updated: June 2026**

## 1. Acceptance of Terms
By accessing and using the Ross House Rentals LLC application ("the App"), you agree to comply with these Terms and Conditions.

## 2. Description of Service
Ross House Rentals LLC provides a property management platform that allows tenants, landlords, and administrators to manage rentals, maintenance, and payments.

## 3. User Accounts
- You are responsible for maintaining the confidentiality of your account.
- All information provided must be accurate and up to date.
- We reserve the right to suspend accounts that violate these terms.

## 4. Payments and Billing
- Rent payments are processed through secure payment providers.
- Due dates and amounts are established in your lease agreement.
- Late payment fees may apply as stipulated in your contract.

## 5. Maintenance and Repairs
- Maintenance requests must be reported through the App.
- Emergencies should be reported immediately by phone.
- Response time varies depending on the urgency of the request.

## 6. Privacy
Your privacy is important to us. Please refer to our Privacy Policy for more details.

## 7. Limitation of Liability
Ross House Rentals LLC shall not be liable for indirect, incidental, or consequential damages.

## 8. Modifications
We reserve the right to modify these terms at any time. Changes will be notified through the App.

## 9. Contact
For questions about these terms, contact us:
- 📞 (806) 934-2018
- 📧 info@rosshouserentals.com
"""

DEFAULT_PRIVACY_ES = """# Política de Privacidad

**Última actualización: Junio 2026**

## 1. Información que Recopilamos
Recopilamos la siguiente información cuando utiliza nuestra Aplicación:
- **Información personal:** Nombre, correo electrónico, número de teléfono.
- **Información de pago:** Datos necesarios para procesar pagos de renta.
- **Información del dispositivo:** Tipo de dispositivo, sistema operativo, tokens de notificación.
- **Información de ubicación:** Solo cuando usted lo autoriza, para mostrar propiedades cercanas.

## 2. Cómo Usamos su Información
Utilizamos su información para:
- Procesar pagos de renta y generar recibos.
- Enviar notificaciones sobre mantenimientos, pagos y actualizaciones.
- Mejorar nuestros servicios y la experiencia del usuario.
- Comunicarnos con usted sobre su cuenta y propiedades.

## 3. Compartir Información
No vendemos su información personal. Podemos compartirla con:
- Proveedores de servicios de pago (Stripe) para procesar transacciones.
- Propietarios de las propiedades que usted renta (información limitada).
- Autoridades legales cuando sea requerido por ley.

## 4. Seguridad de Datos
Implementamos medidas de seguridad para proteger su información:
- Encriptación de datos en tránsito y en reposo.
- Acceso restringido a información personal.
- Monitoreo regular de nuestros sistemas.

## 5. Sus Derechos
Usted tiene derecho a:
- Acceder a su información personal.
- Solicitar la corrección de datos inexactos.
- Solicitar la eliminación de su cuenta y datos.
- Optar por no recibir notificaciones no esenciales.

## 6. Retención de Datos
Mantenemos su información mientras su cuenta esté activa o según sea necesario para cumplir con obligaciones legales.

## 7. Menores de Edad
Nuestra Aplicación no está dirigida a menores de 18 años.

## 8. Cambios a esta Política
Notificaremos cualquier cambio material a esta política a través de la Aplicación.

## 9. Contacto
Para preguntas sobre privacidad:
- 📞 (806) 934-2018
- 📧 info@rosshouserentals.com
"""

DEFAULT_PRIVACY_EN = """# Privacy Policy

**Last updated: June 2026**

## 1. Information We Collect
We collect the following information when you use our App:
- **Personal information:** Name, email address, phone number.
- **Payment information:** Data necessary to process rent payments.
- **Device information:** Device type, operating system, notification tokens.
- **Location information:** Only when you authorize it, to show nearby properties.

## 2. How We Use Your Information
We use your information to:
- Process rent payments and generate receipts.
- Send notifications about maintenance, payments, and updates.
- Improve our services and user experience.
- Communicate with you about your account and properties.

## 3. Sharing Information
We do not sell your personal information. We may share it with:
- Payment service providers (Stripe) to process transactions.
- Property owners of the properties you rent (limited information).
- Legal authorities when required by law.

## 4. Data Security
We implement security measures to protect your information:
- Data encryption in transit and at rest.
- Restricted access to personal information.
- Regular monitoring of our systems.

## 5. Your Rights
You have the right to:
- Access your personal information.
- Request correction of inaccurate data.
- Request deletion of your account and data.
- Opt out of non-essential notifications.

## 6. Data Retention
We maintain your information while your account is active or as needed to comply with legal obligations.

## 7. Minors
Our App is not intended for persons under 18 years of age.

## 8. Changes to this Policy
We will notify any material changes to this policy through the App.

## 9. Contact
For privacy questions:
- 📞 (806) 934-2018
- 📧 info@rosshouserentals.com
"""


async def _ensure_legal_docs():
    """Seed default legal documents if they don't exist."""
    db = get_db()
    existing = await db.legal_documents.find_one({"_id": "legal_config"})
    if not existing:
        await db.legal_documents.insert_one({
            "_id": "legal_config",
            "terms_es": DEFAULT_TERMS_ES.strip(),
            "terms_en": DEFAULT_TERMS_EN.strip(),
            "privacy_es": DEFAULT_PRIVACY_ES.strip(),
            "privacy_en": DEFAULT_PRIVACY_EN.strip(),
            "updated_at": datetime.utcnow(),
        })
        logging.info("✅ Legal documents seeded with defaults")


# ── Public endpoint (no auth) ────────────────────────────────────────────────

@router.get('/public/legal-documents')
async def get_legal_documents(request: Request):
    """Return legal documents for the mobile app."""
    db = get_db()
    await _ensure_legal_docs()

    doc = await db.legal_documents.find_one({"_id": "legal_config"})
    if not doc:
        return {"success": True, "terms_es": "", "terms_en": "", "privacy_es": "", "privacy_en": ""}

    return {
        "success": True,
        "terms_es": doc.get("terms_es", ""),
        "terms_en": doc.get("terms_en", ""),
        "privacy_es": doc.get("privacy_es", ""),
        "privacy_en": doc.get("privacy_en", ""),
        "updated_at": doc.get("updated_at", "").isoformat() if isinstance(doc.get("updated_at"), datetime) else str(doc.get("updated_at", "")),
    }


# ── Admin endpoints ──────────────────────────────────────────────────────────

@router.get('/admin/legal-documents')
async def admin_get_legal_documents(request: Request):
    """Admin: Get legal documents for editing."""
    await auth_admin(request)
    db = get_db()
    await _ensure_legal_docs()

    doc = await db.legal_documents.find_one({"_id": "legal_config"})
    return {
        "success": True,
        "documents": {
            "terms_es": doc.get("terms_es", ""),
            "terms_en": doc.get("terms_en", ""),
            "privacy_es": doc.get("privacy_es", ""),
            "privacy_en": doc.get("privacy_en", ""),
            "updated_at": doc.get("updated_at", "").isoformat() if isinstance(doc.get("updated_at"), datetime) else str(doc.get("updated_at", "")),
        }
    }


@router.put('/admin/legal-documents')
async def admin_update_legal_documents(request: Request):
    """Admin: Update legal documents (terms and/or privacy, ES and/or EN)."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    update = {"updated_at": datetime.utcnow()}
    for field in ["terms_es", "terms_en", "privacy_es", "privacy_en"]:
        if field in data:
            update[field] = data[field]

    if len(update) <= 1:
        return {"success": False, "detail": "No fields to update"}

    await _ensure_legal_docs()
    await db.legal_documents.update_one(
        {"_id": "legal_config"},
        {"$set": update}
    )

    logging.info(f"✅ Legal documents updated: {list(update.keys())}")
    return {"success": True, "message": "Documentos legales actualizados"}
