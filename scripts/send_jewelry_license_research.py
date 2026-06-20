"""
Jewelry Store License Research — Dumas, TX (Moore County) — 2026
==================================================================
Investigación completa para Alfredo Hernández:
  - Federal: FinCEN AML 31 CFR 1027, IRS EIN, BOI Report
  - Estatal Texas: Sales Tax Permit, CPMD (OCCC), Weights & Measures (TDA),
    Pawn Shop License (si presta), Secretary of State (LLC/DBA)
  - Local Dumas/Moore County: Certificate of Occupancy, Zoning, Alarm Permit
  - Estructura legal (LLC vs Sole Prop) + EIN
  - Seguros: Jeweler's Block, General Liability, Workers Comp
  - Costos estimados de apertura
  - Checklist paso a paso 12 semanas
  - Contactos clave con teléfonos
"""
import os, base64
from datetime import datetime
from dotenv import load_dotenv
load_dotenv("/app/ross-house-backend/.env")

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, PageBreak, KeepTogether)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT

OUT = "/tmp/jewelry/Jewelry_Store_License_Research_Dumas_TX.pdf"
os.makedirs("/tmp/jewelry", exist_ok=True)

# Brand colors — Gold/Premium theme
GOLD = colors.HexColor("#B8860B"); GOLD_L = colors.HexColor("#FEF3C7")
DARK = colors.HexColor("#0F172A"); GRAY = colors.HexColor("#6B7280")
GREEN = colors.HexColor("#059669"); GREEN_L = colors.HexColor("#ECFDF5")
RED = colors.HexColor("#DC2626"); RED_L = colors.HexColor("#FEE2E2")
BLUE = colors.HexColor("#1E40AF"); BLUE_L = colors.HexColor("#DBEAFE")
BG = colors.HexColor("#FAFAFA")
LIGHT = colors.HexColor("#F3F4F6")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=24, textColor=DARK, spaceAfter=4, leading=28)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=15, textColor=GOLD, spaceAfter=10, leading=18, fontName="Helvetica-Bold")
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=11, textColor=DARK, spaceAfter=4, leading=14, fontName="Helvetica-Bold")
BODY = ParagraphStyle("Body", parent=ss["Normal"], fontSize=10, textColor=DARK, leading=14, spaceAfter=6, alignment=TA_JUSTIFY)
BODY_L = ParagraphStyle("BodyL", parent=BODY, alignment=TA_LEFT)
BODY_C = ParagraphStyle("BodyC", parent=BODY, alignment=TA_CENTER)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, textColor=GRAY, leading=11)
LBL = ParagraphStyle("Lbl", parent=BODY, fontSize=8, textColor=GOLD, fontName="Helvetica-Bold", spaceAfter=2)

elems = []

# ═══ COVER ═══════════════════════════════════════════════════════════
elems += [
    Spacer(1, 0.5*inch),
    Paragraph('<font color="#B8860B"><b>R</b>OSS HOUSE RENTALS · BUSINESS ADVISORY</font>',
              ParagraphStyle("logo", parent=BODY_C, fontSize=11, textColor=GOLD, fontName="Helvetica-Bold")),
    Spacer(1, 0.5*inch),
    Paragraph("LICENCIAS PARA TIENDA DE JOYERÍA DE ORO",
              ParagraphStyle("title", parent=H1, fontSize=26, alignment=TA_CENTER, leading=30, textColor=DARK)),
    Spacer(1, 0.1*inch),
    Paragraph('<font color="#B8860B">DUMAS, TEXAS — MOORE COUNTY · 2026</font>',
              ParagraphStyle("sub", parent=BODY_C, fontSize=14, textColor=GOLD, fontName="Helvetica-Bold")),
    Spacer(1, 0.35*inch),
    Paragraph("Investigación profesional para",
              ParagraphStyle("for", parent=BODY_C, fontSize=10, textColor=GRAY)),
    Paragraph("<b>Alfredo Hernández</b>",
              ParagraphStyle("name", parent=BODY_C, fontSize=18, textColor=DARK, leading=22, fontName="Helvetica-Bold")),
    Spacer(1, 0.5*inch),
]

# Cover panel — scope
scope = Table([
    [Paragraph("<font color='#B8860B' size=10><b>ALCANCE DEL REPORTE</b></font>", BODY_C)],
    [Paragraph(
        "Este reporte cubre <b>TODOS</b> los escenarios operacionales:<br/><br/>"
        "✅ <b>VENTA</b> de joyería nueva (oro 10K/14K/18K/24K, plata, gemas)<br/>"
        "✅ <b>COMPRA</b> de oro/joyería usada y chatarra (scrap)<br/>"
        "✅ <b>PRÉSTAMOS</b> con joyas como colateral (pawn shop)<br/><br/>"
        "<font color='#6B7280' size=9><i>Las licencias aplicables dependen del modelo final que elija Alfredo.</i></font>",
        BODY_L)],
], colWidths=[6.4*inch])
scope.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),GOLD),
    ("BACKGROUND",(0,1),(-1,1),GOLD_L),
    ("LEFTPADDING",(0,0),(-1,-1),16),
    ("RIGHTPADDING",(0,0),(-1,-1),16),
    ("TOPPADDING",(0,0),(-1,-1),12),
    ("BOTTOMPADDING",(0,0),(-1,-1),12),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
]))
elems.append(scope)

elems += [
    Spacer(1, 0.6*inch),
    Paragraph(
        f"<font color='#6B7280' size=9>Generado: {datetime.now().strftime('%B %d, %Y')}  ·  Fuentes: "
        "FinCEN, IRS, Texas Comptroller, OCCC, TDA, Secretary of State, City of Dumas, "
        "Moore County, NFIB 2026 Guide, Texas Jewelers Association</font>",
        BODY_C),
]

