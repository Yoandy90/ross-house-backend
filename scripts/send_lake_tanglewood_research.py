"""
One-shot: Generate Lake Tanglewood comprehensive research PDF
and email it to yoandyross@gmail.com via SendGrid.
"""
import os
import base64
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, ListFlowable, ListItem,
)
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition


PDF_PATH = "/tmp/Lake_Tanglewood_Investigacion_Completa.pdf"
RECIPIENT = "yoandyross@gmail.com"


def build_pdf(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.65 * inch, bottomMargin=0.65 * inch,
        title="Lake Tanglewood TX - Investigacion Completa 2026",
        author="Ross House Rentals - Analisis de Mercado",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle("T", parent=styles["Title"], fontSize=20,
                           textColor=colors.HexColor("#0E5AA7"), spaceAfter=4,
                           alignment=1)
    subtitle = ParagraphStyle("ST", parent=styles["Normal"], fontSize=11,
                              textColor=colors.HexColor("#555"), spaceAfter=14,
                              alignment=1, fontName="Helvetica-Oblique")
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=15,
                        textColor=colors.HexColor("#C8102E"),
                        spaceBefore=16, spaceAfter=8,
                        borderColor=colors.HexColor("#C8102E"),
                        borderWidth=0, borderPadding=0)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12,
                        textColor=colors.HexColor("#0E5AA7"),
                        spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("B", parent=styles["Normal"], fontSize=10,
                          leading=14, alignment=TA_JUSTIFY, spaceAfter=4)
    note = ParagraphStyle("N", parent=styles["Normal"], fontSize=9,
                          leading=12, textColor=colors.HexColor("#A14600"),
                          backColor=colors.HexColor("#FFF8E1"),
                          borderColor=colors.HexColor("#F5A623"),
                          borderWidth=1, borderPadding=8,
                          spaceBefore=6, spaceAfter=10)
    pro = ParagraphStyle("Pro", parent=styles["Normal"], fontSize=10,
                         leading=13, textColor=colors.HexColor("#0F7B3E"),
                         leftIndent=14)
    con = ParagraphStyle("Con", parent=styles["Normal"], fontSize=10,
                         leading=13, textColor=colors.HexColor("#9F1239"),
                         leftIndent=14)
    bullet = ParagraphStyle("BU", parent=styles["Normal"], fontSize=10,
                            leading=13, leftIndent=12, spaceAfter=2)

    story = []

    # ═══ COVER ═══
    story.append(Paragraph("Lake Tanglewood, TX", title))
    story.append(Paragraph("Investigacion Inmobiliaria Completa - 2026", subtitle))
    story.append(Paragraph(
        "Analisis preparado para: <b>Yoandy Ross / Ross House Rentals LLC</b><br/>"
        "Fuentes: Census 2020, Zillow, Redfin, Trulia, Realtor.com, Niche, "
        "Randall County, Canyon ISD, laketanglewood.org, datausa.io, Wikipedia.", body
    ))
    story.append(Spacer(1, 10))

    # Quick snapshot
    story.append(Paragraph("Snapshot Ejecutivo", h2))
    snap = [
        ["Metrica", "Valor"],
        ["Tipo de comunidad", "Village incorporado, gated, residencial-resort"],
        ["Ubicacion", "Randall County, ~15-20 min al sur de Amarillo"],
        ["Poblacion (2020 census)", "686 residentes"],
        ["Edad mediana", "60.6 anos (43% mayor de 65)"],
        ["Ingreso mediano del hogar", "$125,179 - $178,335"],
        ["% Propietarios", "96.6%"],
        ["Valor mediano de casa", "~$640,000 - $660,000"],
        ["Precio mediano de lista", "$950,000 - $1,000,000"],
        ["HOA anual", "$3,150 ($262.50/mes)"],
        ["Property tax estimado", "~$11,000/ano sobre $640k"],
        ["Distrito escolar", "Canyon ISD (calificacion alta)"],
        ["Crimen", "Muy bajo, gated con seguridad propia"],
    ]
    t = Table(snap, colWidths=[2.5 * inch, 4.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8F9FA")),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)

    # ═══ UBICACION Y GEOGRAFIA ═══
    story.append(PageBreak())
    story.append(Paragraph("1. Ubicacion y Geografia", h1))
    story.append(Paragraph(
        "Lake Tanglewood se encuentra en el <b>condado de Randall</b>, "
        "aproximadamente 15-20 minutos al sur de Amarillo via la autopista TX-217. "
        "La comunidad esta a solo 10 minutos de uno de los grandes atractivos turisticos "
        "del Panhandle de Texas: el <b>Parque Estatal Palo Duro Canyon</b>, el "
        "segundo canon mas grande de Estados Unidos.", body
    ))
    story.append(Paragraph(
        "A diferencia de un suburbio tradicional, Lake Tanglewood es un \"village\" "
        "oficialmente incorporado y opera como una <b>comunidad cerrada (gated community)</b>. "
        "Fue concebido como una zona resort-residencial donde profesionales y jubilados "
        "de Amarillo construyen sus casas, ya sea como vivienda permanente o como segunda residencia.", body
    ))
    story.append(Paragraph(
        "El lago de unos <b>140 acres</b> es el corazon de la comunidad, formado por una "
        "presa construida entre 1961 y 1962. La zona residencial se concentra en pocas calles "
        "que rodean el lago, lo que da una sensacion de exclusividad y baja densidad.", body
    ))

    # ═══ HISTORIA ═══
    story.append(Paragraph("2. Historia", h1))
    history_items = [
        "<b>1961</b> - Roy Stockton y John Currie inician la construccion de la presa.",
        "<b>1962</b> - Se completa la presa y se llena el lago Tanglewood.",
        "<b>1965</b> - Se forma <b>Lake Tanglewood, Inc.</b> como corporacion de Texas.",
        "<b>1979 y 2003</b> - Reestructuraciones de propiedad/leases.",
        "<b>Hoy</b> - Comunidad consolidada, ~686 residentes, mayoritariamente viviendas permanentes.",
    ]
    bullets = [ListItem(Paragraph(it, body), leftIndent=8, bulletColor=colors.HexColor("#C8102E"))
               for it in history_items]
    story.append(ListFlowable(bullets, bulletType="bullet", start="•"))

    # ═══ DEMOGRAFIA ═══
    story.append(Paragraph("3. Demografia 2026", h1))
    demo_data = [
        ["Metrica", "Valor"],
        ["Poblacion", "686 (Census 2020) / 624-750 estimado 2026"],
        ["Edad mediana", "60.6 anos"],
        ["% mayores de 65", "43%"],
        ["% Blanco no hispano", "89.8%"],
        ["% Hispano / Latino", "6.27%"],
        ["Ingreso mediano del hogar", "$125,179 - $178,335"],
        ["% Propietarios", "96.6%"],
        ["% Ciudadanos USA", "100%"],
        ["Idioma en casa", "100% ingles"],
    ]
    t = Table(demo_data, colWidths=[2.5 * inch, 4.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Perfil del residente tipico:</b> Ejecutivo retirado, medico, abogado, "
        "dueno de negocio exitoso de Amarillo, o profesional de alto rango. "
        "Este NO es un mercado de inquilinos masivo; es un mercado de propietarios.", note
    ))

    # ═══ MERCADO INMOBILIARIO ═══
    story.append(PageBreak())
    story.append(Paragraph("4. Mercado Inmobiliario 2026", h1))

    story.append(Paragraph("Compra de vivienda", h2))
    market_data = [
        ["Fuente", "Valor Mediano", "Precio Venta", "Precio Lista"],
        ["Zillow", "$641,666", "-", "$959,500"],
        ["Trulia", "$654,709", "-", "-"],
        ["Redfin", "-", "$607,136", "-"],
        ["Realtor.com", "-", "-", "$990,000"],
    ]
    t = Table(market_data, colWidths=[1.7 * inch, 1.7 * inch, 1.7 * inch, 1.8 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<b>Realidad del mercado:</b> Valor real promedio entre $640k-$660k, pero "
        "los precios de lista estan tipicamente entre $950k-$1M. "
        "Inventario actual: ~22 casas en venta. Volumen de ventas muy bajo, "
        "lo que causa volatilidad mes a mes.", body
    ))

    story.append(Paragraph("Renta a largo plazo", h2))
    story.append(Paragraph(
        "No existe estadistica oficial confiable porque hay muy pocos inquilinos en la "
        "comunidad (96.6% son propietarios). Por comparacion con casas similares en Amarillo "
        "(viviendas de $600k+), la renta estimada estaria entre <b>$3,000 y $5,500 al mes</b>.", body
    ))

    story.append(Paragraph("Renta corta (Airbnb / vacacional)", h2))
    story.append(Paragraph(
        "Cabanas en plataformas como Orbitz se listan entre <b>$120 y $451 por noche</b>. "
        "IMPORTANTE: Las rentas cortas en Lake Tanglewood requieren <b>aprobacion del Board</b> "
        "de la comunidad. No es un mercado de Airbnb libre. Algunas comunidades vecinas "
        "como Tanglewood Shores prohiben totalmente las rentas cortas, lo que reduce la "
        "competencia y aumenta margenes para quienes consiguen aprobacion.", body
    ))

    # ═══ HOA / POA ═══
    story.append(PageBreak())
    story.append(Paragraph("5. HOA / POA - Cuotas de la Comunidad", h1))
    hoa_data = [
        ["Concepto", "Costo"],
        ["HOA anual por lote", "$3,150 / ano"],
        ["Equivalente mensual", "~$262.50 / mes"],
        ["Cuotas mensuales adicionales (fee sheet)", "~$158 / mes"],
        ["Cargos por venta/transferencia", "Adicionales (consultar contrato)"],
        ["Cargo especifico por Airbnb", "No existe, pero requiere aprobacion del Board"],
    ]
    t = Table(hoa_data, colWidths=[3.5 * inch, 3.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph("<b>Lo que cubre la HOA:</b>", body))
    coverage = [
        "Mantenimiento del lago",
        "Acceso al lago (esqui acuatico, pesca, kayak, jet ski)",
        "Caminos privados",
        "Seguridad y control del gate",
        "Areas comunes / parques",
        "Posiblemente club house",
    ]
    bullets = [ListItem(Paragraph(it, body), leftIndent=8) for it in coverage]
    story.append(ListFlowable(bullets, bulletType="bullet", start="•"))

    # ═══ ESCUELAS ═══
    story.append(Paragraph("6. Escuelas", h1))
    story.append(Paragraph(
        "Lake Tanglewood pertenece al <b>distrito escolar Canyon ISD</b>, "
        "uno de los mejores del Panhandle de Texas. Las calificaciones son altas "
        "y atrae a familias profesionales. El calendario escolar 2026-2027 inicia "
        "el <b>19 de agosto de 2026</b>. La tasa fiscal escolar es de $1.233 por cada "
        "$100 de valor (bajo de $1.28 en un ano reciente). Escuelas relevantes: "
        "Canyon High School y Canyon Junior High.", body
    ))

    # ═══ TAXES ═══
    story.append(Paragraph("7. Impuestos a la Propiedad", h1))
    tax_data = [
        ["Entidad", "Tasa por $100", "En casa de $640k"],
        ["Randall County", "0.40099", "~$2,566 / ano"],
        ["Canyon ISD", "1.233", "~$7,891 / ano"],
        ["Lake Tanglewood Village", "(variable)", "~$500 - $1,500 / ano"],
        ["TOTAL ESTIMADO", "~1.7%", "~$11,000 / ano"],
    ]
    t = Table(tax_data, colWidths=[2.3 * inch, 2.2 * inch, 2.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("BACKGROUND", (0, 4), (-1, 4), colors.HexColor("#FFE082")),
        ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Recuerda: Texas no tiene state income tax, pero los property taxes son "
        "comparativamente altos. Para una casa de $640k, espera pagar "
        "<b>~$11,000 al ano en impuestos a la propiedad</b>.", body
    ))

    # ═══ CRIMEN ═══
    story.append(Paragraph("8. Seguridad y Crimen", h1))
    story.append(Paragraph(
        "Lake Tanglewood es una <b>comunidad cerrada con control de acceso</b>, "
        "patrullaje propio y respaldo del Randall County Sheriff. Randall County en general "
        "tiene mas crimen de propiedad que violento, y es significativamente mas seguro "
        "que Potter County (al norte de Amarillo). Resenas residentes describen la zona "
        "como \"muy segura, gated, presencia policial visible\".", body
    ))
    story.append(Paragraph(
        "Es de las zonas mas seguras del Panhandle de Texas.", note
    ))

    # ═══ AMENIDADES ═══
    story.append(Paragraph("9. Amenidades", h1))
    amenities = [
        "Lago de ~140 acres (esqui acuatico, jet ski, pesca, kayak)",
        "Pesca de lubina, bagre, crappie",
        "Areas de playa privadas",
        "Marina y muelles privados (algunas casas con muelle propio)",
        "Cerca de Palo Duro Canyon State Park (atractivo turistico)",
        "Country clubs en Amarillo a 15 minutos",
        "Hospitales y restaurantes de Amarillo accesibles",
        "Vida silvestre abundante (venados, aves)",
    ]
    bullets = [ListItem(Paragraph(it, body), leftIndent=8) for it in amenities]
    story.append(ListFlowable(bullets, bulletType="bullet", start="•"))

    # ═══ PROS Y CONTRAS ═══
    story.append(PageBreak())
    story.append(Paragraph("10. Analisis Pros y Contras para un Inversor", h1))

    story.append(Paragraph("PROS", h2))
    pros = [
        "Mercado de alto valor con potencial solido de appreciation",
        "Comunidad cerrada y segura, mantiene su valor en el tiempo",
        "Cerca de Palo Duro Canyon - atractivo turistico para Airbnb",
        "Canyon ISD - escuelas excelentes que atraen familias",
        "Inventario bajo (~22 casas) - poca competencia",
        "Texas no tiene state income tax",
        "Demografia estable: jubilados con poder adquisitivo",
    ]
    for p in pros:
        story.append(Paragraph(f"✓ {p}", pro))

    story.append(Paragraph("CONTRAS", h2))
    cons = [
        "Precios de entrada altos (minimo $600k)",
        "HOA significativo: $3,150/ano + extras",
        "Property tax alto: ~$11k/ano",
        "Mercado de renta limitado (96.6% son propietarios)",
        "Airbnb requiere aprobacion del Board - no es libre",
        "Volatilidad de precios por bajo volumen de ventas",
        "Demografia mayor - menos demanda de renta a largo plazo",
        "Mantenimiento mas caro (lake-front = mas exposicion a clima)",
    ]
    for c in cons:
        story.append(Paragraph(f"x {c}", con))

    # ═══ COMPARACION DUMAS ═══
    story.append(PageBreak())
    story.append(Paragraph("11. Comparacion: Lake Tanglewood vs Dumas (tus propiedades)", h1))
    comp_data = [
        ["Metrica", "Lake Tanglewood", "Dumas"],
        ["Valor mediano de casa", "~$640,000", "~$110k-180k"],
        ["Renta mensual potencial", "$3,000 - $5,500", "$900 - $1,400"],
        ["HOA", "$3,150/ano + extras", "$0"],
        ["Property tax anual", "~$11,000", "~$2,500"],
        ["Demanda de renta", "Muy baja", "Alta"],
        ["Tipo de inquilino", "Profesional alto + Airbnb", "Working class, familias"],
        ["Velocidad de ocupacion", "Lenta", "Rapida"],
        ["Cash-on-cash return", "Bajo (5-7%)", "Alto (10-15%)"],
        ["Appreciation potencial", "Alto", "Moderado"],
        ["Nivel de crimen", "Muy bajo", "Moderado"],
        ["Escuelas", "Excelentes (Canyon ISD)", "Buenas"],
    ]
    t = Table(comp_data, colWidths=[2.2 * inch, 2.5 * inch, 2.2 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8F9FA")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#FFFFFF"), colors.HexColor("#F0F4F8")]),
    ]))
    story.append(t)

    # ═══ RECOMENDACIONES ═══
    story.append(Paragraph("12. Recomendacion Estrategica", h1))
    story.append(Paragraph(
        "<b>Si tu objetivo es cash flow rapido</b> -> Lake Tanglewood NO es ideal. "
        "Sigue con Dumas y mercados similares de medio-bajo precio.", body
    ))
    story.append(Paragraph(
        "<b>Si buscas appreciation a largo plazo + estatus</b> -> Lake Tanglewood SI "
        "tiene sentido como inversion patrimonial.", body
    ))
    story.append(Paragraph(
        "<b>Si quieres diversificar tu portafolio</b> -> El mix ideal podria ser "
        "1 propiedad en Lake Tanglewood + 5-10 en Dumas: appreciation premium + cash flow.", body
    ))
    story.append(Paragraph(
        "<b>Si te interesa Airbnb premium</b> -> Es posible pero primero debes obtener "
        "aprobacion del Board de la comunidad.", body
    ))
    story.append(Paragraph(
        "<b>Si lo consideras como segunda casa o jubilacion</b> -> Excelente opcion "
        "por seguridad, ambiente, escuelas y proximidad a Amarillo.", body
    ))

    story.append(Paragraph("Idea: House Hack en Lake Tanglewood", h2))
    story.append(Paragraph(
        "Una estrategia interesante para ti:", body
    ))
    hack = [
        "Comprar 1 casa en Lake Tanglewood ($600k-$700k)",
        "Vivir TU ahi (mudar tu residencia personal de Dumas a Lake Tanglewood)",
        "Vender o rentar tu casa personal actual",
        "Mantener Casas #2 y #3 de Dumas como rentas (alto cash flow)",
    ]
    bullets = [ListItem(Paragraph(it, body), leftIndent=8) for it in hack]
    story.append(ListFlowable(bullets, bulletType="1", start="1"))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<b>Resultado:</b> Mejor calidad de vida personal + cash flow de Dumas paga "
        "el HOA de Lake Tanglewood + doble appreciation (Lake Tanglewood + tus rentas) "
        "+ mejores escuelas si tienes hijos + mayor seguridad.", body
    ))

    # Footer
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "<i>Documento generado por Ross House Rentals platform - Analisis basado en datos "
        "publicos disponibles en junio 2026. Los precios y datos del mercado pueden cambiar; "
        "verifica con un agente de bienes raices local antes de tomar decisiones de compra.</i>", note
    ))

    doc.build(story)


