"""
Generate comprehensive PDF: Texas Real Estate Licensing Guide & Execution Plan
For Ross House Rentals LLC — Yoandy Ross
Includes: TREC Sales Agent vs Broker vs REALTOR comparison, Spanish schools,
house flipping rules, marketplace plan, and phased execution.
"""
import io
import os
import base64
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, ListFlowable, ListItem, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

# ─── Brand Colors ────────────────────────────────────────────────
BRAND_RED = HexColor('#C8102E')
DARK_BG = HexColor('#1a1a2e')
NAVY = HexColor('#16213e')
BLUE = HexColor('#3B82F6')
GREEN = HexColor('#10B981')
PURPLE = HexColor('#8B5CF6')
GOLD = HexColor('#F59E0B')
ORANGE = HexColor('#F97316')
GRAY = HexColor('#6B7280')
LIGHT_BG = HexColor('#F3F4F6')
LIGHT_BLUE = HexColor('#EFF6FF')
LIGHT_GREEN = HexColor('#ECFDF5')
LIGHT_RED = HexColor('#FEF2F2')
WHITE = white


def build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        'CoverTitle', fontName='Helvetica-Bold', fontSize=26,
        textColor=BRAND_RED, alignment=TA_CENTER, spaceAfter=8
    ))
    styles.add(ParagraphStyle(
        'CoverSub', fontName='Helvetica', fontSize=14,
        textColor=GRAY, alignment=TA_CENTER, spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        'CoverSubBold', fontName='Helvetica-Bold', fontSize=16,
        textColor=NAVY, alignment=TA_CENTER, spaceAfter=6
    ))
    styles.add(ParagraphStyle(
        'SectionTitle', fontName='Helvetica-Bold', fontSize=18,
        textColor=BRAND_RED, spaceBefore=20, spaceAfter=10
    ))
    styles.add(ParagraphStyle(
        'SubSection', fontName='Helvetica-Bold', fontSize=13,
        textColor=NAVY, spaceBefore=14, spaceAfter=6
    ))
    styles.add(ParagraphStyle(
        'SubSection2', fontName='Helvetica-Bold', fontSize=11,
        textColor=PURPLE, spaceBefore=10, spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        'Body', fontName='Helvetica', fontSize=10,
        textColor=HexColor('#374151'), spaceAfter=4, leading=14,
        alignment=TA_JUSTIFY
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
        'BulletBold', fontName='Helvetica-Bold', fontSize=10,
        textColor=HexColor('#1F2937'), leftIndent=20, spaceAfter=3, leading=14,
        bulletIndent=10
    ))
    styles.add(ParagraphStyle(
        'NumberedItem', fontName='Helvetica', fontSize=10,
        textColor=HexColor('#374151'), leftIndent=30, spaceAfter=4, leading=14,
        bulletIndent=15
    ))
    styles.add(ParagraphStyle(
        'LinkStyle', fontName='Helvetica', fontSize=10,
        textColor=BLUE, spaceAfter=3, leading=14, leftIndent=20
    ))
    styles.add(ParagraphStyle(
        'TableHeader', fontName='Helvetica-Bold', fontSize=9,
        textColor=white, alignment=TA_CENTER, leading=12
    ))
    styles.add(ParagraphStyle(
        'TableCell', fontName='Helvetica', fontSize=9,
        textColor=HexColor('#374151'), alignment=TA_LEFT, leading=12
    ))
    styles.add(ParagraphStyle(
        'TableCellBold', fontName='Helvetica-Bold', fontSize=9,
        textColor=HexColor('#1F2937'), alignment=TA_LEFT, leading=12
    ))
    styles.add(ParagraphStyle(
        'PhaseTitle', fontName='Helvetica-Bold', fontSize=14,
        textColor=white, alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        'Footer', fontName='Helvetica', fontSize=8,
        textColor=GRAY, alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        'ImportantNote', fontName='Helvetica-Bold', fontSize=10,
        textColor=BRAND_RED, spaceAfter=6, leading=14,
        leftIndent=10, borderColor=BRAND_RED, borderWidth=1, borderPadding=6
    ))
    styles.add(ParagraphStyle(
        'WarningNote', fontName='Helvetica-Bold', fontSize=10,
        textColor=ORANGE, spaceAfter=6, leading=14, leftIndent=10
    ))
    return styles


