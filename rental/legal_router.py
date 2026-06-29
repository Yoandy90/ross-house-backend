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

DEFAULT_COOKIES_ES = """# Política de Cookies

**Última actualización: Junio 2026**

## 1. ¿Qué son las cookies?
Las cookies son pequeños archivos de texto almacenados en su dispositivo cuando visita nuestro sitio web.

## 2. Cookies que utilizamos
- **Estrictamente necesarias:** Para el funcionamiento del sitio (sesión, autenticación, CSRF).
- **Funcionales:** Recuerdan sus preferencias (idioma, modo oscuro).
- **Analíticas:** Estadísticas anónimas de uso para mejorar el servicio.
- **No usamos** cookies publicitarias ni de marketing de terceros.

## 3. Control de cookies
Puede deshabilitar cookies desde su navegador, pero algunas funciones de la App pueden no funcionar correctamente.

## 4. Cookies de terceros
- **Stripe:** Para procesamiento seguro de pagos.
- **Google Maps (opcional):** Para mostrar ubicaciones de propiedades.

## 5. Cambios a esta política
Actualizaremos esta política cuando incorporemos nuevas cookies o servicios.

## 6. Contacto
- 📞 (806) 934-2018
- 📧 privacy@rosshouserentals.com
"""

DEFAULT_COOKIES_EN = """# Cookie Policy

**Last updated: June 2026**

## 1. What are cookies?
Cookies are small text files stored on your device when you visit our website.

## 2. Cookies we use
- **Strictly necessary:** For site operation (session, authentication, CSRF).
- **Functional:** Remember your preferences (language, dark mode).
- **Analytics:** Anonymous usage statistics to improve the service.
- **We do NOT use** third-party advertising or marketing cookies.

## 3. Cookie control
You can disable cookies in your browser, but some App features may not work correctly.

## 4. Third-party cookies
- **Stripe:** For secure payment processing.
- **Google Maps (optional):** To display property locations.

## 5. Changes to this policy
We will update this policy when we incorporate new cookies or services.

## 6. Contact
- 📞 (806) 934-2018
- 📧 privacy@rosshouserentals.com
"""

DEFAULT_ACCOUNT_DELETION_ES = """# Cómo Eliminar tu Cuenta — Preguntas Frecuentes

**Última actualización: Junio 2026**

> ℹ️ La eliminación de cuenta se realiza **directamente dentro de la app**. Para acceder a esta opción debes estar **registrado e iniciado sesión**.

## ❓ ¿Cómo elimino mi cuenta?
1. Abre la app **Ross House Rentals** en tu iPhone o Android.
2. Inicia sesión con tu correo o teléfono.
3. Ve a **Perfil → Eliminar mi Cuenta**.
4. Confirma escribiendo la palabra **ELIMINAR** y toca el botón rojo.
5. Tu cuenta será eliminada inmediatamente.

## ❓ ¿Qué pasa si no puedo iniciar sesión?
Si perdiste acceso a tu cuenta y deseas eliminarla, envíanos un correo a **privacy@rosshouserentals.com** con:
- Asunto: **Solicitud de Eliminación de Cuenta**
- Tu nombre completo
- Correo electrónico o teléfono registrado en la app
- Motivo (opcional)

Procesaremos tu solicitud en un máximo de **30 días**.

## ❓ ¿Qué datos se eliminan?
- Información personal de tu perfil
- Métodos de pago guardados (tarjetas, ACH)
- Solicitudes de mantenimiento y mensajes
- Tokens de notificaciones push

## ❓ ¿Qué datos se conservan?
Por obligación legal (IRS, ley de Texas, FCRA):
- Registros financieros — hasta **7 años**
- Contratos de arrendamiento — hasta **3 años después** del fin del contrato
- Verificaciones de antecedentes — destruidas en 30 días

## ❓ ¿Es reversible?
No. Una vez eliminada, no podemos restaurar la cuenta. Asegúrate de descargar cualquier recibo o documento que necesites conservar.

## ❓ ¿Tengo contratos activos?
Si tienes un contrato de arrendamiento vigente, **debes esperar** a que termine antes de eliminar tu cuenta.

## 📞 Contacto
- 📧 **privacy@rosshouserentals.com**
- 📞 (806) 934-2018
- 📍 305 Bruce Ave, Dumas, TX 79029
"""