def send_email(pdf_path: str, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY missing")

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    html = """
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto;">
      <h2 style="color: #0E5AA7;">Lake Tanglewood, TX - Investigacion Completa 2026</h2>
      <p>Hola Yoandy,</p>
      <p>Adjunto encontraras el <b>analisis inmobiliario completo</b> de Lake Tanglewood, TX
      que solicitaste. El PDF incluye:</p>
      <ul>
        <li>Snapshot ejecutivo con todas las metricas clave</li>
        <li>Ubicacion, geografia e historia de la comunidad</li>
        <li>Demografia detallada (poblacion, edad, ingresos)</li>
        <li>Mercado inmobiliario 2026 (compra, renta larga, Airbnb)</li>
        <li>HOA / POA: cuotas y que cubren</li>
        <li>Escuelas (Canyon ISD)</li>
        <li>Impuestos a la propiedad (estimacion para casa $640k)</li>
        <li>Crimen y seguridad</li>
        <li>Amenidades y atractivos turisticos</li>
        <li>Analisis Pros vs Contras para inversores</li>
        <li>Comparacion lado a lado: Lake Tanglewood vs Dumas (tus propiedades)</li>
        <li>Recomendacion estrategica + idea de \"House Hack\"</li>
      </ul>
      <p style="background:#E8F5E9;padding:12px;border-left:4px solid #10B981;border-radius:4px;">
        <b>Insight clave:</b> Lake Tanglewood es appreciation premium pero bajo cash flow.
        Dumas es alto cash flow pero appreciation moderado. El mix ideal podria ser
        1 propiedad en Lake Tanglewood + 5-10 en Dumas.
      </p>
      <p>Saludos,<br/>Equipo Ross House Rentals</p>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="Lake Tanglewood TX - Investigacion Inmobiliaria Completa 2026 (PDF)",
        html_content=html,
    )
    message.attachment = Attachment(
        FileContent(encoded),
        FileName("Lake_Tanglewood_TX_Investigacion_2026.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    sg = SendGridAPIClient(api_key)
    res = sg.send(message)
    print(f"SendGrid: {res.status_code} -> {recipient}")


if __name__ == "__main__":
    print("Generating PDF...")
    build_pdf(PDF_PATH)
    print(f"PDF: {PDF_PATH} ({os.path.getsize(PDF_PATH)} bytes)")
    print(f"Sending to {RECIPIENT}...")
    send_email(PDF_PATH, RECIPIENT)
    print("Done!")