def generate_report():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.6*inch, bottomMargin=0.6*inch,
        leftMargin=0.75*inch, rightMargin=0.75*inch
    )
    styles = build_styles()
    elements = []

    # ═══════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════
    elements.append(Spacer(1, 80))
    elements.append(HRFlowable(width="100%", thickness=3, color=BRAND_RED))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("ROSS HOUSE RENTALS LLC", styles['CoverTitle']))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(
        "Guia Completa de Licencias de Bienes Raices en Texas",
        styles['CoverSubBold']
    ))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Sales Agent vs REALTOR vs Broker | Escuelas en Espanol | House Flipping",
        styles['CoverSub']
    ))
    elements.append(Paragraph(
        "Marketplace de Propiedades | Plan de Ejecucion por Fases",
        styles['CoverSub']
    ))
    elements.append(Spacer(1, 20))
    elements.append(HRFlowable(width="100%", thickness=3, color=BRAND_RED))
    elements.append(Spacer(1, 30))

    # Quick index
    elements.append(Paragraph("<b>CONTENIDO DEL REPORTE:</b>", styles['BodyBold']))
    toc_items = [
        "1. Comparativa: Sales Agent vs REALTOR vs Broker",
        "2. Paso a Paso para Obtener Cada Licencia",
        "3. Escuelas de Bienes Raices (Opciones en Espanol)",
        "4. House Flipping: Reglas y Regulaciones en Texas",
        "5. Wholesaling en Texas: Lo que Puedes y No Puedes Hacer",
        "6. Marketplace de Propiedades para Terceros: Requisitos Legales",
        "7. Plan de Ejecucion Detallado por Fases",
        "8. Presupuesto Estimado y Cronograma",
        "9. Links y Recursos Importantes",
    ]
    for item in toc_items:
        elements.append(Paragraph(item, styles['BulletItem']))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(
        f"Preparado para: Yoandy Ross | Fecha: {datetime.now().strftime('%d de %B, %Y')}",
        styles['CoverSub']
    ))
    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 1: COMPARATIVA
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("1. COMPARATIVA: SALES AGENT vs REALTOR vs BROKER", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "En Texas, existen tres roles principales en bienes raices. Es fundamental entender "
        "la diferencia porque cada uno tiene diferentes requisitos, costos, y alcance legal.",
        styles['Body']
    ))
    elements.append(Spacer(1, 8))

    # Comparison Table
    header_style = styles['TableHeader']
    cell_style = styles['TableCell']
    bold_cell = styles['TableCellBold']

    table_data = [
        [
            Paragraph("<b>Aspecto</b>", header_style),
            Paragraph("<b>Sales Agent</b>", header_style),
            Paragraph("<b>Broker</b>", header_style),
            Paragraph("<b>REALTOR</b>", header_style),
        ],
        [
            Paragraph("<b>Que es?</b>", bold_cell),
            Paragraph("Licencia de nivel inicial otorgada por TREC. Puede comprar/vender propiedades en nombre de clientes.", cell_style),
            Paragraph("Licencia de nivel superior. Puede operar independientemente, supervisar agentes y manejar su propia firma.", cell_style),
            Paragraph("NO es una licencia. Es una membresia en la National Association of REALTORS (NAR). Puede ser Agent o Broker.", cell_style),
        ],
        [
            Paragraph("<b>Puede trabajar solo?</b>", bold_cell),
            Paragraph("NO. Debe trabajar bajo un Broker patrocinador.", cell_style),
            Paragraph("SI. Puede abrir su propia oficina y patrocinar agentes.", cell_style),
            Paragraph("Depende de su licencia base (Agent o Broker).", cell_style),
        ],
        [
            Paragraph("<b>Puede patrocinar agentes?</b>", bold_cell),
            Paragraph("NO.", cell_style),
            Paragraph("SI.", cell_style),
            Paragraph("Solo si tambien tiene licencia de Broker.", cell_style),
        ],
        [
            Paragraph("<b>Educacion requerida</b>", bold_cell),
            Paragraph("180 horas (6 cursos de 30 hrs c/u) aprobados por TREC.", cell_style),
            Paragraph("270 horas + curso de Brokerage Administration (30 hrs) + 4 anos de experiencia activa.", cell_style),
            Paragraph("Tener licencia activa (Agent o Broker) + pagar cuota NAR + completar orientacion.", cell_style),
        ],
        [
            Paragraph("<b>Examen</b>", bold_cell),
            Paragraph("Si, via Pearson VUE. Examen estatal + nacional.", cell_style),
            Paragraph("Si, examen de Broker via Pearson VUE (mas dificil).", cell_style),
            Paragraph("No hay examen adicional de NAR.", cell_style),
        ],
        [
            Paragraph("<b>Costo estimado total</b>", bold_cell),
            Paragraph("$800 - $1,500 (educacion + examen + solicitud + fingerprints).", cell_style),
            Paragraph("$1,500 - $3,000 (educacion adicional + examen + experiencia).", cell_style),
            Paragraph("$150 - $500/ano (cuota NAR local + estatal + nacional).", cell_style),
        ],
        [
            Paragraph("<b>Renovacion</b>", bold_cell),
            Paragraph("Cada 2 anos. 18 hrs CE requeridas.", cell_style),
            Paragraph("Cada 2 anos. 18 hrs CE requeridas.", cell_style),
            Paragraph("Anual. Cuota de membresia + etica NAR.", cell_style),
        ],
        [
            Paragraph("<b>Beneficios clave</b>", bold_cell),
            Paragraph("Entrada rapida al mercado. Comisiones por ventas/rentas. Acceso a MLS con broker.", cell_style),
            Paragraph("Independencia total. Puede abrir firma. Retiene mas comision. Supervision.", cell_style),
            Paragraph("Acceso a MLS. Credibilidad con clientes. Networking. Designaciones especiales.", cell_style),
        ],
    ]

    col_widths = [1.1*inch, 2.0*inch, 2.0*inch, 1.9*inch]
    comparison_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    comparison_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_BG]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(comparison_table)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "IMPORTANTE: Para tu plan de marketplace, eventualmente necesitaras la licencia de "
        "BROKER para poder operar independientemente y patrocinar agentes.",
        styles['ImportantNote']
    ))

    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 2: PASO A PASO
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("2. PASO A PASO PARA OBTENER CADA LICENCIA", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    # --- Sales Agent ---
    elements.append(Paragraph("A) LICENCIA DE SALES AGENT (Agente de Ventas)", styles['SubSection']))
    sa_steps = [
        "<b>Paso 1 - Verificar Elegibilidad:</b> Ser mayor de 18 anos, ciudadano americano o residente legal, y cumplir con los estandares de honestidad e integridad de TREC.",
        "<b>Paso 2 - Aplicar a TREC:</b> Enviar la solicitud de Sales Agent y pagar la tarifa ($205) en el portal online de TREC. Tienes 1 ano para completar todos los requisitos.",
        "<b>Paso 3 - Completar 180 Horas de Educacion:</b> Son 6 cursos obligatorios de 30 horas cada uno:\n"
        "   - Principles of Real Estate I\n"
        "   - Principles of Real Estate II\n"
        "   - Law of Agency\n"
        "   - Law of Contracts\n"
        "   - Promulgated Contract Forms\n"
        "   - Real Estate Finance",
        "<b>Paso 4 - Huellas Digitales (Fingerprints):</b> Programa una cita para fingerprints a traves del Department of Public Safety de Texas. TREC NO acepta huellas de otras agencias. Costo ~$38.",
        "<b>Paso 5 - Background Check:</b> TREC realizara una verificacion de antecedentes criminales automaticamente despues de recibir tus huellas.",
        "<b>Paso 6 - Examen Estatal:</b> Programar el examen con Pearson VUE ($54). El examen tiene dos partes: nacional y estatal. Si fallas 3 veces, necesitas educacion adicional antes de reintentar.",
        "<b>Paso 7 - Encontrar un Broker Patrocinador:</b> Despues de pasar el examen, TREC emite tu licencia en estado INACTIVO. Necesitas un broker activo en Texas que te patrocine para poder practicar.",
        "<b>Paso 8 - Activar la Licencia:</b> Una vez que el broker acepta el patrocinio en el sistema de TREC, tu licencia se activa y puedes comenzar a trabajar.",
    ]
    for step in sa_steps:
        elements.append(Paragraph(step, styles['BulletItem']))
    elements.append(Spacer(1, 5))

    # Cost summary for SA
    sa_cost_data = [
        [Paragraph("<b>Concepto</b>", header_style), Paragraph("<b>Costo Estimado</b>", header_style)],
        [Paragraph("Solicitud TREC", cell_style), Paragraph("$205", cell_style)],
        [Paragraph("Educacion (180 hrs online)", cell_style), Paragraph("$400 - $800", cell_style)],
        [Paragraph("Fingerprints", cell_style), Paragraph("$38", cell_style)],
        [Paragraph("Examen Pearson VUE", cell_style), Paragraph("$54", cell_style)],
        [Paragraph("Background Check", cell_style), Paragraph("Incluido con fingerprints", cell_style)],
        [Paragraph("<b>TOTAL ESTIMADO</b>", bold_cell), Paragraph("<b>$697 - $1,097</b>", bold_cell)],
    ]
    sa_cost_table = Table(sa_cost_data, colWidths=[4*inch, 2.5*inch])
    sa_cost_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_BLUE]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(sa_cost_table)
    elements.append(Spacer(1, 5))
    elements.append(Paragraph("Tiempo estimado: 2-4 meses (dependiendo de la velocidad de estudio).", styles['BodyBold']))
    elements.append(Spacer(1, 12))

    # --- Broker ---
    elements.append(Paragraph("B) LICENCIA DE BROKER", styles['SubSection']))
    broker_steps = [
        "<b>Paso 1 - Requisito Previo:</b> Debes haber tenido una licencia de Sales Agent ACTIVA por al menos 4 anos continuos en los ultimos 5 anos.",
        "<b>Paso 2 - Completar 270 Horas de Educacion:</b> Incluye las 180 horas de Sales Agent + 90 horas adicionales. MAS el curso obligatorio de Brokerage Administration (30 hrs).",
        "<b>Paso 3 - Aplicar a TREC para Broker:</b> Enviar solicitud de Broker ($305) con prueba de experiencia y educacion.",
        "<b>Paso 4 - Examen de Broker:</b> Programar y pasar el examen de Broker con Pearson VUE. Es mas extenso y dificil que el de Sales Agent.",
        "<b>Paso 5 - Activar:</b> Una vez aprobado, puedes operar independientemente, abrir tu propia firma, y patrocinar otros agentes.",
    ]
    for step in broker_steps:
        elements.append(Paragraph(step, styles['BulletItem']))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph(
        "NOTA: El camino a Broker es mas largo pero es ESENCIAL si quieres operar tu marketplace "
        "independientemente y patrocinar agentes que vendan propiedades de terceros en tu plataforma.",
        styles['WarningNote']
    ))
    elements.append(Spacer(1, 12))

    # --- REALTOR ---
    elements.append(Paragraph("C) MEMBRESIA DE REALTOR (NAR)", styles['SubSection']))
    realtor_steps = [
        "<b>Paso 1:</b> Tener una licencia activa de Sales Agent O Broker en Texas.",
        "<b>Paso 2:</b> Unirse a una asociacion local de REALTORS afiliada a NAR (ej: Houston Association of REALTORS, Dallas/Fort Worth, etc.).",
        "<b>Paso 3:</b> Pagar las cuotas anuales (~$150-$500 dependiendo de la asociacion local).",
        "<b>Paso 4:</b> Completar la orientacion de NAR y el curso de etica.",
        "<b>Beneficios:</b> Acceso al MLS (Multiple Listing Service), credibilidad con el titulo REALTOR, herramientas de marketing, networking, y designaciones especiales como CRS, ABR, etc.",
    ]
    for step in realtor_steps:
        elements.append(Paragraph(step, styles['BulletItem']))

    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 3: ESCUELAS EN ESPANOL
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("3. ESCUELAS DE BIENES RAICES (Opciones en Espanol)", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "A continuacion se listan las escuelas mas reconocidas en Texas para obtener tu licencia. "
        "Nota: La mayoria de los cursos TREC-approved estan en ingles, pero algunas escuelas ofrecen "
        "soporte parcial en espanol o guias traducidas.",
        styles['Body']
    ))
    elements.append(Spacer(1, 8))

    schools = [
        {
            "name": "Champions School of Real Estate",
            "url": "https://www.championsschool.com",
            "spanish": "SI - Guia de pasos en espanol disponible (PDF). Material del curso principal en ingles.",
            "mode": "Online + Presencial (Houston, Dallas, Austin, San Antonio, Fort Worth)",
            "cost": "$400 - $800 (paquete completo de 180 hrs)",
            "pros": "La escuela #1 en Texas. Reconocida por TREC. Soporte al estudiante. Preparacion para examen incluida.",
            "link_pdf": "https://www.championsschool.com/resources/files/re-steps-spanish-v20250131.pdf"
        },
        {
            "name": "The CE Shop",
            "url": "https://www.theceshop.com/texas",
            "spanish": "Parcial - Interfaz puede traducirse, material principal en ingles.",
            "mode": "100% Online (a tu propio ritmo)",
            "cost": "$400 - $700",
            "pros": "Flexible, 100% online. Acceso movil. Material interactivo. Garantia de devolucion.",
        },
        {
            "name": "Aceable Agent",
            "url": "https://www.aceableagent.com/real-estate/texas/",
            "spanish": "NO confirmado en espanol completo.",
            "mode": "100% Online + App Movil",
            "cost": "$449 - $649",
            "pros": "Moderna, app movil, videos interactivos. Aprobada por TREC. La mas popular entre jovenes.",
        },
        {
            "name": "VanEd Real Estate School",
            "url": "https://www.vaned.com/texas/",
            "spanish": "NO - Solo en ingles.",
            "mode": "100% Online",
            "cost": "$350 - $600",
            "pros": "Economica, buena preparacion para examen. Acceso ilimitado.",
        },
        {
            "name": "Kaplan Real Estate Education",
            "url": "https://www.kapre.com/real-estate-licensing/texas",
            "spanish": "NO - Solo en ingles.",
            "mode": "Online + Live Online + Presencial",
            "cost": "$500 - $900",
            "pros": "Marca reconocida nacionalmente. Profesores experimentados. Multiples formatos.",
        },
        {
            "name": "ICA School (International Career Academy)",
            "url": "https://icaschool.com/es/licencias-estatales/texas/",
            "spanish": "SI - Pagina en espanol disponible. Atencion en espanol.",
            "mode": "Online",
            "cost": "$300 - $500",
            "pros": "Opciones en espanol. Cursos de inspeccion de viviendas tambien disponibles.",
        },
    ]

    for school in schools:
        elements.append(Paragraph(f"<b>{school['name']}</b>", styles['SubSection2']))
        elements.append(Paragraph(f"Web: <font color='blue'><u>{school['url']}</u></font>", styles['LinkStyle']))
        elements.append(Paragraph(f"<b>Espanol:</b> {school['spanish']}", styles['BulletItem']))
        elements.append(Paragraph(f"<b>Modalidad:</b> {school['mode']}", styles['BulletItem']))
        elements.append(Paragraph(f"<b>Costo:</b> {school['cost']}", styles['BulletItem']))
        elements.append(Paragraph(f"<b>Ventajas:</b> {school['pros']}", styles['BulletItem']))
        if 'link_pdf' in school:
            elements.append(Paragraph(
                f"PDF en Espanol: <font color='blue'><u>{school['link_pdf']}</u></font>",
                styles['LinkStyle']
            ))
        elements.append(Spacer(1, 6))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "RECOMENDACION: Champions School of Real Estate es la mejor opcion para Texas. "
        "Tiene la guia de pasos traducida al espanol y es la mas reconocida por TREC. "
        "Si prefieres algo 100% online, Aceable Agent o The CE Shop son excelentes alternativas.",
        styles['ImportantNote']
    ))

    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 4: HOUSE FLIPPING
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("4. HOUSE FLIPPING: Reglas y Regulaciones en Texas", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "Comprar casas, repararlas y venderlas (house flipping) es una estrategia popular y LEGAL "
        "en Texas. Aqui estan las reglas clave:",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    elements.append(Paragraph("Necesito licencia para hacer flipping?", styles['SubSection']))
    elements.append(Paragraph(
        "<b>NO</b> necesitas licencia de bienes raices para comprar una propiedad a tu nombre, "
        "renovarla, y venderla como inversionista. Solo compras y vendes TU PROPIA propiedad.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Sin embargo, SI necesitas licencia si quieres representar a OTROS compradores/vendedores "
        "o cobrar comision por facilitar transacciones de terceros.",
        styles['BodyBold']
    ))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("Reglas Importantes para Flipping en Texas:", styles['SubSection']))
    flip_rules = [
        "<b>Permisos de Construccion:</b> Las renovaciones significativas (electricas, plomeria, estructurales) "
        "requieren permisos del condado/ciudad. Trabajar sin permisos puede resultar en multas y problemas al vender.",
        "<b>Seller's Disclosure Notice:</b> Texas exige que el vendedor proporcione un aviso de divulgacion sobre "
        "defectos conocidos de la propiedad. Esto incluye problemas estructurales, de plomeria, techos, etc. "
        "NO divulgar es una violacion legal.",
        "<b>Regla FHA de 90 Dias:</b> Si el comprador usa financiamiento FHA, hay una regla que restringe la reventa "
        "de propiedades dentro de los primeros 90 dias de haberla comprado. Si vendes antes de 90 dias, el "
        "comprador FHA no puede obtener su prestamo. Planifica al menos 90+ dias entre compra y venta.",
        "<b>Impuestos:</b> Las ganancias de flipping se consideran ingreso ordinario (no capital gains a largo plazo). "
        "Debes reportarlas al IRS como ingreso activo de negocio. Consulta con tu CPA.",
        "<b>Codigos de Construccion Locales:</b> Cada ciudad tiene sus propios codigos. En Dumas, TX, verifica "
        "con el departamento de permisos del condado Moore.",
        "<b>Seguro:</b> Necesitas seguro de constructor (builder's risk insurance) durante la renovacion. "
        "El seguro de propietario normal NO cubre renovaciones activas.",
        "<b>Contratistas:</b> Usa contratistas con licencia y seguro. Guarda TODOS los recibos y contratos "
        "para documentar mejoras (importante para impuestos y disputas).",
    ]
    for rule in flip_rules:
        elements.append(Paragraph(rule, styles['BulletItem']))

    elements.append(Spacer(1, 8))

    # Flipping cost estimate
    flip_cost_data = [
        [Paragraph("<b>Concepto</b>", header_style), Paragraph("<b>Estimado (Dumas, TX)</b>", header_style)],
        [Paragraph("Compra de propiedad", cell_style), Paragraph("$50,000 - $120,000", cell_style)],
        [Paragraph("Renovacion completa", cell_style), Paragraph("$20,000 - $60,000", cell_style)],
        [Paragraph("Permisos y inspecciones", cell_style), Paragraph("$500 - $2,000", cell_style)],
        [Paragraph("Seguro (builder's risk)", cell_style), Paragraph("$1,000 - $3,000", cell_style)],
        [Paragraph("Costos de cierre (compra + venta)", cell_style), Paragraph("$3,000 - $8,000", cell_style)],
        [Paragraph("Marketing/staging", cell_style), Paragraph("$500 - $2,000", cell_style)],
        [Paragraph("<b>INVERSION TOTAL ESTIMADA</b>", bold_cell), Paragraph("<b>$75,000 - $195,000</b>", bold_cell)],
        [Paragraph("<b>PRECIO VENTA OBJETIVO</b>", bold_cell), Paragraph("<b>$130,000 - $250,000+</b>", bold_cell)],
    ]
    flip_table = Table(flip_cost_data, colWidths=[4*inch, 2.5*inch])
    flip_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), GREEN),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GREEN]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(flip_table)

    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 5: WHOLESALING
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("5. WHOLESALING EN TEXAS: Lo que Puedes y No Puedes Hacer", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "Wholesaling es cuando consigues un contrato de compra con un vendedor y luego asignas "
        "ese contrato a otro comprador por una tarifa. Es legal en Texas PERO tiene reglas estrictas.",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    elements.append(Paragraph("LO QUE PUEDES HACER:", styles['SubSection']))
    can_do = [
        "Asignar contratos de compra a otros compradores.",
        "Cobrar una tarifa de asignacion (assignment fee) por transferir el contrato.",
        "Hacer double closings (comprar y revender el mismo dia usando transactional funding).",
        "Mercadear propiedades que estan bajo tu contrato (con divulgacion).",
    ]
    for item in can_do:
        elements.append(Paragraph(f"<font color='green'>OK</font> - {item}", styles['BulletItem']))

    elements.append(Spacer(1, 6))
    elements.append(Paragraph("LO QUE NO PUEDES HACER SIN LICENCIA:", styles['SubSection']))
    cant_do = [
        "Representar a un comprador o vendedor como agente (eso es brokerage sin licencia).",
        "Publicitar la propiedad como si fueras el dueno o agente autorizado.",
        "NO divulgar que estas vendiendo un interes contractual (equitable interest) y no la propiedad.",
        "Cobrar comision por facilitar transacciones de otros (eso requiere licencia de Broker).",
    ]
    for item in cant_do:
        elements.append(Paragraph(f"<font color='red'>PROHIBIDO</font> - {item}", styles['BulletItem']))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "REGLA CLAVE DE TREC: Debes divulgar POR ESCRITO al vendedor y al comprador que estas "
        "vendiendo un interes contractual (equitable interest/contract rights), NO la propiedad en si. "
        "No hacerlo puede resultar en accion disciplinaria de TREC.",
        styles['ImportantNote']
    ))

    elements.append(Spacer(1, 15))

    # ═══════════════════════════════════════════════════════════
    # SECTION 6: MARKETPLACE PARA TERCEROS
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("6. MARKETPLACE DE PROPIEDADES PARA TERCEROS", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "Tu vision es que terceros puedan publicar sus propiedades en tu app/web para que otros "
        "las compren o renten. Esto tiene implicaciones legales importantes:",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    elements.append(Paragraph("Escenario A: Solo Publicidad (Sin Intermediacion)", styles['SubSection']))
    elements.append(Paragraph(
        "Si tu plataforma SOLO publica listados (como Craigslist o Facebook Marketplace) y no cobra "
        "comision por ventas ni representa a compradores/vendedores, NO necesitas licencia. Los usuarios "
        "se contactan directamente entre si.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Sin embargo, si comienzas a facilitar la transaccion, negociar terminos, o cobrar comision "
        "basada en la venta, entonces entras en territorio de brokerage y SI necesitas licencia.",
        styles['BodyBold']
    ))
    elements.append(Spacer(1, 6))

    elements.append(Paragraph("Escenario B: Marketplace con Brokerage (Modelo Completo)", styles['SubSection']))
    market_reqs = [
        "<b>Licencia de Broker requerida:</b> Para operar un marketplace donde facilitas transacciones, "
        "cobras comision, o representas a compradores/vendedores, necesitas licencia de Broker.",
        "<b>Acuerdos escritos de representacion:</b> A partir de enero 2026, Texas requiere acuerdos ESCRITOS "
        "de representacion de compradores antes de mostrar propiedades o hacer ofertas.",
        "<b>Disclosure IABS:</b> Debes proporcionar el formulario Information About Brokerage Services (IABS) "
        "a todos los participantes.",
        "<b>Atribucion del Broker:</b> Todo listado debe mostrar claramente quien es el broker autorizado.",
        "<b>Acceso MLS:</b> Si quieres integrar listados del MLS, necesitas ser miembro de una asociacion "
        "de REALTORS y cumplir con sus reglas de uso de datos.",
        "<b>No editar listados de terceros:</b> No puedes alterar la informacion de un listado de manera "
        "que sea enganosa.",
    ]
    for req in market_reqs:
        elements.append(Paragraph(req, styles['BulletItem']))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "RECOMENDACION PARA ROSS HOUSE: Comienza con el Escenario A (solo publicidad, sin comision "
        "por transaccion) mientras obtienes tu licencia de Sales Agent. Luego, cuando obtengas la "
        "licencia de Broker en 4-5 anos, migra al Escenario B completo.",
        styles['ImportantNote']
    ))

    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 7: PLAN DE EJECUCION POR FASES
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("7. PLAN DE EJECUCION DETALLADO POR FASES", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    phases = [
        {
            "title": "FASE 1: FUNDACION (Meses 1-4)",
            "color": BLUE,
            "items": [
                "<b>Licencia de Sales Agent:</b> Inscribirse en Champions School (o Aceable Agent). Completar las 180 horas de educacion. Programar fingerprints y examen.",
                "<b>House Flipping - Primer Flip:</b> Identificar propiedad en Dumas/Amarillo. Asegurar financiamiento. Comenzar renovacion de la primera propiedad.",
                "<b>App/Web Enhancement:</b> Agregar seccion de 'Propiedades en Venta' en la app donde puedas listar TUS propiedades de flipping.",
                "<b>LLC Setup:</b> Crear LLC separada para flipping (ej: 'Ross Property Investments LLC') para proteccion legal.",
                "<b>Networking:</b> Unirse a grupos locales de inversionistas (REIAs - Real Estate Investors Associations) en Amarillo/Lubbock.",
                "<b>Meta:</b> Obtener licencia de Sales Agent. Completar primer flip. Listar tus propiedades en la app.",
            ]
        },
        {
            "title": "FASE 2: ACTIVACION (Meses 5-12)",
            "color": GREEN,
            "items": [
                "<b>Activar Licencia:</b> Encontrar un Broker patrocinador. Comenzar a practicar como Sales Agent.",
                "<b>Membresia REALTOR:</b> Unirse a NAR y asociacion local. Obtener acceso al MLS.",
                "<b>Marketplace Fase A:</b> Lanzar el marketplace en tu app/web en modo 'anuncio clasificado' (sin comision directa). "
                "Los duenos de propiedades pueden publicar GRATIS o por una tarifa plana de publicacion.",
                "<b>Segundo/Tercer Flip:</b> Con la experiencia del primer flip, escalar. Considerar propiedades mas grandes o multi-familiares.",
                "<b>Utility Tracker:</b> Completar la integracion del AI Utility Tracker en la app para agregar valor a tus inquilinos.",
                "<b>Meta:</b> Generar ingresos por ventas como Agent. 2-3 flips completados. Marketplace activo con al menos 10 listados.",
            ]
        },
        {
            "title": "FASE 3: EXPANSION (Meses 13-24)",
            "color": PURPLE,
            "items": [
                "<b>Equipo:</b> Reclutar 1-2 Sales Agents para trabajar bajo tu Broker patrocinador (comisiones compartidas).",
                "<b>Flipping Operation:</b> Establecer una operacion de flipping con contratistas fijos, sistema de estimaciones, y pipeline de propiedades.",
                "<b>Marketplace Mejorado:</b> Agregar featured listings (listados destacados pagados), filtros avanzados de busqueda, "
                "y mapas interactivos. Monetizar con suscripciones o tarifas de publicacion premium.",
                "<b>Educacion Broker:</b> Comenzar las 90 horas adicionales de educacion para la licencia de Broker.",
                "<b>Xcel Energy API:</b> Si aprobado, integrar los datos de consumo de energia directamente en la app.",
                "<b>Meta:</b> 5-10 flips completados. Marketplace con 50+ listados. Progreso hacia licencia de Broker.",
            ]
        },
        {
            "title": "FASE 4: BROKER INDEPENDIENTE (Meses 25-48)",
            "color": GOLD,
            "items": [
                "<b>Obtener Licencia de Broker:</b> Completar educacion de Broker + examen. Operar independientemente.",
                "<b>Marketplace Completo (Escenario B):</b> Ahora PUEDES facilitar transacciones, cobrar comision, y representar "
                "compradores/vendedores a traves de tu plataforma.",
                "<b>MLS Integration:</b> Integrar datos del MLS en tu marketplace para mostrar listados verificados.",
                "<b>Patrocinar Agentes:</b> Reclutar agentes que usen tu plataforma. Tu ganas override commission.",
                "<b>Escalar Flipping:</b> Operar 3-5 flips simultaneos. Considerar BRRRR strategy (Buy, Rehab, Rent, Refinance, Repeat).",
                "<b>Expansion Geografica:</b> Expandir marketplace a otras ciudades del Panhandle de Texas.",
                "<b>Meta:</b> Broker independiente. Marketplace generando ingresos pasivos. Equipo de agentes activo.",
            ]
        },
        {
            "title": "FASE 5: VISION A LARGO PLAZO (Ano 5+)",
            "color": BRAND_RED,
            "items": [
                "<b>Ross Real Estate Group:</b> Firma completa de bienes raices bajo tu marca.",
                "<b>App como Plataforma:</b> Ross House se convierte en LA plataforma de propiedades para el Panhandle de Texas.",
                "<b>Property Management + Sales + Flipping:</b> Tres fuentes de ingresos operando simultaneamente.",
                "<b>Franquicia:</b> Considerar modelo de franquicia para expandir a otras regiones.",
                "<b>Commercial Real Estate:</b> Expandir a propiedades comerciales (requiere experiencia adicional).",
            ]
        },
    ]

    for phase in phases:
        # Phase header
        phase_header_data = [[Paragraph(phase['title'], styles['PhaseTitle'])]]
        phase_header = Table(phase_header_data, colWidths=[6.5*inch])
        phase_header.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), phase['color']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(phase_header)
        elements.append(Spacer(1, 6))

        for item in phase['items']:
            elements.append(Paragraph(item, styles['BulletItem']))
        elements.append(Spacer(1, 12))

    elements.append(PageBreak())

    # ═══════════════════════════════════════════════════════════
    # SECTION 8: PRESUPUESTO Y CRONOGRAMA
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("8. PRESUPUESTO ESTIMADO Y CRONOGRAMA", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    budget_data = [
        [
            Paragraph("<b>Fase</b>", header_style),
            Paragraph("<b>Plazo</b>", header_style),
            Paragraph("<b>Inversion</b>", header_style),
            Paragraph("<b>Ingresos Potenciales</b>", header_style),
        ],
        [
            Paragraph("1. Fundacion", bold_cell),
            Paragraph("Meses 1-4", cell_style),
            Paragraph("$1,000 - $1,500 (licencia)", cell_style),
            Paragraph("$0 (preparacion)", cell_style),
        ],
        [
            Paragraph("1b. Primer Flip", bold_cell),
            Paragraph("Meses 2-5", cell_style),
            Paragraph("$75K - $130K", cell_style),
            Paragraph("$20K - $50K profit", cell_style),
        ],
        [
            Paragraph("2. Activacion", bold_cell),
            Paragraph("Meses 5-12", cell_style),
            Paragraph("$500/ano (NAR) + flips", cell_style),
            Paragraph("$40K - $100K/ano", cell_style),
        ],
        [
            Paragraph("3. Expansion", bold_cell),
            Paragraph("Meses 13-24", cell_style),
            Paragraph("$2K - $5K (educacion Broker)", cell_style),
            Paragraph("$100K - $250K/ano", cell_style),
        ],
        [
            Paragraph("4. Broker", bold_cell),
            Paragraph("Meses 25-48", cell_style),
            Paragraph("$5K - $10K (firma, oficina)", cell_style),
            Paragraph("$200K - $500K+/ano", cell_style),
        ],
        [
            Paragraph("5. Vision", bold_cell),
            Paragraph("Ano 5+", cell_style),
            Paragraph("Variable", cell_style),
            Paragraph("$500K+/ano", cell_style),
        ],
    ]

    budget_table = Table(budget_data, colWidths=[1.3*inch, 1.2*inch, 2.0*inch, 2.0*inch], repeatRows=1)
    budget_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#D1D5DB')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_BG]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(budget_table)

    elements.append(Spacer(1, 15))

    # ═══════════════════════════════════════════════════════════
    # SECTION 9: LINKS Y RECURSOS
    # ═══════════════════════════════════════════════════════════
    elements.append(Paragraph("9. LINKS Y RECURSOS IMPORTANTES", styles['SectionTitle']))
    elements.append(HRFlowable(width="100%", thickness=1, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("TREC (Texas Real Estate Commission):", styles['SubSection']))
    trec_links = [
        ("Aplicar para Sales Agent", "https://www.trec.texas.gov/become-licensed/sales-agent"),
        ("Requisitos de Broker", "https://www.trec.texas.gov/become-licensed/broker"),
        ("Renovar Licencia", "https://www.trec.texas.gov/renew-license/real-estate-sales-agent"),
        ("Reglas y Leyes de TREC", "https://www.trec.texas.gov/rules-and-laws"),
        ("Formularios de Contratos TREC", "https://www.trec.texas.gov/agency-information/contracts"),
        ("Cambios de Reglas 2025-2026", "https://licenseclassroom.com/sb-1968-upcoming-trec-rule-changes/"),
    ]
    for name, url in trec_links:
        elements.append(Paragraph(
            f"<b>{name}:</b> <font color='blue'><u>{url}</u></font>",
            styles['BulletItem']
        ))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Examen y Fingerprints:", styles['SubSection']))
    exam_links = [
        ("Pearson VUE (Programar Examen)", "https://home.pearsonvue.com/trec"),
        ("Texas DPS (Fingerprints)", "https://www.dps.texas.gov"),
    ]
    for name, url in exam_links:
        elements.append(Paragraph(
            f"<b>{name}:</b> <font color='blue'><u>{url}</u></font>",
            styles['BulletItem']
        ))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Escuelas de Bienes Raices:", styles['SubSection']))
    school_links = [
        ("Champions School of Real Estate", "https://www.championsschool.com"),
        ("Champions - Guia en Espanol (PDF)", "https://www.championsschool.com/resources/files/re-steps-spanish-v20250131.pdf"),
        ("The CE Shop - Texas", "https://www.theceshop.com/texas"),
        ("Aceable Agent - Texas", "https://www.aceableagent.com/real-estate/texas/"),
        ("ICA School (Espanol)", "https://icaschool.com/es/licencias-estatales/texas/"),
        ("VanEd", "https://www.vaned.com/texas/"),
        ("Kaplan", "https://www.kapre.com/real-estate-licensing/texas"),
    ]
    for name, url in school_links:
        elements.append(Paragraph(
            f"<b>{name}:</b> <font color='blue'><u>{url}</u></font>",
            styles['BulletItem']
        ))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("NAR / REALTOR:", styles['SubSection']))
    nar_links = [
        ("National Association of REALTORS", "https://www.nar.realtor"),
        ("Texas Association of REALTORS", "https://www.texasrealestate.com"),
        ("Buscar Asociacion Local", "https://www.nar.realtor/about-nar/local-associations"),
    ]
    for name, url in nar_links:
        elements.append(Paragraph(
            f"<b>{name}:</b> <font color='blue'><u>{url}</u></font>",
            styles['BulletItem']
        ))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph("Flipping y Wholesaling:", styles['SubSection']))
    flip_links = [
        ("Reglas de Wholesaling en Texas", "https://silblawfirm.com/real-estate-law/how-wholesaling-works-in-texas/"),
        ("FHA 90-Day Flip Rule", "https://www.brightbidhomes.com/blog/fha-flip-rule/"),
        ("Flipping Legal Pitfalls Texas", "https://txprobatelawyer.net/flipping-houses-in-texas-legal-pitfalls-you-might-overlook/"),
    ]
    for name, url in flip_links:
        elements.append(Paragraph(
            f"<b>{name}:</b> <font color='blue'><u>{url}</u></font>",
            styles['BulletItem']
        ))

    # ═══════════════════════════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════════════════════════
    elements.append(Spacer(1, 30))
    elements.append(HRFlowable(width="100%", thickness=2, color=BRAND_RED))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(
        "Documento generado automaticamente por Ross House Rentals LLC — "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}",
        styles['Footer']
    ))
    elements.append(Paragraph(
        "Confidencial — Preparado exclusivamente para Yoandy Ross",
        styles['Footer']
    ))
    elements.append(Paragraph(
        "AVISO: Este documento es informativo y NO constituye asesoria legal. "
        "Consulte con un abogado de bienes raices para su situacion especifica.",
        styles['Footer']
    ))

    doc.build(elements)
    return buffer.getvalue()


