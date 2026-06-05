"""
Generate comprehensive PDF report of Ross House Rentals LLC platform.
Includes all roles, features, and flows.
"""
import io
import base64
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, ListFlowable, ListItem
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# Colors
BRAND_RED = HexColor('#C8102E')
DARK_BG = HexColor('#1a1a2e')
NAVY = HexColor('#16213e')
BLUE = HexColor('#3B82F6')
GREEN = HexColor('#10B981')
PURPLE = HexColor('#8B5CF6')
GOLD = HexColor('#F59E0B')
GRAY = HexColor('#6B7280')
LIGHT_BG = HexColor('#F3F4F6')
WHITE = white


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'CoverTitle', fontName='Helvetica-Bold', fontSize=28,
        textColor=BRAND_RED, alignment=TA_CENTER, spaceAfter=8
    ))
    styles.add(ParagraphStyle(
        'CoverSub', fontName='Helvetica', fontSize=14,
        textColor=GRAY, alignment=TA_CENTER, spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        'SectionTitle', fontName='Helvetica-Bold', fontSize=18,
        textColor=BRAND_RED, spaceBefore=20, spaceAfter=10,
        borderWidth=0, borderPadding=0
    ))
    styles.add(ParagraphStyle(
        'SubSection', fontName='Helvetica-Bold', fontSize=13,
        textColor=NAVY, spaceBefore=14, spaceAfter=6
    ))
    styles.add(ParagraphStyle(
        'FeatureTitle', fontName='Helvetica-Bold', fontSize=11,
        textColor=HexColor('#1F2937'), spaceBefore=8, spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        'Body', fontName='Helvetica', fontSize=10,
        textColor=HexColor('#374151'), spaceAfter=4, leading=14
    ))
    styles.add(ParagraphStyle(
        'BodyBold', fontName='Helvetica-Bold', fontSize=10,
        textColor=HexColor('#1F2937'), spaceAfter=4, leading=14
    ))
    styles.add(ParagraphStyle(
        'BulletItem', fontName='Helvetica', fontSize=10,
        textColor=HexColor('#374151'), leftIndent=20, spaceAfter=3, leading=14,
        bulletIndent=10
    ))
    styles.add(ParagraphStyle(
        'RoleBadge', fontName='Helvetica-Bold', fontSize=12,
        textColor=white, alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        'Footer', fontName='Helvetica', fontSize=8,
        textColor=GRAY, alignment=TA_CENTER
    ))
    return styles


