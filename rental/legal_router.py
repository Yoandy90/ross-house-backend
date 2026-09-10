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

**Última actualización: 10 de septiembre de 2026**

## 1. Aceptación de los Términos
Al acceder y utilizar la aplicación de Ross House Rentals LLC ("la Aplicación"), usted acepta cumplir con estos Términos y Condiciones.

## 2. Descripción del Servicio
Ross House Rentals LLC opera sus propias propiedades de alquiler. El sitio público permite consultar viviendas y enviar solicitudes de interés. Los portales ofrecen funciones de cuenta, pagos, contratos y mantenimiento según el acceso de cada usuario. La administración de propiedades de terceros no está disponible actualmente.

Enviar una solicitud o registrarse en una lista de espera no reserva una vivienda ni garantiza aprobación o disponibilidad. El uso de la plataforma no sustituye su contrato de arrendamiento.

## 3. Cuentas de Usuario
- Usted es responsable de mantener la confidencialidad de su cuenta.
- Toda la información proporcionada debe ser precisa y actualizada.
- Nos reservamos el derecho de suspender cuentas que violen estos términos.

## 4. Pagos y Facturación
- Los pagos electrónicos habilitados utilizan Helcim. Los métodos disponibles se muestran en el flujo de pago. Guardar un método de pago no equivale a pagar la renta.
- Las fechas de vencimiento y montos se establecen en su contrato de arrendamiento.
- Cargos por pago tardío pueden aplicar según lo estipulado en su contrato.

## 5. Mantenimiento y Reparaciones
- Puede reportar solicitudes de mantenimiento a través de la Aplicación o comunicarse con nosotros por teléfono.
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

## 10. Cuenta y registros
La función de eliminación de cuenta no cancela contratos, saldos ni obligaciones pendientes. Consulte la página de eliminación de cuenta para conocer su alcance y cómo solicitar una revisión de sus datos.
"""

DEFAULT_TERMS_EN = """# Terms and Conditions

**Last updated: September 10, 2026**

## 1. Acceptance of Terms
By accessing and using the Ross House Rentals LLC application ("the App"), you agree to comply with these Terms and Conditions.

## 2. Description of Service
Ross House Rentals LLC operates its own rental properties. The public website lets visitors view homes and submit expressions of interest. Portals provide account, payment, contract and maintenance features according to each user's access. Third-party property management is not currently available.

Submitting a request or joining a waitlist does not reserve a home or guarantee approval or availability. Using the platform does not replace your lease agreement.

## 3. User Accounts
- You are responsible for maintaining the confidentiality of your account.
- All information provided must be accurate and up to date.
- We reserve the right to suspend accounts that violate these terms.

## 4. Payments and Billing
- Enabled electronic payments use Helcim. Available methods are shown in the payment flow. Saving a payment method does not constitute a rent payment.
- Due dates and amounts are established in your lease agreement.
- Late payment fees may apply as stipulated in your contract.

## 5. Maintenance and Repairs
- You can report maintenance requests through the App or contact us by phone.
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

## 10. Account and records
The account deletion feature does not cancel leases, balances or outstanding obligations. See the account deletion page for its scope and how to request a review of your data.
"""

DEFAULT_PRIVACY_ES = """# Política de Privacidad

**Última actualización: 10 de septiembre de 2026**

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
- Helcim para los pagos electrónicos habilitados. Conservamos referencias de transacciones y, cuando guarda un método, tokens y datos limitados para identificarlo.
- Propietarios de las propiedades que usted renta (información limitada).
- Autoridades legales cuando sea requerido por ley.

## 4. Seguridad de Datos
Implementamos medidas de seguridad para proteger su información:
- Controles de acceso a la información personal según la cuenta y sus permisos.
- Medidas técnicas diseñadas para proteger sus datos. Ningún sistema puede garantizar seguridad absoluta.

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

DEFAULT_COOKIES_ES = """# Política de Cookies

**Última actualización: 10 de septiembre de 2026**

## 1. ¿Qué son las cookies?
Las cookies son pequeños archivos de texto almacenados en su dispositivo cuando visita nuestro sitio web.

## 2. Cookies y almacenamiento del navegador
- Utilizamos cookies y almacenamiento local para funciones de sesión, autenticación y preferencias, según el portal utilizado.
- La preferencia de idioma puede guardarse en el almacenamiento local del navegador; no todo almacenamiento es una cookie.

## 3. Control del almacenamiento
Puede gestionar cookies y datos del sitio desde su navegador. Eliminarlos o bloquearlos puede cerrar su sesión o impedir algunas funciones.

## 4. Servicios de terceros
Los flujos de pago habilitados utilizan Helcim. Al abrir servicios externos, pueden aplicarse sus propias tecnologías de almacenamiento y políticas. Esto no significa que Helcim coloque cookies en todas las páginas de nuestro sitio.

## 5. Cambios a esta política
Actualizaremos esta política cuando incorporemos nuevas cookies o servicios.

## 6. Contacto
- 📞 (806) 934-2018
- 📧 privacy@rosshouserentals.com
"""