elems.append(PageBreak())

# ═══ EXECUTIVE SUMMARY ════════════════════════════════════════════════
elems.append(Paragraph("RESUMEN EJECUTIVO", H1))
elems.append(Spacer(1, 0.1*inch))
elems.append(Paragraph(
    "Abrir una joyería de oro en Dumas, TX requiere <b>3 niveles de cumplimiento</b>: "
    "federal, estatal y local. El error más común es asumir que basta una sola licencia. "
    "A continuación el camino mínimo viable y los <b>costos totales estimados</b>:",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# Cost summary table
cost_data = [
    [Paragraph("<font color='#FFFFFF'><b>CATEGORÍA</b></font>", BODY),
     Paragraph("<font color='#FFFFFF'><b>COSTO INICIAL</b></font>", BODY_C),
     Paragraph("<font color='#FFFFFF'><b>RENOVACIÓN</b></font>", BODY_C)],
    [Paragraph("<b>Federal</b><br/>EIN, FinCEN AML, BOI Report", BODY),
     Paragraph("<b>$0</b> (todos gratis)", BODY_C),
     Paragraph("BOI cada update", BODY_C)],
    [Paragraph("<b>Texas Estatal</b><br/>Sales Tax + CPMD + Weights & Measures", BODY),
     Paragraph("<b>~$165</b>", BODY_C),
     Paragraph("$70/año (CPMD)", BODY_C)],
    [Paragraph("<b>LLC + DBA</b><br/>Secretary of State", BODY),
     Paragraph("<b>$325</b>", BODY_C),
     Paragraph("$0 (sin renew)", BODY_C)],
    [Paragraph("<b>Local Dumas</b><br/>CO + Zoning + Alarm + Sign", BODY),
     Paragraph("<b>$150-400</b>", BODY_C),
     Paragraph("$25-50/año alarm", BODY_C)],
    [Paragraph("<b>Pawn License (OPCIONAL)</b><br/>OCCC si presta dinero", BODY),
     Paragraph("<b>$685</b>", BODY_C),
     Paragraph("$685/año", BODY_C)],
    [Paragraph("<b>Seguros año 1</b><br/>Jewelers Block + GL + Workers Comp", BODY),
     Paragraph("<b>$3,500-6,000</b>", BODY_C),
     Paragraph("Sube 5-15%/año", BODY_C)],
    [Paragraph("<b>TOTAL ESTIMADO LICENCIAS + SEGUROS</b>", BODY),
     Paragraph("<b><font color='#B8860B'>$4,140-7,575</font></b>", BODY_C),
     Paragraph("$3,580-6,135", BODY_C)],
]
t = Table(cost_data, colWidths=[2.7*inch, 1.85*inch, 1.85*inch])
t.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),GOLD),
    ("BACKGROUND",(0,-1),(-1,-1),GOLD_L),
    ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("LEFTPADDING",(0,0),(-1,-1),8),
    ("RIGHTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),8),
    ("BOTTOMPADDING",(0,0),(-1,-1),8),
    ("BOX",(0,0),(-1,-1),0.5,GRAY),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
]))
elems.append(t)

elems.append(Spacer(1, 0.2*inch))
elems.append(Paragraph(
    "<b>⚠️ Nota crítica:</b> Si Alfredo planea <b>comprar oro/joyería usada</b> al público "
    "(scrap, oro viejo, herencias), la <b>licencia CPMD del OCCC es OBLIGATORIA</b> por ley estatal "
    "(Texas Occupations Code Ch. 1956). Sin ella, comprar oro usado es <b>delito penal de Clase A</b> "
    "(hasta 1 año cárcel + $4,000 multa por cada transacción).",
    ParagraphStyle("warn", parent=BODY, backColor=RED_L, borderColor=RED, borderWidth=1,
                    borderPadding=8, leftIndent=4, rightIndent=4, spaceAfter=8)))

elems.append(PageBreak())

# ═══ SECCIÓN 1: FEDERAL ═══════════════════════════════════════════════
elems.append(Paragraph("1️⃣  REQUISITOS FEDERALES", H1))
elems.append(Spacer(1, 0.1*inch))