def generate_report():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=0.6*inch, bottomMargin=0.6*inch,
                            leftMargin=0.75*inch, rightMargin=0.75*inch)
    styles = build_styles()
    elements = []

    # ══════════════════════════════════════════════════════════
    # COVER PAGE
    # ══════════════════════════════════════════════════════════
    elements.append(Spacer(1, 1.5*inch))
    elements.append(Paragraph("ROSS HOUSE RENTALS LLC", styles['CoverTitle']))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Plataforma de Gestión de Propiedades", styles['CoverSub']))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Reporte Completo de Funcionalidades", styles['CoverSub']))
    elements.append(Spacer(1, 30))
    elements.append(HRFlowable(width="60%", thickness=2, color=BRAND_RED, hAlign='CENTER'))
    elements.append(Spacer(1, 20))

    cover_data = [
        ['Plataformas', 'iOS App (TestFlight) • Web Admin Panel'],
        ['Backend', 'FastAPI + MongoDB Atlas (Railway)'],
        ['Web Admin', 'Next.js 14 (Vercel)'],
        ['App Móvil', 'Expo React Native (iOS/Android)'],
        ['Fecha', datetime.now().strftime('%d de %B, %Y')],
        ['Versión', 'v2.0 — Junio 2026'],
    ]
    ct = Table(cover_data, colWidths=[120, 340])
    ct.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), BRAND_RED),
        ('TEXTCOLOR', (1, 0), (1, -1), HexColor('#374151')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, HexColor('#E5E7EB')),
    ]))
    elements.append(ct)
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # TABLE OF CONTENTS
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("ÍNDICE DE CONTENIDOS", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 12))
    toc_items = [
        "1. Resumen Ejecutivo de la Plataforma",
        "2. Arquitectura del Sistema",
        "3. Roles de Usuario y Permisos",
        "4. Rol: Invitado (Guest) — Funcionalidades",
        "5. Rol: Inquilino (Tenant) — Funcionalidades",
        "6. Rol: Propietario (Landlord) — Funcionalidades",
        "7. Rol: Comprador (Buyer) — Funcionalidades",
        "8. Rol: Administrador (Admin) — App Móvil",
        "9. Panel de Administración Web — Módulos",
        "10. Integraciones de Terceros",
        "11. Seguridad y Protección de Datos",
        "12. Flujos Principales del Negocio",
    ]
    for item in toc_items:
        elements.append(Paragraph(item, styles['Body']))
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 1. EXECUTIVE SUMMARY
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("1. RESUMEN EJECUTIVO", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Ross House Rentals LLC es una plataforma integral de gestión de propiedades que conecta "
        "a propietarios, inquilinos, compradores e invitados a través de una app móvil nativa (iOS/Android) "
        "y un panel de administración web. La plataforma automatiza los procesos de arrendamiento, cobro de rentas, "
        "mantenimiento, contratos legales bilingües (EN/ES), análisis de mercado inmobiliario, "
        "y comunicación en tiempo real.",
        styles['Body']
    ))
    elements.append(Spacer(1, 8))

    stats_data = [
        ['Componente', 'Detalle'],
        ['Pantallas en App Móvil', '30+ pantallas nativas (iOS/Android/Web)'],
        ['Módulos Admin Web', '20 módulos de gestión'],
        ['Endpoints API', '100+ endpoints REST'],
        ['Roles de Usuario', '5 (Guest, Tenant, Landlord, Buyer, Admin)'],
        ['Idiomas', 'Español e Inglés (i18n completo)'],
        ['Contratos', 'Generación PDF bilingüe con foto y firma digital'],
        ['Pagos', 'Stripe Connect + ACH + Pagos manuales'],
        ['Mercado', 'Datos de Mashvisor API (análisis de inversión)'],
    ]
    st = Table(stats_data, colWidths=[160, 310])
    st.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(st)
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 2. ARCHITECTURE
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("2. ARQUITECTURA DEL SISTEMA", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))

    arch_data = [
        ['Capa', 'Tecnología', 'Hosting'],
        ['App Móvil', 'Expo React Native + TypeScript', 'TestFlight (iOS) / EAS Build'],
        ['Panel Admin', 'Next.js 14 + TailwindCSS', 'Vercel'],
        ['Backend API', 'FastAPI (Python 3.12)', 'Railway'],
        ['Base de Datos', 'MongoDB Atlas', 'AWS (Atlas Cloud)'],
        ['Almacenamiento', 'Emergent Object Storage', 'Cloud'],
        ['Email', 'SendGrid API', 'SaaS'],
        ['SMS/WhatsApp', 'Twilio API', 'SaaS'],
        ['Pagos', 'Stripe Connect', 'SaaS'],
        ['Mercado', 'Mashvisor API', 'SaaS'],
        ['AI/LLM', 'OpenAI GPT-4o (via Emergent)', 'SaaS'],
    ]
    at = Table(arch_data, colWidths=[110, 200, 160])
    at.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(at)
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 3. ROLES
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("3. ROLES DE USUARIO Y PERMISOS", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))

    roles_data = [
        ['Rol', 'Cómo se crea', 'Acceso', 'Restricciones'],
        ['Invitado\n(Guest)', 'Auto-registro\nen la app', 'Explorar propiedades,\nmercado, perfil', 'Sin pagos,\nsin mantenimiento,\nsin contratos'],
        ['Inquilino\n(Tenant)', 'Creado por\nel Admin', 'Pagos, contratos,\nmantenimiento,\nchat, documentos', 'Acceso completo\na su propiedad\nasignada'],
        ['Propietario\n(Landlord)', 'Auto-registro\nen la app', 'Dashboard ingresos,\npublicar propiedades,\ncontratos', 'Solo ve sus\npropias propiedades'],
        ['Comprador\n(Buyer)', 'Auto-registro\nen la app', 'Explorar propiedades\nen venta, contactar', 'Sin pagos de\nrenta'],
        ['Admin', 'Configurado\nen BD', 'TODO: Panel web\n+ app completa', 'Sin restricciones'],
    ]
    rt = Table(roles_data, colWidths=[75, 90, 130, 120])
    rt.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (0, 1), HexColor('#EDE9FE')),
        ('BACKGROUND', (0, 2), (0, 2), HexColor('#FEE2E2')),
        ('BACKGROUND', (0, 3), (0, 3), HexColor('#DBEAFE')),
        ('BACKGROUND', (0, 4), (0, 4), HexColor('#D1FAE5')),
        ('BACKGROUND', (0, 5), (0, 5), HexColor('#FEF3C7')),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(rt)
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 4. GUEST ROLE
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("4. ROL: INVITADO (GUEST)", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=PURPLE))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("El rol Invitado es para usuarios que se registran en la app por curiosidad "
        "o están evaluando propiedades antes de convertirse en inquilinos.", styles['Body']))
    elements.append(Spacer(1, 6))

    guest_features = [
        ("Registro con Email y Contraseña", "Se registra seleccionando 'Explorar'. Recibe email de bienvenida con credenciales."),
        ("Dashboard de Bienvenida", "Pantalla principal con banner de exploración y acciones rápidas: Ver Propiedades, Mercado, Contacto, Perfil."),
        ("Explorar Propiedades", "Navega propiedades disponibles para renta con fotos, detalles, ubicación en mapa."),
        ("Mercado Inmobiliario (Mashvisor)", "Accede a datos de mercado: precios por zona, ROI, propiedades en venta, filtros por tipo/precio/habitaciones."),
        ("Perfil y Foto", "Puede subir foto de perfil (cámara o galería), editar nombre, teléfono, email."),
        ("Chat con Administración", "Puede enviar mensajes al admin para consultas sobre propiedades."),
        ("Notificaciones Push", "Recibe notificaciones sobre propiedades nuevas o mensajes."),
        ("Cambiar Contraseña", "Puede actualizar su contraseña desde el perfil."),
        ("Preguntas Frecuentes", "Accede a la sección de FAQs de la app."),
        ("Restricciones", "NO puede: pagar renta, ver contratos, solicitar mantenimiento, firmar documentos. El tab 'Pagos' está oculto."),
    ]
    for title, desc in guest_features:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 5. TENANT ROLE
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("5. ROL: INQUILINO (TENANT)", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("El Inquilino es un usuario creado por el Admin cuando firma un contrato de arrendamiento. "
        "Tiene acceso completo a todas las funciones de gestión de su vivienda.", styles['Body']))

    tenant_features = [
        ("Dashboard de Pagos", "Pantalla principal con gauge de renta mensual, próximo pago, estado (Pagado/Pendiente/Vencido), días restantes, pagos recientes."),
        ("Pagar Renta", "Flujo de pago integrado: seleccionar mes, monto, método de pago (Stripe, tarjeta, ACH), confirmación. Historial de recibos."),
        ("Métodos de Pago", "Agregar/eliminar tarjetas de crédito/débito via Stripe. Configurar autopago mensual."),
        ("Mi Propiedad", "Ver detalles de la propiedad asignada: dirección, habitaciones, baños, fotos categorizadas (cocina, patio, etc.) con visor fullscreen."),
        ("Contratos", "Ver contratos activos, firma digital integrada, addendums. Acceso al PDF del contrato con su foto y cláusula de consentimiento fotográfico."),
        ("Solicitar Mantenimiento", "Crear solicitudes de mantenimiento: descripción, prioridad (urgente/normal/baja), fotos del problema. Seguimiento del estado."),
        ("Chat en Tiempo Real", "Mensajería directa con el administrador. Contador de mensajes no leídos en el header."),
        ("Centro de Firmas", "Firmar documentos pendientes digitalmente (contratos, addendums, checklist de mudanza)."),
        ("Documentos", "Acceder a documentos legales: términos y condiciones, política de privacidad."),
        ("Emergencias / Contacto", "Información de contacto de emergencia, teléfono directo del admin."),
        ("Notificaciones Push", "Alertas de pago vencido, mensajes nuevos, actualizaciones de mantenimiento."),
        ("Foto de Perfil", "Subir foto de perfil desde la app. El admin puede ver esta foto separada de la foto oficial tomada en la oficina."),
        ("Cambiar Contraseña", "Actualizar contraseña desde perfil."),
        ("Eliminar Cuenta", "Opción de solicitar eliminación de cuenta (con confirmación)."),
    ]
    for title, desc in tenant_features:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 6. LANDLORD ROLE
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("6. ROL: PROPIETARIO (LANDLORD)", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BLUE))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Los Propietarios publican sus propiedades en el marketplace y reciben pagos a través de Stripe Connect.", styles['Body']))

    landlord_features = [
        ("Dashboard de Propietario", "Panel con resumen de ingresos, propiedades activas, ocupación, pagos recibidos, gastos."),
        ("Publicar Propiedades", "Crear listados con: dirección, precio, tipo (casa/apartamento/terreno), fotos, descripción, amenidades. Subir múltiples fotos."),
        ("Mis Listados", "Ver y gestionar todas las propiedades publicadas. Editar precio, estado, fotos."),
        ("Stripe Connect", "Onboarding de Stripe Connect para recibir pagos directos de inquilinos. Ver payouts."),
        ("Dashboard de Ingresos", "Tab de Pagos muestra ingresos en lugar de gastos. Gráficas de rendimiento."),
        ("Contratos", "Ver contratos asociados a sus propiedades."),
        ("Datos Bancarios", "Configurar cuenta bancaria para recibir pagos."),
        ("Chat con Admin", "Comunicación directa con el administrador."),
        ("Comisión", "Se aplica un 10% de comisión por defecto sobre las rentas gestionadas."),
    ]
    for title, desc in landlord_features:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 7. BUYER ROLE
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("7. ROL: COMPRADOR (BUYER)", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=GREEN))
    elements.append(Spacer(1, 8))

    buyer_features = [
        ("Explorar Propiedades en Venta", "Navega propiedades disponibles para compra con filtros por precio, zona, tipo."),
        ("Análisis de Mercado", "Datos de Mashvisor: precios promedio, ROI estimado, comparación de barrios."),
        ("Contactar Agente", "Enviar consultas sobre propiedades específicas."),
        ("Perfil y Notificaciones", "Gestionar perfil, recibir alertas de nuevas propiedades."),
        ("Restricciones", "Sin acceso a pagos de renta ni mantenimiento (tab Pagos oculto)."),
    ]
    for title, desc in buyer_features:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))

    # ══════════════════════════════════════════════════════════
    # 8. ADMIN APP
    # ══════════════════════════════════════════════════════════
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("8. ROL: ADMINISTRADOR — APP MÓVIL", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=GOLD))
    elements.append(Spacer(1, 8))

    admin_features = [
        ("Dashboard Admin", "Resumen ejecutivo: propiedades activas, inquilinos, pagos pendientes, mantenimiento abierto, contratos vencidos."),
        ("Gestión de Propiedades", "Agregar/editar propiedades desde la app. Subir fotos categorizadas."),
        ("Gestión de Contratos", "Ver todos los contratos, estados, renovaciones pendientes."),
        ("Chat Centralizado", "Ver todas las conversaciones con inquilinos y responder desde la app."),
        ("Notificaciones Admin", "Alertas de pagos vencidos, solicitudes de mantenimiento, mensajes nuevos."),
        ("Acceso Completo", "El admin puede acceder a todas las funciones de todos los roles."),
    ]
    for title, desc in admin_features:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 9. ADMIN WEB PANEL
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("9. PANEL DE ADMINISTRACIÓN WEB", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph("URL: https://ross-house-rentals.vercel.app/admin", styles['Body']))
    elements.append(Paragraph("Acceso exclusivo para el administrador. 20 módulos de gestión:", styles['Body']))
    elements.append(Spacer(1, 8))

    admin_modules = [
        ("Dashboard Principal", "Vista general del negocio: inquilinos activos, propiedades, ingresos del mes, pagos pendientes, gráficos de tendencia, contratos próximos a vencer, alertas."),
        ("Propiedades", "CRUD completo de propiedades. Fotos categorizadas (cocina, sala, patio, baño, etc.), ubicación GPS, detalles de amenidades, estado (disponible/ocupada/mantenimiento)."),
        ("Inquilinos", "Gestión completa de inquilinos con: webcam para foto oficial, formulario detallado (nombre, teléfono, email, ID, empleo, ingresos, contacto de emergencia). Auto-creación de usuario con contraseña y email de bienvenida. Dos fotos: Foto Oficial (webcam oficina) vs Foto del App (subida por el usuario)."),
        ("Contratos", "Generación de contratos PDF bilingües (EN/ES) con: datos de las partes, foto del inquilino, términos del arrendamiento, reglas del hogar, addendums (mascotas, pintura con plomo, SCRA/militar, consentimiento fotográfico). Firma digital. Checklist de mudanza con fotos."),
        ("Pagos / Cobros", "Registrar pagos manuales, ver historial, pagos recurrentes automáticos, integración Stripe, exportar reportes."),
        ("Gastos", "Registrar gastos de propiedades: mantenimiento, seguros, impuestos, utilities. Categorización y reportes."),
        ("Mantenimiento", "Ver solicitudes de mantenimiento, asignar prioridad, actualizar estado, enviar alertas a inquilinos."),
        ("Inspecciones", "Programar inspecciones de move-in/move-out. Checklist con fotos antes/después, firma digital del inquilino."),
        ("Marketplace", "Gestionar listados públicos de propiedades en venta/renta. Aprobar/rechazar listados de propietarios."),
        ("Propietarios", "Gestión de propietarios: pagos, comisiones (10%), payouts via Stripe Connect, registro de gastos por propietario."),
        ("Mercado (Mashvisor)", "Análisis de mercado inmobiliario: precios por zona, ROI, comparativas, propiedades de inversión. Filtros por tipo, precio, habitaciones."),
        ("Inversiones", "Portafolio de inversiones: tracking de propiedades compradas, gastos, fotos, rendimiento ROI."),
        ("Chat", "Centro de mensajería: ver todas las conversaciones con usuarios, responder, plantillas de mensajes predefinidas."),
        ("Mensajes", "Envío masivo de mensajes/notificaciones a inquilinos: por SMS (Twilio), email (SendGrid), push notification."),
        ("AI Brain", "Asistente AI (GPT-4o): análisis de documentos, generación de textos, consultas sobre propiedades, asistencia legal."),
        ("Calendario", "Vista calendario de: vencimientos de contratos, pagos, inspecciones, citas."),
        ("Configuración", "Configuración del negocio: datos de la empresa, logo, métodos de pago, integraciones API, plantillas de contrato."),
        ("FAQs", "Gestionar preguntas frecuentes que aparecen en la app para los usuarios."),
        ("Reportes", "Generación de reportes: ingresos, gastos, ocupación, morosidad, rendimiento por propiedad."),
        ("Rendimiento", "Análisis de rendimiento por propiedad: ingresos vs gastos, NOI, cap rate, ocupación."),
    ]
    for title, desc in admin_modules:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 10. INTEGRATIONS
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("10. INTEGRACIONES DE TERCEROS", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))

    int_data = [
        ['Servicio', 'Uso', 'Estado'],
        ['Stripe Connect', 'Pagos de renta, tarjetas, ACH, payouts a propietarios', 'Activo'],
        ['SendGrid', 'Emails transaccionales, bienvenida, recibos, alertas', 'Activo'],
        ['Twilio', 'SMS de verificación OTP, recordatorios de pago, WhatsApp', 'Activo'],
        ['Mashvisor', 'Datos de mercado inmobiliario, análisis de inversión', 'Activo'],
        ['OpenAI GPT-4o', 'AI Brain: análisis de documentos, asistencia inteligente', 'Activo'],
        ['Emergent Storage', 'Almacenamiento de fotos (propiedades, inquilinos, perfil)', 'Activo'],
        ['Expo Push', 'Notificaciones push a dispositivos iOS/Android', 'Activo'],
        ['MongoDB Atlas', 'Base de datos principal (cloud)', 'Activo'],
        ['Topaz Signature', 'Firma digital con pad de hardware (en oficina)', 'Pendiente'],
    ]
    it = Table(int_data, colWidths=[120, 260, 70])
    it.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(it)
    elements.append(PageBreak())

    # ══════════════════════════════════════════════════════════
    # 11. SECURITY
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("11. SEGURIDAD Y PROTECCIÓN DE DATOS", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))

    security_items = [
        ("Autenticación", "JWT tokens con expiración, bcrypt para hash de contraseñas, OTP por SMS para verificación."),
        ("Autorización por Rol", "Cada endpoint verifica el rol del usuario. Inquilinos solo ven sus datos, propietarios solo sus propiedades."),
        ("HTTPS Everywhere", "Todo el tráfico encriptado con TLS. Railway y Vercel proveen certificados SSL automáticos."),
        ("Fotos de Identificación", "Dos fotos separadas: foto oficial (tomada en oficina con webcam) para contratos, foto de perfil (subida por el usuario). Cláusula legal de consentimiento fotográfico incluida en el contrato."),
        ("Datos Sensibles", "Contraseñas hasheadas con bcrypt. Tokens JWT con expiración. Datos financieros procesados por Stripe (PCI compliant)."),
        ("Eliminación de Cuenta", "Los usuarios pueden solicitar eliminación de su cuenta. Los datos se eliminan con confirmación de texto."),
        ("Política de Privacidad", "Disponible en la app en español e inglés."),
    ]
    for title, desc in security_items:
        elements.append(Paragraph(f"<b>▸ {title}</b>", styles['FeatureTitle']))
        elements.append(Paragraph(desc, styles['BulletItem']))
    elements.append(Spacer(1, 12))

    # ══════════════════════════════════════════════════════════
    # 12. BUSINESS FLOWS
    # ══════════════════════════════════════════════════════════
    elements.append(Paragraph("12. FLUJOS PRINCIPALES DEL NEGOCIO", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("<b>FLUJO 1: Onboarding de Nuevo Inquilino</b>", styles['SubSection']))
    flow1 = [
        "1. Admin crea inquilino en Panel Web (/admin/inquilinos) → Toma foto con webcam",
        "2. Sistema auto-genera credenciales (email + contraseña temporal)",
        "3. Se envía email de bienvenida con credenciales via SendGrid",
        "4. Se crea registro en 'app_users' con rol 'tenant' + registro en 'tenants'",
        "5. Admin crea contrato PDF bilingüe con foto del inquilino + cláusula de consentimiento",
        "6. Inquilino firma contrato digitalmente en la app o en oficina",
        "7. Inquilino accede a la app: ve su propiedad, pagos, mantenimiento",
    ]
    for step in flow1:
        elements.append(Paragraph(step, styles['BulletItem']))

    elements.append(Paragraph("<b>FLUJO 2: Pago de Renta Mensual</b>", styles['SubSection']))
    flow2 = [
        "1. Inquilino abre la app → Dashboard muestra próximo pago con días restantes",
        "2. Click en 'Pagar Renta' → Seleccionar mes y monto",
        "3. Seleccionar método: tarjeta guardada, nueva tarjeta, o ACH",
        "4. Stripe procesa el pago → Confirmación instantánea",
        "5. Admin ve el pago en Panel Web (/admin/pagos)",
        "6. Si tiene autopago configurado, se cobra automáticamente cada mes",
        "7. Si hay mora, el sistema envía notificación push + SMS",
    ]
    for step in flow2:
        elements.append(Paragraph(step, styles['BulletItem']))

    elements.append(Paragraph("<b>FLUJO 3: Solicitud de Mantenimiento</b>", styles['SubSection']))
    flow3 = [
        "1. Inquilino va a Mantenimiento → 'Nueva Solicitud'",
        "2. Describe el problema, selecciona prioridad (urgente/normal/baja), adjunta fotos",
        "3. La solicitud llega al admin en Panel Web (/admin/mantenimiento)",
        "4. Admin actualiza estado: pendiente → en progreso → completado",
        "5. Inquilino recibe notificación push con actualizaciones",
    ]
    for step in flow3:
        elements.append(Paragraph(step, styles['BulletItem']))

    elements.append(Paragraph("<b>FLUJO 4: Publicación de Propiedad (Propietario)</b>", styles['SubSection']))
    flow4 = [
        "1. Propietario se registra en la app como 'Landlord'",
        "2. Va a 'Agregar Propiedad' → Completa formulario (dirección, precio, tipo, fotos)",
        "3. Admin revisa y aprueba el listado en Panel Web (/admin/marketplace)",
        "4. Propiedad aparece visible para Invitados y Compradores en la app",
        "5. Usuarios interesados envían consultas → Admin y Propietario las ven",
    ]
    for step in flow4:
        elements.append(Paragraph(step, styles['BulletItem']))

    elements.append(Paragraph("<b>FLUJO 5: Inspección de Move-In / Move-Out</b>", styles['SubSection']))
    flow5 = [
        "1. Admin programa inspección en Panel Web (/admin/inspecciones)",
        "2. El día de la inspección, Admin usa checklist con fotos (estado de pisos, paredes, etc.)",
        "3. Inquilino firma el checklist digitalmente",
        "4. En move-out, se comparan fotos antes/después para evaluar daños",
        "5. Se genera reporte de inspección para depósito de seguridad",
    ]
    for step in flow5:
        elements.append(Paragraph(step, styles['BulletItem']))

    elements.append(Spacer(1, 20))
    elements.append(HRFlowable(width="100%", thickness=2, color=BRAND_RED))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(
        "Documento generado automáticamente por Ross House Rentals LLC — "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}",
        styles['Footer']
    ))
    elements.append(Paragraph("Confidencial — Solo para uso interno y análisis", styles['Footer']))

    doc.build(elements)
    return buffer.getvalue()


if __name__ == '__main__':
    pdf_bytes = generate_report()
    # Save locally
    with open('/tmp/ross_house_report.pdf', 'wb') as f:
        f.write(pdf_bytes)
    print(f"✅ PDF generated: {len(pdf_bytes)} bytes")
    print(f"📄 Saved to /tmp/ross_house_report.pdf")