DEFAULT_COOKIES_EN = """# Cookie Policy

**Last updated: September 10, 2026**

## 1. What are cookies?
Cookies are small text files stored on your device when you visit our website.

## 2. Cookies and browser storage
- We use cookies and local storage for session, authentication and preference features, depending on the portal used.
- Your language preference may be saved in browser local storage; not all storage is a cookie.

## 3. Storage controls
You can manage cookies and site data through your browser. Removing or blocking them may sign you out or prevent some features from working.

## 4. Third-party services
Enabled payment flows use Helcim. When you open external services, their own storage technologies and policies may apply. This does not mean Helcim places cookies on every page of our site.

## 5. Changes to this policy
We will update this policy when we incorporate new cookies or services.

## 6. Contact
- 📞 (806) 934-2018
- 📧 privacy@rosshouserentals.com
"""

DEFAULT_ACCOUNT_DELETION_ES = """# Eliminación de cuenta y datos

**Última actualización: 10 de septiembre de 2026**

## 1. Cómo iniciar la eliminación
En la aplicación móvil, abra **Perfil → Eliminar mi Cuenta**. Escriba la palabra de confirmación que aparece en pantalla y confirme. La opción requiere iniciar sesión.

Si no puede acceder a su cuenta, escriba a **info@rosshouserentals.com** indicando su nombre y el correo o teléfono de la cuenta para solicitar asistencia y una revisión de sus datos. No envíe contraseñas ni datos completos de tarjetas. Podemos necesitar verificar su identidad antes de atender la solicitud.

## 2. Alcance de la función actual
La función modifica el nombre, correo y teléfono del perfil de la cuenta, marca ese perfil como eliminado y retira la configuración de autopago vinculada al usuario. También modifica el nombre visible del remitente en los mensajes asociados.

**No es un borrado completo de todos los registros.** Se conserva un registro de la solicitud que incluye identificador de usuario, nombre, correo, rol y fecha. Cambiar el nombre visible en mensajes no elimina su contenido.

## 3. Registros que pueden permanecer
La función actual no elimina automáticamente contratos, historial financiero, solicitudes de mantenimiento, contenido de mensajes ni todos los métodos de pago guardados. Tampoco constituye una confirmación de eliminación de datos en servicios externos. Puede solicitar por correo una revisión específica de los datos que permanecen y del motivo de su conservación.

## 4. Contratos, pagos y plazos
Eliminar la cuenta no termina un contrato de arrendamiento, cancela una deuda ni revierte un pago. Si tiene un contrato o saldo pendiente, contáctenos para coordinar el acceso a documentos y la comunicación sobre su vivienda.

Esta función no establece un plazo automático de borrado para todos los registros. La conservación debe revisarse según el tipo de dato, las obligaciones aplicables y las solicitudes recibidas; esta página no atribuye un plazo universal al IRS o a la ley de Texas.

## 5. Antes de confirmar
Descargue los documentos y recibos que necesite. Si no está seguro del alcance de la eliminación, contáctenos antes de confirmar.

## 6. Contacto
- Correo: **info@rosshouserentals.com**
- Teléfono: (806) 934-2018
"""



DEFAULT_ACCOUNT_DELETION_EN = """# Account and data deletion

**Last updated: September 10, 2026**

## 1. How to start deletion
In the mobile app, open **Profile → Delete My Account**. Type the confirmation word displayed on screen and confirm. You must be signed in to use this option.

If you cannot access your account, email **info@rosshouserentals.com** with your name and account email or phone number to request assistance and a review of your data. Do not send passwords or full card details. We may need to verify your identity before handling your request.

## 2. Scope of the current feature
The feature changes the account profile name, email and phone number, marks that profile as deleted and removes the autopay configuration linked to the user. It also changes the displayed sender name on associated messages.

**It does not erase every record.** A request record is retained, including user ID, name, email, role and date. Changing the displayed sender name does not delete message content.

## 3. Records that may remain
The current feature does not automatically delete leases, financial history, maintenance requests, message content or all saved payment methods. It also does not confirm deletion by external services. You may request a specific review by email of the data that remains and the reason for retaining it.

## 4. Leases, payments and timing
Deleting an account does not terminate a lease, cancel a debt or reverse a payment. If you have an active lease or outstanding balance, contact us to coordinate access to documents and communication about your home.

This feature does not set an automatic deletion deadline for all records. Retention must be reviewed according to the type of data, applicable obligations and requests received; this page does not attribute a universal period to the IRS or Texas law.

## 5. Before confirming
Download the documents and receipts you need. If you are unsure about the scope of deletion, contact us before confirming.

## 6. Contact
- Email: **info@rosshouserentals.com**
- Phone: (806) 934-2018
"""