# 1.1 EIN
elems.append(Paragraph("1.1  EIN (Employer Identification Number)", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Número de identificación fiscal de la empresa (equivalente al SSN para negocios). "
    "Lo necesitas para abrir cuenta bancaria comercial, contratar empleados, y todas las demás licencias.<br/><br/>"
    "<b>Cómo obtenerlo:</b> Online gratis en <b>irs.gov/ein</b> — toma 10 minutos, lo recibes "
    "inmediatamente en PDF.<br/>"
    "<b>Costo:</b> <font color='#059669'><b>$0 (GRATIS)</b></font><br/>"
    "<b>Tiempo:</b> 10 minutos online<br/>"
    "<b>Requisitos:</b> SSN o ITIN del dueño, dirección, tipo de entidad (LLC recomendado)",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 1.2 FinCEN AML
elems.append(Paragraph("1.2  FinCEN AML — Programa Anti-Lavado (31 CFR 1027)", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Si tu joyería tiene compras O ventas de metales/piedras/joyas con valor >50% en "
    "materiales preciosos por más de <b>$50,000/año</b> (umbral activado el primer año en el que "
    "se cruza), eres clasificado como <b>'Dealer in Precious Metals, Stones, or Jewels'</b> y "
    "DEBES implementar un programa AML escrito.<br/><br/>"
    "<b>5 componentes obligatorios del programa AML:</b>",
    BODY))

aml_data = [
    [Paragraph("<b>1. Programa escrito</b>", BODY),
     Paragraph("Documento aprobado por gerencia: políticas y procedimientos por escrito", BODY)],
    [Paragraph("<b>2. Compliance Officer</b>", BODY),
     Paragraph("Designar persona responsable del cumplimiento (puede ser el dueño)", BODY)],
    [Paragraph("<b>3. Controles internos</b>", BODY),
     Paragraph("Identificación del cliente, due diligence, reportes de transacciones sospechosas (SAR)", BODY)],
    [Paragraph("<b>4. Entrenamiento</b>", BODY),
     Paragraph("Capacitar a TODOS los empleados — anual o cuando cambien regulaciones", BODY)],
    [Paragraph("<b>5. Independent testing</b>", BODY),
     Paragraph("Auditoría interna o externa cada 12-18 meses para validar el programa", BODY)],
]
t2 = Table(aml_data, colWidths=[1.7*inch, 4.7*inch])
t2.setStyle(TableStyle([
    ("VALIGN",(0,0),(-1,-1),"TOP"),
    ("BACKGROUND",(0,0),(0,-1),GOLD_L),
    ("LEFTPADDING",(0,0),(-1,-1),8),
    ("RIGHTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GRAY),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
]))
elems.append(t2)

elems.append(Spacer(1, 0.1*inch))
elems.append(Paragraph(
    "<b>Costo:</b> <font color='#059669'><b>$0 federal</b></font> (no hay fee de registro), pero "
    "el desarrollo del programa AML cuesta <b>$500-2,500</b> si lo haces con consultor.<br/>"
    "<b>Multas por NO cumplir:</b> $25,000 a $250,000 por violación + posible cárcel.<br/>"
    "<b>Reporting:</b> SAR (Suspicious Activity Report) en cualquier transacción "
    "sospechosa &gt;$3,000. Cash &gt;$10,000 dispara reporte automático (Form 8300 al IRS).",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 1.3 BOI Report
elems.append(Paragraph("1.3  BOI Report — Beneficial Ownership Information", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Desde 2024, FinCEN exige que TODAS las LLC/Corp registradas reporten "
    "los beneficial owners (personas con &gt;25% propiedad o control). Es una sola vez, online.<br/><br/>"
    "<b>Cómo:</b> <b>fincen.gov/boi</b> — formulario online ~15 min<br/>"
    "<b>Costo:</b> <font color='#059669'><b>$0 GRATIS</b></font><br/>"
    "<b>Plazo:</b> 30 días después de formar la LLC<br/>"
    "<b>Multa por NO reportar:</b> $591/día (hasta $10,000) + posible cárcel 2 años",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 1.4 Form 8300
elems.append(Paragraph("1.4  IRS Form 8300 — Cash Transaction Reports", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Cualquier transacción en efectivo &gt;$10,000 (o serie de transacciones "
    "relacionadas que sumen ese monto en 12 meses) debe reportarse al IRS en 15 días.<br/><br/>"
    "<b>Costo:</b> $0 (es un reporte obligatorio gratis)<br/>"
    "<b>Multa por NO reportar:</b> $290 por formulario + posibles cargos penales si es intencional",
    BODY))

elems.append(PageBreak())

# ═══ SECCIÓN 2: ESTATAL TEXAS ═════════════════════════════════════════
elems.append(Paragraph("2️⃣  REQUISITOS ESTATALES (TEXAS)", H1))
elems.append(Spacer(1, 0.1*inch))

# 2.1 Sales Tax Permit
elems.append(Paragraph("2.1  Sales & Use Tax Permit (Texas Comptroller)", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Permiso OBLIGATORIO para cobrar impuesto sobre ventas (Texas: 6.25% estatal + "
    "hasta 2% local = <b>8.25% en Dumas</b>).<br/><br/>"
    "<b>Dónde:</b> <b>comptroller.texas.gov</b> &gt; Apply for a Permit &gt; Sales Tax<br/>"
    "<b>Costo:</b> <font color='#059669'><b>$0 GRATIS</b></font><br/>"
    "<b>Tiempo:</b> 2-3 semanas (puedes empezar a operar mientras esperas)<br/>"
    "<b>Filing:</b> Mensual, trimestral o anual según volumen de ventas<br/>"
    "<b>IMPORTANTE:</b> Joyería en TX NO es exenta de sales tax (a diferencia de bullion puro). "
    "Anillos, cadenas, dijes — todos pagan 8.25%. Solo bullion (.999+ pureza, sin diseño) está exento.",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 2.2 CPMD
elems.append(Paragraph("2.2  ⭐ Crafted Precious Metal Dealer (CPMD) — OCCC", H2))
elems.append(Paragraph(
    "<font color='#DC2626'><b>CRÍTICO si compras oro/joyería usada al público.</b></font> "
    "Esta es la licencia que más se olvida y la que más multas genera en Texas.<br/><br/>"
    "<b>Qué cubre:</b> Comprar joyas viejas, oro de segunda mano, plata vieja, gemas usadas, scrap, "
    "estate jewelry. <b>NO cubre</b> compra de bullion/coins nuevos.<br/><br/>"
    "<b>Autoridad:</b> Office of Consumer Credit Commissioner (OCCC) vía portal ALECS<br/>"
    "<b>Web:</b> <b>occc.texas.gov/industry/cpmd/</b><br/>"
    "<b>Costo:</b> <b>$70/año</b> (registración + renovación anual)<br/>"
    "<b>Tiempo:</b> ~2 semanas de procesamiento<br/><br/>"
    "<b>Lo que debes entregar al estado por cada compra:</b><br/>"
    "&nbsp;&nbsp;• <b>ID</b> con foto del vendedor (copia)<br/>"
    "&nbsp;&nbsp;• <b>Descripción detallada</b> de cada pieza (peso, kilataje, gemas)<br/>"
    "&nbsp;&nbsp;• <b>Foto</b> de la pieza antes de fundirla/revenderla<br/>"
    "&nbsp;&nbsp;• <b>Hold period:</b> 7 días antes de poder revender/fundir (anti-robo)<br/>"
    "&nbsp;&nbsp;• <b>Reporte al DPS</b> de transacciones (algunos casos)<br/><br/>"
    "<b>⚠️ Sin esta licencia, comprar oro usado = DELITO PENAL Clase A en TX.</b>",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 2.3 Weights & Measures
elems.append(Paragraph("2.3  Weights & Measures Registration (TDA)", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Si usas una BÁSCULA para pesar oro/plata en tus transacciones, "
    "Texas Department of Agriculture (TDA) requiere que la registres y la inspecciones anualmente "
    "para validar que mide correctamente.<br/><br/>"
    "<b>Web:</b> <b>texasagriculture.gov/regulatoryprograms/weightsmeasures</b><br/>"
    "<b>Costo:</b> <b>$25-95/año</b> por báscula registrada (depende del tipo)<br/>"
    "<b>Inspección:</b> Anual, generalmente sin cita previa<br/>"
    "<b>Importancia:</b> Una báscula 'descalibrada' aunque sea por accidente puede costarte "
    "multas $500-$2,500 por unidad. Calibra cada 6 meses con un service certificado.",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 2.4 LLC formation
elems.append(Paragraph("2.4  LLC + DBA — Secretary of State", H2))
elems.append(Paragraph(
    "<b>Recomendación firme: forma una LLC</b>, no operes como sole proprietor. "
    "Protege tu casa/auto de demandas, y luce profesional ante bancos y aseguradoras.<br/><br/>"
    "<b>Cómo:</b> <b>sos.state.tx.us</b> &gt; SOSDirect &gt; File Form 205 (Certificate of Formation)<br/>"
    "<b>Costo LLC:</b> <b>$300</b> (formación, una sola vez)<br/>"
    "<b>Costo DBA</b> (si operas con nombre comercial diferente del legal): <b>$25</b> + filing local "
    "en Moore County Clerk<br/>"
    "<b>Tiempo:</b> 1-3 días business online<br/>"
    "<b>Texas Franchise Tax:</b> Reportar anualmente al Comptroller. <b>$0 a pagar</b> si "
    "ingresos &lt;$2.47M/año (umbral 2026), pero ES OBLIGATORIO presentar el reporte.<br/>"
    "<b>Registered Agent:</b> Necesitas uno con dirección física en TX. Tu propia dirección sirve "
    "($0) o servicio profesional ($100-300/año).",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 2.5 Pawn License (optional)
elems.append(Paragraph("2.5  Pawn Shop License (SOLO si presta dinero) — OCCC", H2))
elems.append(Paragraph(
    "<b>Aplica si:</b> Alfredo presta dinero usando joyas como colateral (te dejan el anillo, "
    "les das $200, vuelven en 30 días con $230). Si solo COMPRA oro y luego lo revende = NO necesita "
    "pawn license, solo CPMD.<br/><br/>"
    "<b>Autoridad:</b> Office of Consumer Credit Commissioner (OCCC) — más estricto que CPMD<br/>"
    "<b>Web:</b> <b>occc.texas.gov/industry/pawnshops</b><br/>"
    "<b>Costo:</b> <b>$685/año</b> (licencia + renovación)<br/>"
    "<b>Requisitos extra:</b><br/>"
    "&nbsp;&nbsp;• <b>Net Assets mínimo</b> de $150,000 (capital + inventario)<br/>"
    "&nbsp;&nbsp;• <b>Background check</b> del dueño (sin felonías recientes)<br/>"
    "&nbsp;&nbsp;• <b>Surety Bond</b> de $5,000<br/>"
    "&nbsp;&nbsp;• Reporting a TX DPS de toda transacción (anti-robo)<br/>"
    "&nbsp;&nbsp;• Hold period de 30 días antes de vender colateral perdido<br/><br/>"
    "<b>Recomendación:</b> Si Alfredo no tiene $150K en capital líquido, mejor NO entrar a pawn al "
    "inicio. Empezar como joyería + CPMD, luego agregar pawn en año 2 cuando haya capital.",
    BODY))

elems.append(PageBreak())

# ═══ SECCIÓN 3: LOCAL DUMAS ═══════════════════════════════════════════
elems.append(Paragraph("3️⃣  REQUISITOS LOCALES (DUMAS / MOORE COUNTY)", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph(
    "<b>Dato clave:</b> Texas <b>NO tiene licencia comercial estatal general</b>. Los requisitos "
    "locales varían por ciudad. La ciudad de Dumas <b>no publica una lista pública</b> de licencias, "
    "lo que significa: <b>HAY QUE LLAMAR antes de firmar el contrato del local.</b>",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 3.1 Certificate of Occupancy
elems.append(Paragraph("3.1  Certificate of Occupancy (CO) — City of Dumas", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> El edificio físico debe ser aprobado por el city building official ANTES de "
    "abrir el negocio. Verifica que el espacio cumpla con códigos de construcción, electricidad, "
    "salidas de emergencia, baños, y zoning.<br/><br/>"
    "<b>Dónde:</b> City Hall de Dumas — <b>124 W. 6th Street, Dumas, TX 79029</b><br/>"
    "<b>Teléfono:</b> <b>(806) 935-4101</b><br/>"
    "<b>Costo:</b> <b>$50-200</b> (varía por tamaño del local)<br/>"
    "<b>Tiempo:</b> 1-3 semanas (inspección física requerida)<br/>"
    "<b>IMPORTANTE:</b> Si el local antes era restaurante/oficina y va a ser joyería, "
    "es <b>cambio de uso</b> y necesita nueva CO. Si era joyería antes, transferencia es más rápida.",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 3.2 Zoning
elems.append(Paragraph("3.2  Zoning Approval — Verificación de Uso de Suelo", H2))
elems.append(Paragraph(
    "<b>Qué es:</b> Confirmar que el lote permite uso comercial 'retail'. La mayoría de la "
    "<b>Dumas Avenue</b> (calle principal) y <b>Hwy 287</b> están zonificadas C-1 o C-2 (commercial), "
    "pero zonas residenciales o industriales NO permiten joyería.<br/><br/>"
    "<b>Dónde:</b> Dumas Planning Department — mismo City Hall<br/>"
    "<b>Costo:</b> Usualmente <b>$0-50</b> (consulta gratis, dictamen formal con fee)<br/>"
    "<b>Acción crítica:</b> ANTES de firmar lease, lleva la dirección al City Hall y pide "
    "verificación escrita de zoning. <b>No confíes solo en lo que dice el landlord.</b>",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 3.3 Sign Permit
elems.append(Paragraph("3.3  Sign Permit — Letrero Comercial", H2))
elems.append(Paragraph(
    "<b>Qué cubre:</b> Letreros exteriores, luminosos, banners. Dumas tiene reglas de tamaño "
    "máximo (sq ft según el frente del local) y restricciones de luces LED brillantes.<br/><br/>"
    "<b>Costo:</b> <b>$50-150</b> (una sola vez por letrero)<br/>"
    "<b>Tip:</b> Si el letrero anterior del local sigue ahí, pide al city que lo reapruebe (más barato).",
    BODY))

elems.append(Spacer(1, 0.15*inch))

# 3.4 Alarm Permit
elems.append(Paragraph("3.4  ⭐ Alarm Permit + Burglar Alarm System (CRÍTICO)", H2))
elems.append(Paragraph(
    "<b>Por qué crítico:</b> Una joyería de oro es target #1 de robos. Aseguradoras "
    "<b>NO te van a vender Jewelers Block insurance</b> sin sistema de alarma certificado UL Listed "
    "+ permit municipal.<br/><br/>"
    "<b>Sistema requerido por Jewelers Block:</b><br/>"
    "&nbsp;&nbsp;• Central station monitoring 24/7 (UL Listed Grade A o B)<br/>"
    "&nbsp;&nbsp;• Cámaras de alta resolución (1080p+) con backup en cloud<br/>"
    "&nbsp;&nbsp;• Caja fuerte TL-15 o TL-30 (rated por Underwriters Laboratories)<br/>"
    "&nbsp;&nbsp;• Vitrinas con vidrio templado o policarbonato<br/>"
    "&nbsp;&nbsp;• Sensores de movimiento + glass break + smoke<br/><br/>"
    "<b>Permit municipal Dumas:</b> $25-50/año (registro con City Police para que respondan "
    "a alarmas).<br/>"
    "<b>Costo total sistema de seguridad:</b> <b>$3,000-8,000</b> instalación + $40-80/mes monitoring",
    BODY))

elems.append(PageBreak())

# ═══ SECCIÓN 4: INSURANCE ═════════════════════════════════════════════
elems.append(Paragraph("4️⃣  SEGUROS REQUERIDOS", H1))
elems.append(Spacer(1, 0.1*inch))

ins_data = [
    [Paragraph("<b><font color='#FFFFFF'>SEGURO</font></b>", BODY),
     Paragraph("<b><font color='#FFFFFF'>QUÉ CUBRE</font></b>", BODY),
     Paragraph("<b><font color='#FFFFFF'>COSTO/AÑO</font></b>", BODY_C)],
    [Paragraph("<b>Jewelers Block Insurance</b><br/>(OBLIGATORIO)", BODY),
     Paragraph("Robo, hurto, daño, transporte de inventario, "
               "consignación, joyas de clientes en reparación", BODY),
     Paragraph("<b>$2,500-4,500</b>", BODY_C)],
    [Paragraph("<b>General Liability (GL)</b>", BODY),
     Paragraph("Si un cliente se cae en tu tienda, demanda por lesiones, "
               "daño a propiedad de terceros", BODY),
     Paragraph("<b>$500-1,200</b>", BODY_C)],
    [Paragraph("<b>Business Personal Property</b>", BODY),
     Paragraph("Equipos: vitrinas, computadora, caja fuerte, "
               "mobiliario (NO joyas, eso va en Jewelers Block)", BODY),
     Paragraph("<b>$300-800</b>", BODY_C)],
    [Paragraph("<b>Workers Comp</b><br/>(si tienes empleados)", BODY),
     Paragraph("Lesiones en el trabajo. TX no lo exige por ley, pero "
               "RECOMENDADO si tienes 1+ empleados", BODY),
     Paragraph("<b>$0 o $400-900</b>", BODY_C)],
    [Paragraph("<b>Cyber Liability</b>", BODY),
     Paragraph("Si aceptas tarjetas, breach de datos del POS, "
               "robo de información de clientes", BODY),
     Paragraph("<b>$300-600</b>", BODY_C)],
    [Paragraph("<b>Crime/Employee Dishonesty</b>", BODY),
     Paragraph("Robo INTERNO (empleados que se llevan joyas)", BODY),
     Paragraph("<b>$200-500</b>", BODY_C)],
    [Paragraph("<b>TOTAL año 1</b>", BODY),
     Paragraph("Paquete completo", BODY),
     Paragraph("<b><font color='#B8860B'>$3,800-8,500</font></b>", BODY_C)],
]
t3 = Table(ins_data, colWidths=[2.0*inch, 3.4*inch, 1.0*inch])
t3.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),GOLD),
    ("BACKGROUND",(0,-1),(-1,-1),GOLD_L),
    ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("LEFTPADDING",(0,0),(-1,-1),6),
    ("RIGHTPADDING",(0,0),(-1,-1),6),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GRAY),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
]))
elems.append(t3)

elems.append(Spacer(1, 0.15*inch))
elems.append(Paragraph(
    "<b>Top 3 carriers de Jewelers Block en TX (2026):</b><br/>"
    "&nbsp;&nbsp;1. <b>Jewelers Mutual</b> — el líder del mercado, 100+ años especializado<br/>"
    "&nbsp;&nbsp;&nbsp;&nbsp;<b>(800) 558-6411</b> · jewelersmutual.com<br/>"
    "&nbsp;&nbsp;2. <b>Chubb Jewelers</b> — premium pero excelente claim service<br/>"
    "&nbsp;&nbsp;3. <b>Mountain Jewelers Insurance</b> — opción para joyerías &lt;$500K inventory",
    BODY))

elems.append(PageBreak())

# ═══ SECCIÓN 5: CHECKLIST 12 SEMANAS ══════════════════════════════════
elems.append(Paragraph("5️⃣  PLAN DE ACCIÓN — 12 SEMANAS", H1))
elems.append(Spacer(1, 0.1*inch))

steps = [
    ("SEMANA 1", "Estructura Legal",
     "Formar LLC en sos.state.tx.us ($300). Obtener EIN en irs.gov/ein (gratis, 10 min). "
     "Abrir cuenta bancaria comercial en banco local de Dumas (Amarillo National, Happy State Bank)."),
    ("SEMANA 1-2", "BOI Report + DBA",
     "Filing BOI en fincen.gov/boi (gratis, 15 min). Filing DBA en Moore County Clerk si vas a "
     "operar con nombre comercial diferente del legal de la LLC ($25)."),
    ("SEMANA 2", "Búsqueda de local + Zoning check",
     "ANTES de firmar lease: ve al City Hall de Dumas con la dirección exacta. Pide carta escrita "
     "confirmando zoning C-1 o C-2 (commercial retail). NO firmes contrato sin esto."),
    ("SEMANA 2-3", "Firma lease + aplicar CO",
     "Una vez confirmado zoning, firma el lease (negocia 60 días free rent para build-out). "
     "Aplica Certificate of Occupancy en City Hall ($50-200). Esto dispara inspección."),
    ("SEMANA 3", "Sales Tax Permit",
     "Aplica permit en comptroller.texas.gov (gratis). Tarda 2-3 semanas en llegar por correo, "
     "pero puedes operar mientras esperas."),
    ("SEMANA 3-4", "CPMD Registration (OCCC)",
     "Crea cuenta en portal ALECS, sube documentos, paga $70. Esta licencia es OBLIGATORIA "
     "si vas a comprar oro/joyería usada al público. Sin ella es delito penal."),
    ("SEMANA 4", "Build-out del local",
     "Construye vitrinas, instala iluminación LED para mostrar joyería, pinta. Instala báscula "
     "comercial certificada (no báscula de cocina), regístrala con TDA ($25-95)."),
    ("SEMANA 5-6", "Sistema de seguridad",
     "Instala sistema UL Listed con monitoring 24/7 (PROSurety, ADT Commercial, Tyco). "
     "Compra caja fuerte TL-15 mínimo. Instala cámaras 1080p+ con backup en cloud. $3,000-8,000."),
    ("SEMANA 6", "Alarm Permit municipal",
     "Una vez instalado el sistema, registra alarm permit en Dumas City Police para que respondan "
     "a alarmas. $25-50/año."),
    ("SEMANA 7", "Compra inventario",
     "Compra inventario inicial de Stuller, Quality Gold, JTV Wholesale o Rio Grande. "
     "Empieza con $30K-60K en inventario para tienda pequeña. Negocia memo/consignación para "
     "minimizar capital inicial."),
    ("SEMANA 8", "Insurance shopping",
     "Llama a Jewelers Mutual (800) 558-6411 con: lista de inventario, fotos del sistema "
     "de seguridad, copia de UL certificate, dirección del local. Pide quote DP-3 + GL + "
     "Workers Comp. Compara contra Chubb."),
    ("SEMANA 9", "Programa AML escrito (si aplica)",
     "Si proyectas &gt;$50K/año en compras O ventas de metales preciosos: contrata "
     "consultor AML ($500-2,500) o usa template de Jewelers Mutual para crear el programa "
     "escrito. Designa compliance officer."),
    ("SEMANA 10", "POS + Software",
     "Instala POS especializado: <b>The Edge Jewelry Software</b> (líder), Jewel360, o "
     "GoldDealerSoftware. Te ayuda a cumplir con reportes CPMD automáticamente."),
    ("SEMANA 10-11", "Marketing pre-apertura",
     "Crea Google Business Profile, Facebook/Instagram. Anuncios en Dumas Moore County News. "
     "Soft opening con descuento 15% primera semana."),
    ("SEMANA 12", "Grand Opening + Compliance review",
     "ABRE. En primer mes: hacer audit interno del flujo CPMD (¿estás guardando IDs, fotos, "
     "hold periods?). Si fallaste algo, ajusta YA antes de que venga inspector."),
]

for i, (when, title, desc) in enumerate(steps, 1):
    row = Table([
        [Paragraph(f"<font color='#FFFFFF' size=12><b>{i}</b></font>",
                   ParagraphStyle("n", parent=BODY_C, fontSize=12, textColor=colors.white)),
         Paragraph(f"<font color='#B8860B' size=8><b>{when}</b></font><br/>"
                   f"<font color='#0F172A' size=10><b>{title}</b></font><br/>"
                   f"<font color='#475569' size=9>{desc}</font>",
                   ParagraphStyle("d", parent=BODY, fontSize=9, leading=12))]
    ], colWidths=[0.4*inch, 6.4*inch])
    row.setStyle(TableStyle([("BACKGROUND",(0,0),(0,0),GOLD),
                              ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),7),
                              ("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),6),
                              ("BOTTOMPADDING",(0,0),(-1,-1),6),
                              ("BOX",(0,0),(-1,-1),0.5,colors.HexColor("#E5E7EB"))]))
    elems.append(row)
    elems.append(Spacer(1, 0.05*inch))

elems.append(PageBreak())

# ═══ SECCIÓN 6: CONTACTOS ═════════════════════════════════════════════
elems.append(Paragraph("6️⃣  CONTACTOS CLAVE", H1))
elems.append(Spacer(1, 0.1*inch))

contacts = [
    ("Texas Comptroller (Sales Tax)", "(800) 252-5555", "comptroller.texas.gov"),
    ("Texas OCCC (CPMD + Pawn)", "(800) 538-1579", "occc.texas.gov"),
    ("Texas Secretary of State (LLC)", "(512) 463-5555", "sos.state.tx.us"),
    ("Texas Department of Agriculture", "(800) 835-5832", "texasagriculture.gov"),
    ("FinCEN (AML + BOI)", "(800) 949-2732", "fincen.gov"),
    ("IRS EIN (Federal Tax ID)", "(800) 829-4933", "irs.gov/ein"),
    ("City of Dumas (CO + Zoning)", "(806) 935-4101", "ci.dumas.tx.us"),
    ("Moore County Clerk (DBA)", "(806) 935-2009", "co.moore.tx.us"),
    ("Dumas Police (Alarm Permit)", "(806) 935-4151", "Dumas City Hall"),
    ("Dumas Chamber of Commerce", "(806) 935-2123", "dumaschamber.org"),
    ("Dumas EDC (incentivos)", "(806) 935-6248", "dumasedc.org"),
    ("Jewelers Mutual Insurance", "(800) 558-6411", "jewelersmutual.com"),
    ("Texas Jewelers Association", "(817) 595-2700", "texasjewelers.org"),
    ("Jewelers of America (national)", "(800) 223-0673", "jewelers.org"),
]

contact_table = [[
    Paragraph("<b><font color='#FFFFFF'>ENTIDAD</font></b>", BODY),
    Paragraph("<b><font color='#FFFFFF'>TELÉFONO</font></b>", BODY_C),
    Paragraph("<b><font color='#FFFFFF'>WEB</font></b>", BODY_C),
]]
for name, phone, web in contacts:
    contact_table.append([
        Paragraph(name, BODY),
        Paragraph(f"<b>{phone}</b>", BODY_C),
        Paragraph(f"<font size=9>{web}</font>", BODY_C),
    ])

tc = Table(contact_table, colWidths=[2.6*inch, 1.5*inch, 2.3*inch])
tc.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),GOLD),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("LEFTPADDING",(0,0),(-1,-1),7),
    ("RIGHTPADDING",(0,0),(-1,-1),7),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GRAY),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT]),
]))
elems.append(tc)

elems.append(Spacer(1, 0.3*inch))

# Final note + disclaimer
elems.append(Paragraph("🎯 PRIMERA LLAMADA RECOMENDADA ESTA SEMANA", H3))
elems.append(Paragraph(
    "<b>Dumas City Hall: (806) 935-4101.</b> Pide hablar con building/zoning. "
    "Pregunta: <i>'Estoy buscando local para abrir una joyería en Dumas. ¿Pueden confirmarme "
    "qué calles/direcciones son zona C-1 o C-2 retail? ¿Qué documentos necesito para Certificate "
    "of Occupancy?'</i> Esta llamada de 10 minutos te ahorra meses de errores costosos.",
    ParagraphStyle("hl", parent=BODY, backColor=GOLD_L, borderColor=GOLD, borderWidth=1,
                    borderPadding=10, leftIndent=4, rightIndent=4)))

elems.append(Spacer(1, 0.2*inch))
elems.append(Paragraph(
    "<font size=8 color='#6B7280'>Este reporte se basa en información pública 2026 de FinCEN, "
    "IRS, Texas Comptroller, OCCC, TDA, Secretary of State, City of Dumas, Moore County, NFIB y "
    "Texas Jewelers Association. Las regulaciones cambian — verifica directamente con cada entidad "
    "antes de iniciar trámites. Este reporte no constituye asesoría legal o financiera; consulta "
    "un abogado o CPA para tu situación específica.<br/>"
    "Ross House Rentals · Business Advisory · " + datetime.now().strftime("%B %Y") + "</font>",
    BODY_C))

# BUILD
doc = SimpleDocTemplate(OUT, pagesize=letter, rightMargin=0.55*inch, leftMargin=0.55*inch,
                         topMargin=0.55*inch, bottomMargin=0.5*inch,
                         title="Jewelry Store License Research — Dumas TX 2026",
                         author="Ross House Rentals · Business Advisory")
doc.build(elems)
print(f"✅ PDF generado: {OUT}  ({os.path.getsize(OUT)/1024:.1f} KB)")

# ═══ EMAIL ════════════════════════════════════════════════════════════
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
    FileContent, FileName, FileType, Disposition)

with open(OUT, "rb") as f: pdf_bytes = f.read()
stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

mail = Mail(
    from_email=Email(os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com"), "Ross House Rentals"),
    to_emails=To("yoandyross@gmail.com"),
    subject="💎 Licencias para Joyería de Oro en Dumas TX — Investigación Completa para Alfredo Hernández",
)
mail.add_content(Content("text/html", f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto">
  <div style="padding:32px 24px;background:linear-gradient(135deg,#B8860B 0%,#92590A 100%);border-radius:16px 16px 0 0;color:#fff">
    <div style="font-size:11px;letter-spacing:3px;font-weight:600;opacity:0.85;margin-bottom:8px">💎 BUSINESS ADVISORY</div>
    <h1 style="margin:0;font-size:26px;line-height:1.2;font-weight:700">
      Joyería de Oro · Dumas, TX
    </h1>
    <p style="margin:8px 0 0;opacity:0.95;font-size:14px">
      Investigación completa de licencias para Alfredo Hernández
    </p>
  </div>

  <div style="padding:24px;background:#fff;border:1px solid #E5E7EB;border-top:0;border-radius:0 0 16px 16px">
    <p>Yoandy, aquí tienes el reporte completo para Alfredo. Cubre <b>los 3 escenarios</b>:
    vender joyería nueva, comprar oro usado, y opcionalmente prestar dinero (pawn).</p>

    <h3 style="color:#B8860B">📊 Bottom Line</h3>
    <table style="width:100%;border-collapse:collapse;margin:18px 0">
      <tr>
        <td style="padding:10px;background:#FEF3C7;border-radius:8px;text-align:center;width:50%">
          <div style="font-size:18px;font-weight:bold;color:#B8860B">$4,140-7,575</div>
          <div style="font-size:10px;color:#6B7280">Costo total año 1<br/>(licencias + seguros)</div>
        </td>
        <td style="padding:10px;background:#ECFDF5;border-radius:8px;text-align:center;width:50%">
          <div style="font-size:18px;font-weight:bold;color:#059669">12 semanas</div>
          <div style="font-size:10px;color:#6B7280">Tiempo total<br/>desde formar LLC hasta apertura</div>
        </td>
      </tr>
    </table>

    <h3 style="color:#B8860B">⚠️ Las 3 licencias críticas (las que NADIE puede saltarse)</h3>
    <ol>
      <li><b>EIN federal</b> (gratis, irs.gov/ein)</li>
      <li><b>Texas Sales Tax Permit</b> (gratis, Comptroller) — joyería paga 8.25%</li>
      <li><b>CPMD del OCCC</b> ($70/año) — <span style="color:#DC2626"><b>OBLIGATORIA si compra oro usado.
      Sin ella es delito penal Clase A</b></span></li>
    </ol>

    <h3 style="color:#B8860B">📑 El PDF incluye</h3>
    <ol>
      <li>Federal: EIN, FinCEN AML 31 CFR 1027, BOI Report, Form 8300</li>
      <li>Estatal TX: Sales Tax, CPMD (OCCC), Weights & Measures, Pawn License opcional, LLC</li>
      <li>Local Dumas: Certificate of Occupancy, Zoning, Sign Permit, Alarm Permit</li>
      <li>Seguros: Jewelers Block, GL, Workers Comp, Cyber (con quotes estimados)</li>
      <li><b>Plan de 12 semanas paso a paso</b> desde formación de LLC hasta grand opening</li>
      <li>14 contactos con teléfonos directos (Comptroller, OCCC, City Hall, etc.)</li>
    </ol>

    <h3 style="color:#B8860B">🎯 Acción inmediata para Alfredo (10 min hoy)</h3>
    <p style="background:#FEF3C7;padding:12px;border-left:4px solid #B8860B;border-radius:4px">
      Llamar a <b>City of Dumas: (806) 935-4101</b> y preguntar: <i>"¿Qué calles son zona
      C-1 o C-2 retail? ¿Qué necesito para Certificate of Occupancy de una joyería?"</i>
      Esta llamada le ahorra meses de errores costosos.
    </p>

    <h3 style="color:#B8860B">⚠️ Decisión clave que Alfredo debe tomar</h3>
    <p><b>¿Va a COMPRAR oro usado al público o solo vender joyería nueva?</b></p>
    <ul>
      <li><b>Si solo VENDE nuevo:</b> No necesita CPMD ni Pawn License. Ahorra $755/año y mucho papeleo.</li>
      <li><b>Si COMPRA usado:</b> CPMD del OCCC OBLIGATORIA ($70/año). Sin ella, cada compra es felonía.</li>
      <li><b>Si PRESTA dinero (pawn):</b> Pawn License ($685/año) + $150K en net assets + surety bond.</li>
    </ul>

    <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0">
    <p style="font-size:11px;color:#6B7280">Generado: {stamp}<br/>Ross House Rentals · Business Advisory</p>
  </div>
</div>
"""))
mail.attachment = Attachment(
    FileContent(base64.b64encode(pdf_bytes).decode()),
    FileName("Jewelry_Store_License_Research_Dumas_TX_2026.pdf"),
    FileType("application/pdf"),
    Disposition("attachment"),
)
sg = SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
resp = sg.send(mail)
print(f"✅ Email enviado (status={resp.status_code}) → yoandyross@gmail.com")