DEFAULT_ACCOUNT_DELETION_EN = """# How to Delete Your Account — Frequently Asked Questions

**Last updated: June 2026**

> ℹ️ Account deletion is performed **directly inside the app**. To access this option you must be **registered and signed in**.

## ❓ How do I delete my account?
1. Open the **Ross House Rentals** app on your iPhone or Android.
2. Sign in with your email or phone.
3. Go to **Profile → Delete My Account**.
4. Confirm by typing the word **DELETE** and tap the red button.
5. Your account will be removed immediately.

## ❓ What if I can't sign in?
If you lost access to your account and wish to delete it, email us at **privacy@rosshouserentals.com** with:
- Subject: **Account Deletion Request**
- Your full name
- Email or phone number registered in the app
- Reason (optional)

We will process your request within **30 days**.

## ❓ What data is deleted?
- Personal profile information
- Saved payment methods (cards, ACH)
- Maintenance requests and messages
- Push notification tokens

## ❓ What data is kept?
For legal obligations (IRS, Texas law, FCRA):
- Financial records — up to **7 years**
- Lease agreements — up to **3 years after** the lease ends
- Background checks — destroyed within 30 days

## ❓ Is this reversible?
No. Once deleted, we cannot restore the account. Make sure to download any receipts or documents you need to keep.

## ❓ Do I have active leases?
If you have an active lease agreement, you **must wait** until it ends before deleting your account.

## 📞 Contact
- 📧 **privacy@rosshouserentals.com**
- 📞 (806) 934-2018
- 📍 305 Bruce Ave, Dumas, TX 79029
"""

DEFAULT_PRIVACY_EN = """# Privacy Policy

**Última actualización: Junio 2026**

En cumplimiento con la **Guideline 5.1.1(v) de App Store**, esta página explica cómo solicitar la eliminación de su cuenta de Ross House Rentals.

## 1. Eliminar desde la App
1. Inicie sesión en la App.
2. Vaya a **Perfil → Configuración → Eliminar Cuenta**.
3. Confirme con su PIN o contraseña.
4. Su cuenta y datos personales se eliminarán dentro de **30 días**.

## 2. Eliminar por correo electrónico
Envíe un correo a **privacy@rosshouserentals.com** con:
- Asunto: "Solicitud de Eliminación de Cuenta"
- Nombre completo, correo registrado y motivo (opcional)

Procesaremos su solicitud en un máximo de **30 días**.

## 3. Datos que se eliminan
- Información personal de su perfil.
- Métodos de pago (tarjetas / ACH).
- Solicitudes de mantenimiento, mensajes y notificaciones.
- Tokens de notificaciones push.

## 4. Datos que se conservan (obligación legal)
- Registros financieros: hasta **7 años** (requerido por el IRS).
- Contratos de arrendamiento: hasta **3 años después** del fin del contrato (ley de Texas).
- Verificaciones de antecedentes: destruidas en 30 días.

## 5. Antes de eliminar
- Asegúrese de que **no tiene contratos de arrendamiento activos**.
- Descargue cualquier recibo o documento que necesite conservar.

## 6. Contacto
- 📞 (806) 934-2018
- 📧 privacy@rosshouserentals.com
- 📍 305 Bruce Ave, Dumas, TX 79029
"""

DEFAULT_ACCOUNT_DELETION_EN = """# Account Deletion

**Last updated: June 2026**

In compliance with **App Store Guideline 5.1.1(v)**, this page explains how to request the deletion of your Ross House Rentals account.

## 1. Delete from the App
1. Log in to the App.
2. Go to **Profile → Settings → Delete Account**.
3. Confirm with your PIN or password.
4. Your account and personal data will be deleted within **30 days**.

## 2. Delete by email
Send an email to **privacy@rosshouserentals.com** with:
- Subject: "Account Deletion Request"
- Your full name, registered email, and reason (optional)

We will process your request within **30 days**.

## 3. Data that will be deleted
- Personal profile information.
- Payment methods (cards / ACH).
- Maintenance requests, messages, and notifications.
- Push notification tokens.

## 4. Data we are required to retain (legal obligation)
- Financial records: up to **7 years** (required by IRS).
- Lease agreements: up to **3 years after** the lease ends (Texas law).
- Background checks: destroyed within 30 days.

## 5. Before deleting
- Make sure you have **no active lease agreements**.
- Download any receipts or documents you need to keep.

## 6. Contact
- 📞 (806) 934-2018
- 📧 privacy@rosshouserentals.com
- 📍 305 Bruce Ave, Dumas, TX 79029
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