def send_email_with_pdf(pdf_bytes, to_email="yoandyross@gmail.com"):
    """Send the PDF via SendGrid."""
    import sendgrid
    from sendgrid.helpers.mail import (
        Mail, Attachment, FileContent, FileName, FileType, Disposition
    )

    sg_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")

    if not sg_key:
        print("ERROR: SENDGRID_API_KEY not found in environment")
        return False

    encoded_pdf = base64.b64encode(pdf_bytes).decode()

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject="Ross House Rentals - Guia Completa de Licencias de Bienes Raices en Texas",
        html_content="""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #C8102E; padding: 20px; text-align: center;">
                <h1 style="color: white; margin: 0;">ROSS HOUSE RENTALS LLC</h1>
            </div>
            <div style="padding: 20px; background: #f8f9fa;">
                <h2 style="color: #1a1a2e;">Guia de Licencias de Bienes Raices en Texas</h2>
                <p>Hola Yoandy,</p>
                <p>Adjunto encontraras el reporte completo con:</p>
                <ul>
                    <li><b>Comparativa:</b> Sales Agent vs REALTOR vs Broker</li>
                    <li><b>Paso a Paso:</b> Como aplicar a cada licencia</li>
                    <li><b>Escuelas:</b> Opciones en espanol para Texas</li>
                    <li><b>House Flipping:</b> Reglas y regulaciones</li>
                    <li><b>Wholesaling:</b> Lo permitido y lo prohibido</li>
                    <li><b>Marketplace:</b> Requisitos para listados de terceros</li>
                    <li><b>Plan de Ejecucion:</b> 5 fases detalladas con cronograma y presupuesto</li>
                </ul>
                <p style="color: #C8102E;"><b>Nota:</b> Este documento es informativo. Consulta con un abogado de bienes raices para tu situacion especifica.</p>
                <p>Saludos,<br><b>Ross House Rentals LLC</b></p>
            </div>
            <div style="background: #1a1a2e; padding: 10px; text-align: center;">
                <p style="color: #9CA3AF; font-size: 12px; margin: 0;">Generado automaticamente por el sistema Ross House</p>
            </div>
        </div>
        """
    )

    attachment = Attachment(
        FileContent(encoded_pdf),
        FileName('Guia_Bienes_Raices_Texas_Ross_House.pdf'),
        FileType('application/pdf'),
        Disposition('attachment')
    )
    message.attachment = attachment

    try:
        sg = sendgrid.SendGridAPIClient(api_key=sg_key)
        response = sg.send(message)
        print(f"Email enviado! Status: {response.status_code}")
        return response.status_code in [200, 201, 202]
    except Exception as e:
        print(f"Error enviando email: {e}")
        return False


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv('/app/ross-house-backend/.env')

    print("Generando PDF de Licencias de Bienes Raices en Texas...")
    pdf_bytes = generate_report()

    # Save locally
    output_path = '/tmp/guia_bienes_raices_texas.pdf'
    with open(output_path, 'wb') as f:
        f.write(pdf_bytes)
    print(f"PDF generado: {len(pdf_bytes):,} bytes")
    print(f"Guardado en: {output_path}")

    # Send email
    print("\nEnviando por email a yoandyross@gmail.com...")
    success = send_email_with_pdf(pdf_bytes)
    if success:
        print("EMAIL ENVIADO EXITOSAMENTE!")
    else:
        print("ERROR al enviar el email.")