DEFAULT_PRIVACY_EN = """# Privacy Policy

**Last updated: September 10, 2026**

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
- Helcim for enabled electronic payments. We retain transaction references and, when you save a method, tokens and limited details to identify it.
- Property owners of the properties you rent (limited information).
- Legal authorities when required by law.

## 4. Data Security
We implement security measures to protect your information:
- Access controls for personal information based on the account and its permissions.
- Technical measures designed to protect your data. No system can guarantee absolute security.

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
    defaults = {
        "terms_es": DEFAULT_TERMS_ES.strip(),
        "terms_en": DEFAULT_TERMS_EN.strip(),
        "privacy_es": DEFAULT_PRIVACY_ES.strip(),
        "privacy_en": DEFAULT_PRIVACY_EN.strip(),
        "cookies_es": DEFAULT_COOKIES_ES.strip(),
        "cookies_en": DEFAULT_COOKIES_EN.strip(),
        "account_deletion_es": DEFAULT_ACCOUNT_DELETION_ES.strip(),
        "account_deletion_en": DEFAULT_ACCOUNT_DELETION_EN.strip(),
    }
    if not existing:
        await db.legal_documents.insert_one({
            "_id": "legal_config",
            **defaults,
            "updated_at": datetime.utcnow(),
        })
        logging.info("✅ Legal documents seeded with defaults")
    else:
        from rental.legal_content_revision import revise_known_seed_documents
        await revise_known_seed_documents(db.legal_documents, existing)
        # Backfill any missing fields (e.g. cookies / account_deletion in older deployments)
        missing = {k: v for k, v in defaults.items() if k not in existing or not existing.get(k)}
        if missing:
            await db.legal_documents.update_one(
                {"_id": "legal_config"},
                {"$set": {**missing, "updated_at": datetime.utcnow()}}
            )
            logging.info(f"✅ Legal documents backfilled: {list(missing.keys())}")


# ── Public endpoint (no auth) ────────────────────────────────────────────────

@router.get('/public/legal-documents')
async def get_legal_documents(request: Request):
    """Return legal documents for the mobile app."""
    db = get_db()
    await _ensure_legal_docs()

    doc = await db.legal_documents.find_one({"_id": "legal_config"})
    if not doc:
        return {"success": True, "terms_es": "", "terms_en": "", "privacy_es": "", "privacy_en": "", "cookies_es": "", "cookies_en": "", "account_deletion_es": "", "account_deletion_en": ""}

    return {
        "success": True,
        "terms_es": doc.get("terms_es", ""),
        "terms_en": doc.get("terms_en", ""),
        "privacy_es": doc.get("privacy_es", ""),
        "privacy_en": doc.get("privacy_en", ""),
        "cookies_es": doc.get("cookies_es", ""),
        "cookies_en": doc.get("cookies_en", ""),
        "account_deletion_es": doc.get("account_deletion_es", ""),
        "account_deletion_en": doc.get("account_deletion_en", ""),
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
            "cookies_es": doc.get("cookies_es", ""),
            "cookies_en": doc.get("cookies_en", ""),
            "account_deletion_es": doc.get("account_deletion_es", ""),
            "account_deletion_en": doc.get("account_deletion_en", ""),
            "updated_at": doc.get("updated_at", "").isoformat() if isinstance(doc.get("updated_at"), datetime) else str(doc.get("updated_at", "")),
        }
    }


@router.put('/admin/legal-documents')
async def admin_update_legal_documents(request: Request):
    """Admin: Update legal documents (terms, privacy, cookies, account_deletion - ES and/or EN)."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    update = {"updated_at": datetime.utcnow()}
    for field in ["terms_es", "terms_en", "privacy_es", "privacy_en", "cookies_es", "cookies_en", "account_deletion_es", "account_deletion_en"]:
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

