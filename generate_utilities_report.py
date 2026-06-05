"""
Generate comprehensive PDF: Utility Integrations & Bill Payment Center Research
for Ross House Rentals LLC — Dumas, TX
"""
import io
import base64
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# Colors
BRAND_RED = HexColor('#C8102E')
NAVY = HexColor('#16213e')
BLUE = HexColor('#3B82F6')
GREEN = HexColor('#10B981')
PURPLE = HexColor('#8B5CF6')
GOLD = HexColor('#F59E0B')
ORANGE = HexColor('#F97316')
GRAY = HexColor('#6B7280')
LIGHT_BG = HexColor('#F3F4F6')
DARK_TEXT = HexColor('#1F2937')
MED_TEXT = HexColor('#374151')
BORDER = HexColor('#D1D5DB')
LIGHT_GREEN = HexColor('#D1FAE5')
LIGHT_RED = HexColor('#FEE2E2')
LIGHT_YELLOW = HexColor('#FEF3C7')
LIGHT_BLUE = HexColor('#DBEAFE')
LIGHT_PURPLE = HexColor('#EDE9FE')


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('CoverTitle', fontName='Helvetica-Bold', fontSize=26, textColor=BRAND_RED, alignment=TA_CENTER, spaceAfter=6))
    styles.add(ParagraphStyle('CoverSub', fontName='Helvetica', fontSize=13, textColor=GRAY, alignment=TA_CENTER, spaceAfter=4))
    styles.add(ParagraphStyle('SectionTitle', fontName='Helvetica-Bold', fontSize=17, textColor=BRAND_RED, spaceBefore=16, spaceAfter=8))
    styles.add(ParagraphStyle('SubSection', fontName='Helvetica-Bold', fontSize=13, textColor=NAVY, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle('SubSection2', fontName='Helvetica-Bold', fontSize=11, textColor=BLUE, spaceBefore=10, spaceAfter=4))
    styles.add(ParagraphStyle('FeatureTitle', fontName='Helvetica-Bold', fontSize=10.5, textColor=DARK_TEXT, spaceBefore=6, spaceAfter=3))
    styles.add(ParagraphStyle('Body', fontName='Helvetica', fontSize=10, textColor=MED_TEXT, spaceAfter=4, leading=14))
    styles.add(ParagraphStyle('BodyBold', fontName='Helvetica-Bold', fontSize=10, textColor=DARK_TEXT, spaceAfter=4, leading=14))
    styles.add(ParagraphStyle('BulletPt', fontName='Helvetica', fontSize=9.5, textColor=MED_TEXT, leftIndent=18, spaceAfter=3, leading=13))
    styles.add(ParagraphStyle('SmallNote', fontName='Helvetica-Oblique', fontSize=8.5, textColor=GRAY, leftIndent=18, spaceAfter=2, leading=12))
    styles.add(ParagraphStyle('Footer', fontName='Helvetica', fontSize=8, textColor=GRAY, alignment=TA_CENTER))
    styles.add(ParagraphStyle('Highlight', fontName='Helvetica-Bold', fontSize=10, textColor=BRAND_RED, spaceAfter=4, leading=14))
    return styles


def generate_report():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=0.5*inch, bottomMargin=0.5*inch,
                            leftMargin=0.7*inch, rightMargin=0.7*inch)
    S = build_styles()
    E = []

    # ══════════════════════════════════════════════════════
    # COVER
    # ══════════════════════════════════════════════════════
    E.append(Spacer(1, 1.2*inch))
    E.append(Paragraph("ROSS HOUSE RENTALS LLC", S['CoverTitle']))
    E.append(Spacer(1, 6))
    E.append(Paragraph("Investigación de Integraciones de Servicios Públicos", S['CoverSub']))
    E.append(Paragraph("& Modelo de Negocio: Centro de Pagos de Bills", S['CoverSub']))
    E.append(Spacer(1, 24))
    E.append(HRFlowable(width="50%", thickness=2, color=BRAND_RED, hAlign='CENTER'))
    E.append(Spacer(1, 16))

    cover_data = [
        ['Ubicación', 'Dumas, TX (Moore County) — Texas Panhandle'],
        ['Alcance', 'Electricidad, Gas, Agua, Internet, Basura'],
        ['Enfoque', 'APIs de datos + Módulo de pagos de bills + Comisiones'],
        ['Fecha', datetime.now().strftime('%d de Junio, %Y')],
        ['Confidencial', 'Solo para uso interno — Ross House Rentals LLC'],
    ]
    ct = Table(cover_data, colWidths=[110, 350])
    ct.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9.5),
        ('TEXTCOLOR', (0, 0), (0, -1), BRAND_RED),
        ('TEXTCOLOR', (1, 0), (1, -1), MED_TEXT),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, BORDER),
    ]))
    E.append(ct)
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # TABLE OF CONTENTS
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("ÍNDICE DE CONTENIDOS", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 10))
    toc = [
        "PARTE 1: PROVEEDORES DE SERVICIOS EN DUMAS, TX",
        "  1.1  Electricidad — Xcel Energy (SPS)",
        "  1.2  Gas Natural — Atmos Energy",
        "  1.3  Agua — Ciudad de Dumas (Municipal)",
        "  1.4  Internet — Múltiples Proveedores",
        "  1.5  Basura — Servicio Municipal",
        "",
        "PARTE 2: DISPONIBILIDAD DE APIs PARA MÉTRICAS",
        "  2.1  Tabla Resumen de APIs",
        "  2.2  Xcel Energy — Green Button Connect My Data",
        "  2.3  Atmos Energy — API Limitada (AMI)",
        "  2.4  Agua / Internet / Basura — Sin API",
        "",
        "PARTE 3: MODELO DE NEGOCIO — CENTRO DE PAGOS DE BILLS",
        "  3.1  Concepto del Servicio",
        "  3.2  Redes de Pago Disponibles (CheckFreePay, PayNearMe, KUBRA)",
        "  3.3  Pagos Directos a Proveedores (Xcel, Atmos)",
        "  3.4  Modelo de Comisiones y Revenue",
        "  3.5  Requisitos Legales (FinCEN / Money Transmitter)",
        "",
        "PARTE 4: MÓDULO PROPUESTO PARA EL ADMIN PANEL",
        "  4.1  Diseño del Módulo 'Pagos de Servicios'",
        "  4.2  Flujo del Cliente en la Oficina",
        "  4.3  Flujo del Inquilino desde la App",
        "",
        "PARTE 5: PLAN DE IMPLEMENTACIÓN EN 3 FASES",
        "",
        "PARTE 6: ANÁLISIS FINANCIERO ESTIMADO",
    ]
    for item in toc:
        if item == "":
            E.append(Spacer(1, 4))
        elif item.startswith("PARTE"):
            E.append(Paragraph(f"<b>{item}</b>", S['Body']))
        else:
            E.append(Paragraph(item, S['BulletPt']))
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # PART 1: PROVIDERS
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("PARTE 1: PROVEEDORES DE SERVICIOS EN DUMAS, TX", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    # 1.1 Xcel
    E.append(Paragraph("1.1 ⚡ ELECTRICIDAD — Xcel Energy (Southwestern Public Service)", S['SubSection']))
    xcel_data = [
        ['Campo', 'Detalle'],
        ['Proveedor', 'Xcel Energy / Southwestern Public Service Company (SPS)'],
        ['Cobertura', 'Todo el Texas Panhandle incluyendo Dumas, Amarillo, Lubbock'],
        ['Servicios', 'Electricidad residencial y comercial'],
        ['Facturación', 'Portal online, app móvil, correo, teléfono'],
        ['Pago en Persona', 'Pay Stations autorizadas — $1.50 por transacción'],
        ['API Disponible', '✅ SÍ — Green Button Connect My Data (OAuth 2.0 + ESPI)'],
        ['Datos Accesibles', 'Consumo kWh, historial 24 meses, datos de facturación'],
        ['Requisito API', 'Ser aprobado como Green Button Service Provider'],
        ['Smart Meters', 'Itron Gen 5 Riva (IEEE 2030.5) — en despliegue'],
        ['Web', 'xcelenergy.com'],
        ['Teléfono', '1-800-895-4999'],
    ]
    t = Table(xcel_data, colWidths=[120, 350])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Spacer(1, 10))

    # 1.2 Atmos
    E.append(Paragraph("1.2 🔥 GAS NATURAL — Atmos Energy", S['SubSection']))
    atmos_data = [
        ['Campo', 'Detalle'],
        ['Proveedor', 'Atmos Energy Corporation'],
        ['Cobertura', 'Dumas, TX y más de 1,400 comunidades en TX'],
        ['Servicios', 'Gas natural residencial y comercial'],
        ['Facturación', 'Portal online (Account Center), correo, teléfono'],
        ['Pago en Persona', '2,000+ centros autorizados + Walmart'],
        ['API Disponible', '⚠️ LIMITADA — Solo en áreas con medidores AMI/wireless'],
        ['Datos Accesibles', 'Consumo therms (si tiene AMI), facturación'],
        ['Requisito API', 'Llamar a atención al cliente para verificar elegibilidad'],
        ['Web', 'atmosenergy.com'],
        ['Teléfono', '1-888-286-6700'],
    ]
    t = Table(atmos_data, colWidths=[120, 350])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Spacer(1, 10))

    # 1.3 Agua
    E.append(Paragraph("1.3 💧 AGUA — Ciudad de Dumas (Municipal)", S['SubSection']))
    water_data = [
        ['Campo', 'Detalle'],
        ['Proveedor', 'Ciudad de Dumas — Servicios Municipales'],
        ['Cobertura', 'Dumas, TX (ciudad y alrededores)'],
        ['Servicios', 'Agua potable, alcantarillado'],
        ['Facturación', 'Portal Municipal Online (dumastx.municipalonlinepayments.com)'],
        ['Pago en Persona', 'Oficina del City Hall de Dumas'],
        ['API Disponible', '❌ NO — Solo portal de pagos web, sin API pública'],
        ['Datos Accesibles', 'Ninguno vía API; solo consulta manual en portal'],
        ['Web', 'dumastx.municipalonlinepayments.com/dumastx/utilities'],
    ]
    t = Table(water_data, colWidths=[120, 350])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Spacer(1, 10))

    # 1.4 Internet
    E.append(Paragraph("1.4 🌐 INTERNET — Múltiples Proveedores", S['SubSection']))
    inet_data = [
        ['Proveedor', 'Tipo', 'Velocidad Max', 'Cobertura'],
        ['Kinetic (Windstream)', 'Fibra / DSL', '2,000 Mbps', '~74%'],
        ['Sparklight', 'Cable', '1,000 Mbps', 'Parcial'],
        ['Plains Internet', 'Fixed Wireless', '1,000 Mbps', '~99.9%'],
        ['AT&T Internet Air', '5G Home', 'Variable', 'Parcial'],
        ['Verizon Home', 'Fixed Wireless', 'Variable', '~97%'],
        ['XNET WiFi', 'Wireless', '2 Gbps', 'Parcial'],
        ['Starlink', 'Satélite', '~200 Mbps', '100%'],
        ['Viasat / HughesNet', 'Satélite', '~100 Mbps', '100%'],
    ]
    t = Table(inet_data, colWidths=[130, 100, 100, 100])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
    ]))
    E.append(t)
    E.append(Paragraph("Nota: Ningún proveedor de internet ofrece API de consumo para terceros.", S['SmallNote']))
    E.append(Spacer(1, 10))

    # 1.5 Basura
    E.append(Paragraph("1.5 🗑️ BASURA — Servicio Municipal / WM", S['SubSection']))
    E.append(Paragraph("No se confirmó el proveedor exacto de recolección de basura en Dumas. Posiblemente es servicio municipal "
        "o contratado con Waste Management (WM). No existe API pública para este servicio.", S['Body']))
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # PART 2: API AVAILABILITY
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("PARTE 2: DISPONIBILIDAD DE APIs PARA MÉTRICAS", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    E.append(Paragraph("2.1 Tabla Resumen de APIs", S['SubSection']))
    api_data = [
        ['Servicio', 'Proveedor', 'API', 'Protocolo', 'Datos', 'Dificultad'],
        ['⚡ Electricidad', 'Xcel Energy', '✅ SÍ', 'Green Button\nOAuth 2.0', 'kWh, costos,\n24 meses hist.', '⭐⭐⭐\nMedia'],
        ['🔥 Gas', 'Atmos Energy', '⚠️ Limitada', 'API privada\n(si hay AMI)', 'Therms,\nfacturación', '⭐⭐⭐⭐\nAlta'],
        ['💧 Agua', 'Ciudad Dumas', '❌ NO', 'N/A', 'N/A', 'N/A'],
        ['🌐 Internet', 'Varios', '❌ NO', 'N/A', 'N/A', 'N/A'],
        ['🗑️ Basura', 'Municipal/WM', '❌ NO', 'N/A', 'N/A', 'N/A'],
    ]
    t = Table(api_data, colWidths=[75, 75, 55, 75, 80, 65])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_RED), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, 1), LIGHT_GREEN),
        ('BACKGROUND', (0, 2), (-1, 2), LIGHT_YELLOW),
        ('BACKGROUND', (0, 3), (-1, 5), LIGHT_RED),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ]))
    E.append(t)
    E.append(Spacer(1, 12))

    # 2.2 Xcel Green Button
    E.append(Paragraph("2.2 Xcel Energy — Green Button Connect My Data (Detalle)", S['SubSection']))
    E.append(Paragraph("<b>¿Qué es Green Button?</b>", S['FeatureTitle']))
    E.append(Paragraph("Green Button es una iniciativa del Departamento de Energía de EE.UU. que estandariza "
        "el acceso a datos de consumo energético. Xcel Energy participa en este programa permitiendo que "
        "aplicaciones autorizadas accedan a datos de electricidad y gas de sus clientes con consentimiento.", S['Body']))

    E.append(Paragraph("<b>Proceso de Integración:</b>", S['FeatureTitle']))
    steps = [
        "1. Ross House aplica como Green Button Service Provider (formulario en xcelenergy.com)",
        "2. Xcel revisa la aplicación (uso de datos, seguridad, propósito)",
        "3. Una vez aprobado, se reciben credenciales OAuth 2.0",
        "4. Se implementa el flujo: inquilino autoriza → se descargan datos en formato ESPI/XML",
        "5. Los datos incluyen: consumo kWh por período, costos, historial de 24 meses",
    ]
    for s in steps:
        E.append(Paragraph(s, S['BulletPt']))

    E.append(Paragraph("<b>Datos que obtendríamos:</b>", S['FeatureTitle']))
    gbd = [
        "• Consumo de electricidad en kWh (por mes o por intervalo según el medidor)",
        "• Costo facturado por período",
        "• Historial de hasta 24 meses",
        "• Datos de la cuenta (dirección de servicio, tipo de tarifa)",
    ]
    for g in gbd:
        E.append(Paragraph(g, S['BulletPt']))

    E.append(Paragraph("<b>⚠️ Nota Importante:</b> UtilityAPI (middleware popular) eliminó a Xcel Energy de su plataforma "
        "en septiembre 2025. La integración debe ser DIRECTA con Xcel.", S['Highlight']))
    E.append(Spacer(1, 8))

    # 2.3 Atmos
    E.append(Paragraph("2.3 Atmos Energy — API Limitada", S['SubSection']))
    E.append(Paragraph("Atmos Energy tiene una API pero su disponibilidad depende de si la dirección tiene un medidor "
        "AMI (Advanced Metering Infrastructure) con lectura inalámbrica. Un representante de Atmos confirmó "
        "que la API existe pero 'solo en ciertas áreas'.", S['Body']))
    E.append(Paragraph("<b>Acción requerida:</b> Llamar a Atmos al 1-888-286-6700 y preguntar específicamente: "
        "'¿Las direcciones en Dumas, TX tienen medidores AMI? ¿Ofrecen acceso API a datos de consumo para "
        "aplicaciones autorizadas de property management?'", S['BodyBold']))
    E.append(Spacer(1, 6))

    # 2.4 Sin API
    E.append(Paragraph("2.4 Agua / Internet / Basura — Sin API Disponible", S['SubSection']))
    E.append(Paragraph("Estos servicios no ofrecen APIs públicas. Para agua, la Ciudad de Dumas solo tiene un portal "
        "de pagos web sin capacidad de integración programática. La alternativa es usar nuestro "
        "AI (GPT-4o) para extraer datos de facturas escaneadas/fotografiadas por los inquilinos.", S['Body']))
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # PART 3: BILL PAYMENT CENTER
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("PARTE 3: MODELO DE NEGOCIO — CENTRO DE PAGOS DE BILLS", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    E.append(Paragraph("3.1 Concepto del Servicio", S['SubSection']))
    E.append(Paragraph("La oficina de Ross House Rentals se convierte en un <b>Centro Autorizado de Pagos de Servicios</b>. "
        "Los clientes (inquilinos y público general) pueden venir a la oficina a pagar sus facturas de "
        "electricidad, gas, agua, teléfono, y otros servicios. Ross House cobra una tarifa de conveniencia "
        "y/o recibe comisión del proveedor de pagos.", S['Body']))

    E.append(Paragraph("<b>Beneficios del modelo:</b>", S['FeatureTitle']))
    benefits = [
        "• Genera ingresos adicionales por cada transacción procesada",
        "• Aumenta el tráfico de personas a la oficina (potenciales inquilinos/compradores)",
        "• Fideliza a los inquilinos actuales (todo en un solo lugar)",
        "• Diferenciador vs otras compañías de property management",
        "• Servicio muy valorado en comunidades rurales como Dumas donde opciones son limitadas",
    ]
    for b in benefits:
        E.append(Paragraph(b, S['BulletPt']))
    E.append(Spacer(1, 8))

    # 3.2 Payment Networks
    E.append(Paragraph("3.2 Redes de Pago Disponibles", S['SubSection']))
    E.append(Spacer(1, 4))

    # CheckFreePay
    E.append(Paragraph("A) CheckFreePay (Fiserv)", S['SubSection2']))
    cfp_data = [
        ['Campo', 'Detalle'],
        ['Empresa', 'CheckFreePay — División de Fiserv (la más grande de EE.UU.)'],
        ['Red', 'La red más grande de pagos walk-in en EE.UU.'],
        ['Billers', 'Miles de compañías de servicios (electricidad, gas, agua, teléfono, cable, etc.)'],
        ['Comisión', '✅ SÍ — Comisión por cada transacción (varía por biller y tipo de pago)'],
        ['Cómo Aplicar', 'Llenar Agent Request Form en checkfreepay.com/en/agents.html'],
        ['Requisitos', 'Computadora, impresora de recibos, internet de alta velocidad'],
        ['Compliance', 'Capacitación AML/BSA incluida por CheckFreePay'],
        ['Ventaja', 'Acceso inmediato a cientos de billers sin contratos individuales'],
        ['Web', 'checkfreepay.com/en/agents.html'],
    ]
    t = Table(cfp_data, colWidths=[100, 370])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), BLUE), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BLUE),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Paragraph("⭐ RECOMENDACIÓN: Esta es la mejor opción para empezar. Mayor red, fácil aplicación, comisión por transacción.", S['Highlight']))
    E.append(Spacer(1, 8))

    # PayNearMe
    E.append(Paragraph("B) PayNearMe", S['SubSection2']))
    pnm_data = [
        ['Campo', 'Detalle'],
        ['Empresa', 'PayNearMe — Plataforma de pagos en efectivo'],
        ['Red', 'Tiendas participantes (7-Eleven, CVS, etc.)'],
        ['Modelo', 'Código de barras personalizado para pago en tienda'],
        ['Comisión', '⚠️ No pública — negociada por partner'],
        ['Aplicación', 'Contactar equipo de ventas de PayNearMe'],
        ['Mejor Para', 'Grandes volúmenes, cadenas de retail'],
        ['Web', 'paynearme.com'],
    ]
    t = Table(pnm_data, colWidths=[100, 370])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), PURPLE), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_PURPLE),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Spacer(1, 8))

    # KUBRA
    E.append(Paragraph("C) KUBRA EZ-PAY", S['SubSection2']))
    kubra_data = [
        ['Campo', 'Detalle'],
        ['Empresa', 'KUBRA — Plataforma de pagos para utilities'],
        ['Red', '67,000+ ubicaciones retail (CVS, Dollar Tree, 7-Eleven)'],
        ['Modelo', 'Retail Cash Payment Network con código de barras'],
        ['Comisión', '⚠️ Negociada — a través de partner de pagos'],
        ['Aplicación', 'Contactar KUBRA Payments team directamente'],
        ['Mejor Para', 'Utilities y billers grandes'],
        ['Web', 'kubra.com/kubra-payments'],
    ]
    t = Table(kubra_data, colWidths=[100, 370])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), GREEN), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GREEN),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(PageBreak())

    # 3.3 Direct payments
    E.append(Paragraph("3.3 Pagos Directos a Proveedores Locales", S['SubSection']))
    direct_data = [
        ['Proveedor', 'Pago en Persona', 'Fee', 'Comisión al Agente'],
        ['Xcel Energy', '✅ Pay Stations autorizadas', '$1.50/transacción', 'Negociable — contactar Xcel'],
        ['Atmos Energy', '✅ 2,000+ centros + Walmart', 'Variable', 'Centros son independientes'],
        ['Ciudad de Dumas', '✅ City Hall', 'Gratis', 'No aplica (es municipal)'],
    ]
    t = Table(direct_data, colWidths=[100, 150, 100, 130])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
    ]))
    E.append(t)
    E.append(Spacer(1, 10))

    # 3.4 Revenue Model
    E.append(Paragraph("3.4 Modelo de Comisiones y Revenue", S['SubSection']))
    E.append(Paragraph("Existen <b>3 fuentes de ingreso</b> principales como Centro de Pagos:", S['Body']))
    E.append(Spacer(1, 4))

    rev_data = [
        ['Fuente de Ingreso', 'Descripción', 'Estimado por Transacción'],
        ['Comisión del\nPayment Network', 'CheckFreePay/KUBRA paga al agente\npor cada pago procesado', '$0.50 — $2.00\n(varía por biller)'],
        ['Convenience Fee\nal Cliente', 'Cargo al cliente por el servicio\nde pago en persona', '$1.50 — $4.99\n(estándar en industria)'],
        ['Cross-Sell /\nTráfico Adicional', 'Clientes que vienen a pagar bills\npueden interesarse en rentar o\ncomprar propiedades', 'Valor indirecto\n(leads de negocio)'],
    ]
    t = Table(rev_data, colWidths=[110, 200, 130])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), GOLD), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_YELLOW),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Spacer(1, 10))

    # 3.5 Legal
    E.append(Paragraph("3.5 Requisitos Legales (FinCEN / Money Transmitter)", S['SubSection']))
    E.append(Paragraph("<b>Buena noticia:</b> Según FinCEN (Financial Crimes Enforcement Network), un negocio que acepta "
        "pagos de facturas como <b>agente autorizado</b> de la compañía de servicios públicos "
        "<b>NO se considera Money Transmitter</b>, siempre y cuando:", S['Body']))
    legal_items = [
        "• Solo acepte y reenvíe fondos para las utilities contratadas",
        "• No ofrezca servicios generales de transferencia de dinero",
        "• Opere bajo contrato con el payment network (ej: CheckFreePay)",
        "• Cumpla con los requisitos de AML/BSA (Anti-Money Laundering)",
        "• Mantenga registros de transacciones según lo requerido",
    ]
    for l in legal_items:
        E.append(Paragraph(l, S['BulletPt']))
    E.append(Paragraph("Esto significa que Ross House NO necesita licencia de Money Transmitter para operar como "
        "centro de pagos si actúa como agente autorizado de CheckFreePay u otra red.", S['Highlight']))
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # PART 4: ADMIN MODULE
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("PARTE 4: MÓDULO PROPUESTO PARA EL ADMIN PANEL", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    E.append(Paragraph("4.1 Diseño del Módulo 'Pagos de Servicios'", S['SubSection']))
    E.append(Paragraph("Nuevo módulo en el Panel Admin Web (/admin/pagos-servicios) con las siguientes funciones:", S['Body']))

    module_features = [
        ("Dashboard de Pagos", "Vista general: total transacciones hoy/semana/mes, comisiones ganadas, "
            "proveedores más utilizados, gráfica de tendencia."),
        ("Registrar Pago Manual", "El admin selecciona: proveedor (Xcel, Atmos, Agua, etc.), "
            "ingresa número de cuenta del cliente, monto, método de pago recibido (efectivo, cheque, "
            "tarjeta). Se genera recibo imprimible."),
        ("Integración CheckFreePay", "Si se aprueba como agente, los pagos se procesan directamente "
            "a través de la red de CheckFreePay con confirmación en tiempo real."),
        ("Historial de Transacciones", "Registro completo: fecha, cliente, proveedor, monto, "
            "comisión ganada, estado, recibo."),
        ("Recibos Imprimibles", "Generar recibo PDF profesional con logo de Ross House, "
            "datos del pago, número de confirmación."),
        ("Reporte de Comisiones", "Reporte mensual de todas las comisiones ganadas, desglosado por proveedor."),
        ("Perfil de Servicios del Inquilino", "Ver qué servicios tiene cada inquilino, "
            "historial de pagos por servicio, alertas de facturas vencidas."),
    ]
    for title, desc in module_features:
        E.append(Paragraph(f"<b>▸ {title}</b>", S['FeatureTitle']))
        E.append(Paragraph(desc, S['BulletPt']))
    E.append(Spacer(1, 10))

    # 4.2 Office flow
    E.append(Paragraph("4.2 Flujo del Cliente en la Oficina", S['SubSection']))
    office_flow = [
        "1. Ramón llega a la oficina de Ross House con su factura de Xcel Energy",
        "2. La recepcionista abre el módulo 'Pagos de Servicios' en el Admin Panel",
        "3. Selecciona 'Xcel Energy' como proveedor",
        "4. Escanea el código de barras de la factura O ingresa el número de cuenta manualmente",
        "5. Ingresa el monto a pagar y el método de pago (efectivo, cheque, tarjeta)",
        "6. El sistema procesa el pago a través de CheckFreePay",
        "7. Se genera e imprime un recibo para Ramón",
        "8. Ross House gana comisión + fee de conveniencia",
        "9. El pago queda registrado en el historial del sistema",
    ]
    for f in office_flow:
        E.append(Paragraph(f, S['BulletPt']))
    E.append(Spacer(1, 10))

    # 4.3 App flow
    E.append(Paragraph("4.3 Flujo del Inquilino desde la App (Fase Futura)", S['SubSection']))
    app_flow = [
        "1. El inquilino abre la app → va a la nueva sección 'Mis Servicios'",
        "2. Ve su consumo de electricidad (vía Xcel Green Button API)",
        "3. Puede subir foto de cualquier factura → AI extrae datos automáticamente",
        "4. Dashboard muestra: gráfica de consumo mensual, alertas de facturas altas",
        "5. Opcionalmente: puede pagar su factura de Xcel directamente desde la app (si se integra)",
        "6. El admin ve todos los datos de servicios de todos los inquilinos en el Admin Panel",
    ]
    for f in app_flow:
        E.append(Paragraph(f, S['BulletPt']))
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # PART 5: IMPLEMENTATION PLAN
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("PARTE 5: PLAN DE IMPLEMENTACIÓN EN 3 FASES", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    phases = [
        ['Fase', 'Tiempo', 'Acción', 'Resultado'],
        ['FASE 1\nInmediata', '1-2\nsemanas', '• Aplicar a CheckFreePay como agente\n'
            '• Construir módulo "Pagos de Servicios" en Admin Panel\n'
            '• Implementar registro manual de pagos + recibos\n'
            '• Construir "Utility Tracker" en app (subir foto de factura + AI)',
            '• Oficina puede recibir pagos de bills\n'
            '• Inquilinos pueden trackear servicios\n'
            '• Revenue adicional inmediato'],
        ['FASE 2\n1-2 meses', '1-2\nmeses', '• Aplicar a Xcel Energy Green Button Program\n'
            '• Llamar a Atmos para verificar API en Dumas\n'
            '• Integrar datos de Xcel (consumo automático)\n'
            '• Dashboard de energía en la app del inquilino',
            '• Datos de electricidad automáticos\n'
            '• Gráficas de consumo sin esfuerzo\n'
            '• Alertas inteligentes de consumo'],
        ['FASE 3\n3-6 meses', '3-6\nmeses', '• Integrar Atmos (si API disponible)\n'
            '• Benchmarking entre propiedades\n'
            '• Pago de bills desde la app (si CheckFreePay lo permite)\n'
            '• Reportes de eficiencia energética',
            '• Plataforma completa de utilities\n'
            '• Decisiones informadas de inversión\n'
            '• Diferenciador total en el mercado'],
    ]
    t = Table(phases, colWidths=[65, 45, 210, 160])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_RED), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, 1), LIGHT_GREEN),
        ('BACKGROUND', (0, 2), (-1, 2), LIGHT_BLUE),
        ('BACKGROUND', (0, 3), (-1, 3), LIGHT_PURPLE),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
    ]))
    E.append(t)
    E.append(PageBreak())

    # ══════════════════════════════════════════════════════
    # PART 6: FINANCIAL ANALYSIS
    # ══════════════════════════════════════════════════════
    E.append(Paragraph("PARTE 6: ANÁLISIS FINANCIERO ESTIMADO", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    E.append(Paragraph("Estimación conservadora basada en la comunidad de Dumas, TX (~15,000 habitantes):", S['Body']))
    E.append(Spacer(1, 6))

    E.append(Paragraph("Escenario: Centro de Pagos de Bills", S['SubSection']))
    fin_data = [
        ['Métrica', 'Conservador', 'Moderado', 'Optimista'],
        ['Transacciones/día', '10', '25', '50'],
        ['Transacciones/mes', '220', '550', '1,100'],
        ['Comisión promedio/tx', '$1.00', '$1.25', '$1.50'],
        ['Fee conveniencia/tx', '$1.50', '$2.00', '$2.50'],
        ['Ingreso por comisión/mes', '$220', '$688', '$1,650'],
        ['Ingreso por fees/mes', '$330', '$1,100', '$2,750'],
        ['TOTAL MENSUAL', '$550', '$1,788', '$4,400'],
        ['TOTAL ANUAL', '$6,600', '$21,450', '$52,800'],
    ]
    t = Table(fin_data, colWidths=[140, 100, 100, 100])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, -2), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -2), (-1, -1), LIGHT_GREEN),
        ('BACKGROUND', (0, 1), (-1, -3), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    E.append(t)
    E.append(Spacer(1, 8))
    E.append(Paragraph("Nota: Los montos de comisión son estimados. CheckFreePay proveerá las tasas exactas "
        "durante el proceso de aplicación. Los fees de conveniencia son configurables por Ross House.", S['SmallNote']))
    E.append(Spacer(1, 12))

    E.append(Paragraph("Costos de Implementación", S['SubSection']))
    cost_data = [
        ['Concepto', 'Costo'],
        ['Aplicación CheckFreePay', 'Gratis'],
        ['Computadora + Impresora de recibos', 'Ya existente en oficina'],
        ['Internet de alta velocidad', 'Ya existente en oficina'],
        ['Desarrollo módulo Admin Panel', 'Incluido en plataforma actual'],
        ['Desarrollo Utility Tracker (app)', 'Incluido en plataforma actual'],
        ['Aplicación Green Button (Xcel)', 'Gratis'],
        ['TOTAL INVERSIÓN ADICIONAL', '$0 (todo ya existe o es gratis)'],
    ]
    t = Table(cost_data, colWidths=[250, 200])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), LIGHT_GREEN),
        ('BACKGROUND', (0, 1), (-1, -2), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    E.append(t)
    E.append(Spacer(1, 16))

    # ═══ NEXT STEPS ═══
    E.append(Paragraph("PRÓXIMOS PASOS RECOMENDADOS", S['SectionTitle']))
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    E.append(Spacer(1, 8))

    next_steps = [
        ("🔴 URGENTE", "Aplicar a CheckFreePay como agente (checkfreepay.com/en/agents.html)"),
        ("🔴 URGENTE", "Aplicar a Xcel Energy Green Button Program"),
        ("🟡 ESTA SEMANA", "Llamar a Atmos Energy (888-286-6700) para preguntar sobre API en Dumas"),
        ("🟡 ESTA SEMANA", "Llamar a Ciudad de Dumas para preguntar sobre datos de agua municipal"),
        ("🟢 PRÓXIMAS 2 SEM", "Construir módulo 'Pagos de Servicios' en Admin Panel"),
        ("🟢 PRÓXIMAS 2 SEM", "Construir 'Utility Tracker' en app (foto de factura + AI)"),
        ("🔵 MES 2", "Integrar Xcel Green Button API (una vez aprobados)"),
        ("🔵 MES 3+", "Integrar Atmos API (si disponible en Dumas)"),
    ]
    for priority, step in next_steps:
        E.append(Paragraph(f"<b>{priority}:</b> {step}", S['Body']))

    E.append(Spacer(1, 20))
    E.append(HRFlowable(width="100%", thickness=2, color=BRAND_RED))
    E.append(Spacer(1, 10))
    E.append(Paragraph(f"Documento generado automáticamente — {datetime.now().strftime('%d/%m/%Y %H:%M')}", S['Footer']))
    E.append(Paragraph("Ross House Rentals LLC — Confidencial — Solo para uso interno", S['Footer']))

    doc.build(E)
    return buffer.getvalue()


if __name__ == '__main__':
    pdf_bytes = generate_report()
    with open('/tmp/ross_house_utilities_research.pdf', 'wb') as f:
        f.write(pdf_bytes)
    print(f"✅ PDF generated: {len(pdf_bytes)} bytes ({len(pdf_bytes)//1024}KB)")
