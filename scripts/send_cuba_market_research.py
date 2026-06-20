"""
Cuba Market Research Report — Junio 2026
=========================================
Análisis exhaustivo para Yoandy Ross:
  - Estado actual de la economía cubana (GDP, inflación, blackouts, food crisis)
  - Marco legal MIPYME 2026 + Decree-Law 114/2025
  - Restricciones US (Trump EO 14404, OFAC, $1,000/quarter cap)
  - Sectores viables ranked por riesgo/reward
  - Estrategias específicas para diáspora
  - Opinión honesta SI/NO vale la pena (sin endulzar)
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
    TableStyle, PageBreak)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT

OUT = "/tmp/cuba/Cuba_Market_Research_2026.pdf"
os.makedirs("/tmp/cuba", exist_ok=True)

# Cuban flag-inspired palette
BLUE = colors.HexColor("#002A8F")
RED = colors.HexColor("#CF142B")
RED_L = colors.HexColor("#FEE2E2")
GREEN = colors.HexColor("#059669"); GREEN_L = colors.HexColor("#ECFDF5")
YELLOW = colors.HexColor("#D97706"); YELLOW_L = colors.HexColor("#FEF3C7")
DARK = colors.HexColor("#0F172A")
GRAY = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F3F4F6")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=22, textColor=DARK, spaceAfter=4, leading=26)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=14, textColor=BLUE, spaceAfter=10, leading=17, fontName="Helvetica-Bold")
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=11, textColor=DARK, spaceAfter=4, leading=14, fontName="Helvetica-Bold")
BODY = ParagraphStyle("Body", parent=ss["Normal"], fontSize=10, textColor=DARK, leading=14, spaceAfter=6, alignment=TA_JUSTIFY)
BODY_L = ParagraphStyle("BodyL", parent=BODY, alignment=TA_LEFT)
BODY_C = ParagraphStyle("BodyC", parent=BODY, alignment=TA_CENTER)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, textColor=GRAY, leading=11)

elems = []

# ═══════════════════════ COVER ═══════════════════════════════════════
elems += [
    Spacer(1, 0.4*inch),
    Paragraph('<font color="#CF142B"><b>R</b>OSS HOUSE · BUSINESS INTELLIGENCE</font>',
              ParagraphStyle("logo", parent=BODY_C, fontSize=11, textColor=RED, fontName="Helvetica-Bold")),
    Spacer(1, 0.4*inch),
    Paragraph("MERCADO CUBANO 2026",
              ParagraphStyle("title", parent=H1, fontSize=30, alignment=TA_CENTER, leading=34, textColor=DARK)),
    Spacer(1, 0.05*inch),
    Paragraph('<font color="#002A8F">VIABILIDAD DE NEGOCIO · MARCO LEGAL · ESTRATEGIA · OPINIÓN HONESTA</font>',
              ParagraphStyle("sub", parent=BODY_C, fontSize=13, textColor=BLUE, fontName="Helvetica-Bold")),
    Spacer(1, 0.4*inch),
]

# Big stat boxes — quick context
stats_data = [[
    Paragraph("<font color='#FFFFFF' size=20><b>$108B</b></font><br/>"
              "<font color='#FFFFFF' size=8>GDP 2026<br/>(crisis prolongada)</font>", BODY_C),
    Paragraph("<font color='#FFFFFF' size=20><b>22%</b></font><br/>"
              "<font color='#FFFFFF' size=8>Inflación anualizada<br/>(real, no oficial)</font>", BODY_C),
    Paragraph("<font color='#FFFFFF' size=20><b>14+ hrs</b></font><br/>"
              "<font color='#FFFFFF' size=8>de apagón diario<br/>en zonas amplias</font>", BODY_C),
    Paragraph("<font color='#FFFFFF' size=20><b>125</b></font><br/>"
              "<font color='#FFFFFF' size=8>actividades<br/>PROHIBIDAS</font>", BODY_C),
]]
t_stats = Table(stats_data, colWidths=[1.55*inch]*4, rowHeights=[0.9*inch])
t_stats.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(0,0),BLUE),
    ("BACKGROUND",(1,0),(1,0),RED),
    ("BACKGROUND",(2,0),(2,0),YELLOW),
    ("BACKGROUND",(3,0),(3,0),DARK),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("ALIGN",(0,0),(-1,-1),"CENTER"),
    ("LEFTPADDING",(0,0),(-1,-1),4),
    ("RIGHTPADDING",(0,0),(-1,-1),4),
]))
elems.append(t_stats)

elems.append(Spacer(1, 0.35*inch))

# Verdict box on cover
verdict = Table([[
    Paragraph("<font color='#FFFFFF' size=11><b>VEREDICTO HONESTO (anticipado)</b></font>", BODY_C)],
    [Paragraph(
        "<font size=14 color='#CF142B'><b>⚠️  ALTO RIESGO  ·  BAJO RETORNO</b></font><br/><br/>"
        "Para un emprendedor cubano-americano operando desde TX, invertir directamente en Cuba en 2026 "
        "es <b>extremadamente arriesgado y, en muchos casos, ILEGAL</b> bajo la ley estadounidense. "
        "Hay oportunidades reales para cubanos <i>en la isla</i>, pero para la diáspora desde EEUU, "
        "el ROI ajustado al riesgo NO justifica la exposición.<br/><br/>"
        "<font color='#6B7280' size=9>Detalle completo del análisis en las páginas siguientes.</font>",
        ParagraphStyle("v", parent=BODY_L, fontSize=10, leading=14))],
], colWidths=[6.4*inch])
verdict.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(0,0),DARK),
    ("BACKGROUND",(0,1),(0,1),RED_L),
    ("BOX",(0,0),(-1,-1),1,RED),
    ("LEFTPADDING",(0,0),(-1,-1),16),
    ("RIGHTPADDING",(0,0),(-1,-1),16),
    ("TOPPADDING",(0,0),(-1,-1),12),
    ("BOTTOMPADDING",(0,0),(-1,-1),12),
]))
elems.append(verdict)

elems += [
    Spacer(1, 0.4*inch),
    Paragraph(
        f"<font color='#6B7280' size=9>Generado: {datetime.now().strftime('%B %d, %Y')}  ·  "
        "Fuentes: ECLAC, BTI Index, WFP, EL PAÍS, FIU Cuba Study Group, OFAC, "
        "Debevoise &amp; Plimpton, Baker McKenzie, Cuba Trade Magazine, "
        "Diaz Trade Law, Trading Economics, Cibercuba</font>", BODY_C),
]
elems.append(PageBreak())

# ═══════════════════════ 1. CONTEXTO ECONÓMICO ═══════════════════════
elems.append(Paragraph("1️⃣  EL CONTEXTO REAL — CUBA EN JUNIO 2026", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph(
    "Antes de hablar de negocios, hay que entender el terreno. Cuba en 2026 atraviesa <b>la peor crisis "
    "económica desde el 'Período Especial' de los 90s</b>, pero esta vez sin la URSS al rescate y con un "
    "embargo más estricto que nunca.", BODY))

elems.append(Spacer(1, 0.12*inch))

elems.append(Paragraph("1.1  Macro Económica", H2))

macro_data = [
    [Paragraph("<b>Indicador</b>", BODY),
     Paragraph("<b>Valor 2026</b>", BODY_C),
     Paragraph("<b>Implicación</b>", BODY)],
    [Paragraph("PIB nominal", BODY),
     Paragraph("$108.96 B USD", BODY_C),
     Paragraph("Contracción tras 2023-2024. Tercera caída consecutiva.", BODY)],
    [Paragraph("Inflación oficial (MoM)", BODY),
     Paragraph("1.85% mensual<br/>~22% anualizada", BODY_C),
     Paragraph("Real es <b>2-3x más alta</b> por dolarización informal y escasez", BODY)],
    [Paragraph("Cambio CUP/USD (informal)", BODY),
     Paragraph("~400 CUP / $1", BODY_C),
     Paragraph("Oficial: 120 CUP/$. Brecha enorme entre tasa estatal y calle.", BODY)],
    [Paragraph("Salario medio estatal", BODY),
     Paragraph("~4,500 CUP/mes ≈ $11 USD", BODY_C),
     Paragraph("Insuficiente para sobrevivir. Todos buscan ingreso extra.", BODY)],
    [Paragraph("Pensión jubilados", BODY),
     Paragraph("~1,500 CUP ≈ $3.75 USD/mes", BODY_C),
     Paragraph("Catástrofe humanitaria. Familia/remesas son la única red.", BODY)],
    [Paragraph("Apagones diarios", BODY),
     Paragraph("14-20 hrs en muchas zonas", BODY_C),
     Paragraph("Sistema eléctrico colapsado. Negocios sin generador = muertos.", BODY)],
    [Paragraph("Escasez alimentos", BODY),
     Paragraph("WFP declarando crisis", BODY_C),
     Paragraph("Libreta cubre 7-10 días/mes. Resto: mercado informal en USD.", BODY)],
    [Paragraph("Población", BODY),
     Paragraph("~10.5M (cae rápido)", BODY_C),
     Paragraph("Éxodo histórico: ~1M emigró 2022-2025. Sigue. Cuba envejece.", BODY)],
]
t_macro = Table(macro_data, colWidths=[1.6*inch, 1.8*inch, 3.0*inch])
t_macro.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),BLUE),
    ("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("LEFTPADDING",(0,0),(-1,-1),6),
    ("RIGHTPADDING",(0,0),(-1,-1),6),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GRAY),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT]),
]))
elems.append(t_macro)

elems.append(Spacer(1, 0.2*inch))
elems.append(Paragraph("1.2  Dolarización de facto", H2))
elems.append(Paragraph(
    "El gobierno cubano reconoció oficialmente en 2024-2025 que la economía está <b>parcialmente "
    "dolarizada</b>. Casi todo bien importado (pollo, aceite, electrodomésticos, materiales de construcción) "
    "se vende SOLO en MLC (Moneda Libremente Convertible) o USD/EUR directo. El peso cubano (CUP) sirve "
    "cada vez menos. Las MIPYMEs operan en MLC para importar; el cliente paga en USD físico (entregado "
    "por remesa) o en MLC vía tarjeta recargada en el exterior.<br/><br/>"
    "<b>Implicación para diáspora:</b> No tiene sentido invertir capital en pesos cubanos. Cualquier "
    "negocio viable opera <b>de facto en USD</b>, lo cual también es el origen del riesgo regulatorio "
    "(el estado puede confiscar cuentas USD cuando quiera).", BODY))

elems.append(PageBreak())

# ═══════════════════════ 2. MARCO LEGAL CUBANO ═══════════════════════
elems.append(Paragraph("2️⃣  MARCO LEGAL — QUÉ ES LEGAL Y QUÉ NO (CUBA)", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph("2.1  La revolución MIPYME (2021-2026)", H2))
elems.append(Paragraph(
    "Cuba creó las <b>MIPYME</b> (Micro, Pequeñas y Medianas Empresas) en agosto 2021 — primer "
    "marco legal para empresa privada en la isla desde 1968. Hubo un boom inicial: para 2023 había "
    "<b>+11,000 MIPYMEs registradas</b>, 90% concentradas en La Habana. Pero en 2024 el gobierno "
    "frenó la apertura:", BODY))

elems.append(Spacer(1, 0.1*inch))

timeline = [
    ("Ago 2021", "Decreto-Ley 46", "Creación legal de MIPYMEs hasta 100 empleados. Boom inicial."),
    ("2022-2023", "Expansión rápida", "+11,000 MIPYMEs registradas. Dominan La Habana. Sector privado contribuye 14% del PIB."),
    ("Feb 2024", "Endurecimiento", "Cuba reduce lista permitida. Pasa de 112 a 125 actividades PROHIBIDAS."),
    ("Sep 2024", "Decentralización selectiva", "Aprobación pasa a Consejos Municipales (CAMs). Más control local, más burocracia."),
    ("Mar 2026", "Decreto-Ley 114/2025", "Permite por primera vez en 70 años empresas MIXTAS estado-privado. Cuentapropistas EXCLUIDOS."),
    ("Jun 2026", "Programa Económico 2026", "Díaz-Canel anuncia 'liberalización' adicional. Sin detalles concretos aún."),
]

for fecha, ley, desc in timeline:
    row = Table([[
        Paragraph(f"<font color='#FFFFFF' size=9><b>{fecha}</b></font>", BODY_C),
        Paragraph(f"<font color='#002A8F'><b>{ley}</b></font><br/><font size=9>{desc}</font>", BODY_L)
    ]], colWidths=[0.95*inch, 5.85*inch])
    row.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0),BLUE),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("BOX",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ]))
    elems.append(row)
    elems.append(Spacer(1, 0.04*inch))

elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("2.2  ¿Qué SÍ está permitido?", H2))
permitido_data = [
    [Paragraph("✅ <b>Comercio minorista</b>", BODY),
     Paragraph("Tiendas físicas y online (paladares, peluquerías, bodegas, ropa)", BODY)],
    [Paragraph("✅ <b>Servicios gastronómicos</b>", BODY),
     Paragraph("Restaurantes, paladares, dulcerías, catering. Permitido + alta demanda.", BODY)],
    [Paragraph("✅ <b>Producción de alimentos</b>", BODY),
     Paragraph("Procesamiento, panaderías, agroindustria. Cuba IMPORTA 80% de comida.", BODY)],
    [Paragraph("✅ <b>Construcción/Reparación</b>", BODY),
     Paragraph("Albañilería, electricidad, plomería, materiales construcción", BODY)],
    [Paragraph("✅ <b>Transporte privado</b>", BODY),
     Paragraph("Taxi, mensajería, fletes de mercancía. Demanda altísima.", BODY)],
    [Paragraph("✅ <b>Tecnología/Desarrollo</b>", BODY),
     Paragraph("Apps, software (con restricciones). Outsourcing IT a empresas extranjeras vía MIPYME.", BODY)],
    [Paragraph("✅ <b>Importación vía estado</b>", BODY),
     Paragraph("MIPYMEs importaron +$2B USD en 2025 (pollo, harina, aceite, materiales). Vía empresa estatal intermediaria.", BODY)],
]
t_perm = Table(permitido_data, colWidths=[1.9*inch, 4.9*inch])
t_perm.setStyle(TableStyle([
    ("VALIGN",(0,0),(-1,-1),"TOP"),
    ("BACKGROUND",(0,0),(0,-1),GREEN_L),
    ("LEFTPADDING",(0,0),(-1,-1),8),
    ("RIGHTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GREEN),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
]))
elems.append(t_perm)

elems.append(PageBreak())

elems.append(Paragraph("2.3  ¿Qué NO está permitido (125 actividades prohibidas)?", H2))
prohibido_data = [
    [Paragraph("❌ <b>Periodismo independiente</b>", BODY),
     Paragraph("Medios de comunicación, agencias de noticias, prensa", BODY)],
    [Paragraph("❌ <b>Educación independiente</b>", BODY),
     Paragraph("Universidades, escuelas, training académico formal", BODY)],
    [Paragraph("❌ <b>Salud privada</b>", BODY),
     Paragraph("Clínicas, hospitales, farmacias, servicios médicos directos al público", BODY)],
    [Paragraph("❌ <b>Servicios profesionales</b>", BODY),
     Paragraph("Abogados, arquitectos, contadores independientes (¡crítico!)", BODY)],
    [Paragraph("❌ <b>Minería y energía</b>", BODY),
     Paragraph("Petróleo, gas, minas, generación eléctrica (sector estratégico)", BODY)],
    [Paragraph("❌ <b>Defensa/Seguridad</b>", BODY),
     Paragraph("Armas, seguridad privada armada, vigilancia privada", BODY)],
    [Paragraph("❌ <b>Telecom</b>", BODY),
     Paragraph("ETECSA tiene monopolio. No se puede competir.", BODY)],
    [Paragraph("❌ <b>Banca/Finanzas</b>", BODY),
     Paragraph("No hay banca privada. Préstamos, FX, seguros = estado", BODY)],
    [Paragraph("❌ <b>Exportación directa</b>", BODY),
     Paragraph("Para exportar, MIPYME debe pasar por empresa estatal de comercio exterior", BODY)],
    [Paragraph("❌ <b>Cuentapropistas → asociaciones</b>", BODY),
     Paragraph("Autoempleados NO pueden entrar en empresas mixtas estado-privado", BODY)],
]
t_proh = Table(prohibido_data, colWidths=[2.1*inch, 4.7*inch])
t_proh.setStyle(TableStyle([
    ("VALIGN",(0,0),(-1,-1),"TOP"),
    ("BACKGROUND",(0,0),(0,-1),RED_L),
    ("LEFTPADDING",(0,0),(-1,-1),8),
    ("RIGHTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,RED),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
]))
elems.append(t_proh)

elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("2.4  Decreto-Ley 114/2025 — Empresas mixtas estado-privado", H2))
elems.append(Paragraph(
    "Vigente desde MARZO 2026. Es el cambio más importante en 70 años: permite por primera vez que "
    "el estado y privados <b>compartan accionariado en una misma empresa</b>. Modalidades:<br/>"
    "&nbsp;&nbsp;• <b>LLCs mixtas</b> (estado + MIPYME comparten acciones)<br/>"
    "&nbsp;&nbsp;• <b>Compra estatal de acciones privadas</b> (state buy-in)<br/>"
    "&nbsp;&nbsp;• <b>Absorción</b> de privadas por estatales<br/>"
    "&nbsp;&nbsp;• <b>Contratos de colaboración</b> sin fusión accionaria<br/><br/>"
    "<b>Ventajas reales:</b> Las empresas mixtas pueden importar/exportar <b>directamente</b> (sin "
    "intermediario), manejar cuentas en USD/EUR, y fijar algunos precios. Pero <b>cada operación "
    "necesita aprobación del Ministerio de Economía</b>, lo que en la práctica genera demoras de "
    "6-18 meses.<br/><br/>"
    "<b>Trampa:</b> Los <i>cuentapropistas</i> (autoempleados, que son la mayoría de los pequeños "
    "negocios cubanos) están EXCLUIDOS de este beneficio. Solo MIPYMEs ≥ ~5 empleados y cooperativas.", BODY))

elems.append(PageBreak())

# ═══════════════════════ 3. RESTRICCIONES US ═══════════════════════
elems.append(Paragraph("3️⃣  EL OTRO LADO — RESTRICCIONES US (CRÍTICO)", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph(
    "<font color='#CF142B'><b>Aquí está el problema más grande para un cubano-americano operando "
    "desde TX.</b></font> No es solo lo que Cuba permite — es lo que <b>EEUU TE PROHÍBE</b> hacer "
    "como U.S. Person (ciudadano, residente permanente, o cualquier persona física/jurídica bajo "
    "jurisdicción US).", BODY))

elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("3.1  Executive Order 14404 (Mayo 2026) — Sanciones secundarias", H2))
elems.append(Paragraph(
    "Trump firmó la EO 14404 el <b>1 de mayo de 2026</b>. Es el endurecimiento más grande contra "
    "Cuba en décadas. Lo crítico:<br/><br/>"
    "• <b>Régimen IEEPA</b> (no solo CACR). Crea lista de sanciones tipo Irán/Rusia.<br/>"
    "• <b>SANCIONES SECUNDARIAS</b>: Bancos extranjeros (España, Canadá, Alemania) que faciliten "
    "transacciones con Cuba pierden acceso al sistema bancario US. Por miedo, muchos bancos "
    "extranjeros <b>cortaron Cuba de inmediato</b>.<br/>"
    "• Sectores blanco: <b>energía, defensa, minería, servicios financieros, seguridad</b>.<br/>"
    "• Cualquier entidad controlada por gobierno cubano = SDN list.<br/><br/>"
    "<b>Implicación PRÁCTICA para Yoandy:</b> Si llegas a operar con un banco en Cuba que toca "
    "GAESA/Fincimex (lo cual es casi imposible evitar), <b>tu LLC en TX puede ser bloqueada por OFAC</b>. "
    "Tus cuentas Stripe, Wells Fargo, todo. <b>Ross House Rentals podría quedar paralizada.</b>",
    BODY))

elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("3.2  Límites de remesas reinstaurados", H2))
remesa_data = [
    [Paragraph("<b>Tipo de remesa</b>", BODY),
     Paragraph("<b>Límite 2026</b>", BODY_C),
     Paragraph("<b>Notas</b>", BODY)],
    [Paragraph("Familiar (a un familiar cercano)", BODY),
     Paragraph("$1,000 / trimestre", BODY_C),
     Paragraph("Reinstaurado por Trump. Antes (Biden 2022-2024): sin límite.", BODY)],
    [Paragraph("Donativa (no familiar)", BODY),
     Paragraph("Restringidas", BODY_C),
     Paragraph("Ya no se puede 'apoyar emprendedores' libremente.", BODY)],
    [Paragraph("Emigración (gastos preliminares)", BODY),
     Paragraph("$1,000 una vez", BODY_C),
     Paragraph("Para familiar que está emigrando.", BODY)],
    [Paragraph("Emigración (boletos/visas)", BODY),
     Paragraph("$1,000 adicional", BODY_C),
     Paragraph("Solo costos de viaje específicos.", BODY)],
    [Paragraph("A Fincimex / GAESA", BODY),
     Paragraph("PROHIBIDO", BODY_C),
     Paragraph("Cualquier institución vinculada al ejército cubano. Multa severa.", BODY)],
]
t_rem = Table(remesa_data, colWidths=[2.2*inch, 1.5*inch, 3.1*inch])
t_rem.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),DARK),
    ("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("LEFTPADDING",(0,0),(-1,-1),6),
    ("RIGHTPADDING",(0,0),(-1,-1),6),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GRAY),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT]),
]))
elems.append(t_rem)

elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("3.3  Inversión directa de diáspora US — STATUS", H2))
elems.append(Paragraph(
    "<b>U.S. Persons NO PUEDEN invertir directamente en MIPYMEs cubanas.</b> Cuban Assets Control "
    "Regulations (CACR 31 CFR 515) sigue vigente. La única excepción histórica fue la <b>'MIPYME "
    "loophole' de Biden</b> (2022) que permitía a US Persons enviar capital semilla a emprendedores; "
    "esa puerta fue <b>cerrada por Trump en enero 2026</b>.<br/><br/>"
    "<b>Estructuras que NO funcionan</b> (la gente cree que sí, pero son ilegales o frágiles):<br/>"
    "&nbsp;&nbsp;• Comprar acciones de MIPYME vía LLC TX → CACR violation<br/>"
    "&nbsp;&nbsp;• Triangular vía LLC en Panamá/España → secondary sanctions EO 14404<br/>"
    "&nbsp;&nbsp;• Préstamo personal a familiar cubano que abre MIPYME → si supera $1,000/Q es violation<br/>"
    "&nbsp;&nbsp;• Pagar por servicios a MIPYME cubana (ej: outsourcing IT) → ZONA GRIS, posible.<br/><br/>"
    "<b>Multas OFAC por violación CACR:</b> Hasta <b>$356,579 por transacción</b> civil. Penal: hasta "
    "<b>$1M + 20 años cárcel</b>.", BODY))

elems.append(PageBreak())

# ═══════════════════════ 4. SECTORES VIABLES ═══════════════════════
elems.append(Paragraph("4️⃣  SECTORES VIABLES — RANKING REALISTA", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph(
    "Si fueras cubano residente en la isla (no diáspora US), estos son los sectores con mejor "
    "relación demanda/capital/regulación en 2026. <b>Ranking por probabilidad de éxito real</b>:", BODY))

elems.append(Spacer(1, 0.15*inch))

sectores = [
    ("🥇 #1", "Importación + Distribución de Alimentos", "GREEN",
     "Mercado: $2B+ USD/año. Demanda infinita: pollo, aceite, harina, leche, granos. Margen: 25-50%. "
     "Capital inicial: $15-50K USD. Requiere acuerdo con empresa estatal de comercio exterior. "
     "Logística vía Panamá/México/España. RIESGO: dependencia de divisas + apagones para refrigeración."),
    ("🥈 #2", "Restaurante / Paladar / Take-away", "GREEN",
     "Demanda local USD constante (turistas + cubanos con remesas). Capital: $5-20K. Margen 30-45%. "
     "Cualquier zona de La Habana, Varadero, Trinidad. RIESGO: apagones diarios (necesitas generador + "
     "freezer) y precio insumos volátil."),
    ("🥉 #3", "Producción / Procesamiento Alimentos", "GREEN",
     "Panaderías, agroindustria pequeña, snacks, refrescos. Cuba importa 80% comida. "
     "Capital: $10-30K. Margen 20-40%. Demanda altísima por sustitución de importaciones. "
     "RIESGO: combustible para transporte, fertilizantes."),
    ("4", "Transporte / Mensajería Privada", "YELLOW",
     "Carga + última milla. Estado de transporte público colapsado. Capital: $8-25K (camión + permisos). "
     "Margen: 35-50%. RIESGO: combustible escaso, repuestos imposibles."),
    ("5", "Construcción / Materiales", "YELLOW",
     "Reparación de viviendas con remesas. Margen: 25-40%. Capital: $5-20K. "
     "RIESGO: importación de cemento/varilla casi imposible, calidad mala."),
    ("6", "Cold Chain / Refrigeración", "YELLOW",
     "Almacenes refrigerados, congeladores rentados. Capital: $15-40K + generadores. Margen: 40-60%. "
     "RIESGO: 100% dependiente de electricidad."),
    ("7", "Servicios IT / Outsourcing", "YELLOW",
     "Developers cubanos trabajando para empresas extranjeras vía MIPYME. Margen excelente (60%+). "
     "Capital: $2-5K. RIESGO: internet pésimo, ETECSA controla todo, sanciones US bloquean PayPal/Stripe."),
    ("8", "Turismo Receptivo", "RED",
     "Casa particular, tours. Turismo en COLAPSO (de 4.7M visitantes pre-pandemia a 1.6M en 2025). "
     "Margen: 20-35% pero VOLUMEN cayó 70%. NO recomendado para inversión nueva."),
    ("9", "Ropa / Moda", "RED",
     "Mercado pequeño, capital limitado de consumidores. Margen bajo, alta competencia callejera. "
     "Capital: $3-10K. Solo viable para nichos premium con clientela MLC."),
    ("10", "Tecnología / E-commerce", "RED",
     "Limitaciones internet, pagos online inexistentes, sanciones US. Solo viable como B2B "
     "outsourcing al extranjero. NO mercado interno B2C significativo."),
]

for rank, sector, color, desc in sectores:
    color_bg = GREEN_L if color == "GREEN" else (YELLOW_L if color == "YELLOW" else RED_L)
    color_b = GREEN if color == "GREEN" else (YELLOW if color == "YELLOW" else RED)
    row = Table([[
        Paragraph(f"<font size=11><b>{rank}</b></font>", BODY_C),
        Paragraph(f"<b>{sector}</b><br/><font size=9>{desc}</font>", BODY_L)
    ]], colWidths=[0.55*inch, 6.25*inch])
    row.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0),color_b),
        ("BACKGROUND",(1,0),(1,0),color_bg),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),8),
        ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),7),
        ("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("BOX",(0,0),(-1,-1),0.5,color_b),
    ]))
    elems.append(row)
    elems.append(Spacer(1, 0.05*inch))

elems.append(PageBreak())

# ═══════════════════════ 5. ESTRATEGIAS DIÁSPORA ═══════════════════════
elems.append(Paragraph("5️⃣  ESTRATEGIAS PARA DIÁSPORA — Lo legal y lo creativo", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph(
    "Asumiendo que <b>NO quieres violar OFAC ni perder tu LLC TX</b>, estas son las únicas "
    "estrategias legítimas para participar del mercado cubano:", BODY))

elems.append(Spacer(1, 0.15*inch))

estrategias = [
    ("A", "Operar vía familiar 100% cubano (residente)",
     "El familiar abre y opera la MIPYME a su nombre. Tú envías remesas familiares ($1,000/Q máx). "
     "El negocio es 100% del familiar legalmente. Tú no eres dueño, no es tu LLC. <br/>"
     "<b>Pros:</b> Legal bajo CACR. Soporta familia. Si funciona, generas ahorros para ellos.<br/>"
     "<b>Contras:</b> No es tu negocio, no controlas. Familia puede mal gestionar. ROI 0% para ti."),

    ("B", "Servicios desde TX hacia diáspora cubana en US",
     "<b>ESTA ES MI RECOMENDACIÓN MÁS FUERTE.</b> Hay 2.5M+ cubanos en EEUU (Miami, Tampa, Houston, "
     "Las Vegas, NY) con necesidades específicas: tax services, immigration paperwork, real estate, "
     "remittance compliance, business advisory para los que abren MIPYMEs allá.<br/>"
     "<b>Pros:</b> 100% legal, sin exposición OFAC. Aprovecha tu Ross Tax + Ross House. ROI alto.<br/>"
     "<b>Contras:</b> Competencia en Miami brutal. En TX/Houston casi virgen."),

    ("C", "Money Service Business licenciada (remesas legales)",
     "Convertirte en una <b>FinCEN MSB</b> + state licenses TX para enviar remesas legales a Cuba "
     "vía corredores autorizados (Western Union, etc.). Margen 3-7% por transacción.<br/>"
     "<b>Pros:</b> Mercado enorme (~$2B remesas/año US→Cuba). Recurring revenue.<br/>"
     "<b>Contras:</b> Compliance MSB es CARO ($50-150K setup, AML program, bond, $185/state). "
     "Y Trump quiere apretar más en 2026. Marcos legales pueden cerrar."),

    ("D", "B2B outsourcing IT desde Cuba a TU LLC TX",
     "Tu LLC en TX contrata a desarrolladores cubanos vía una MIPYME cubana de software como "
     "<b>vendor</b>. Pagas factura de servicios profesionales (no inversión). ZONA GRIS legal "
     "pero hay precedentes (Telesoft Cuba, etc.).<br/>"
     "<b>Pros:</b> Salarios cubanos 1/10 de US. Acceso a talento ingenieril fuerte.<br/>"
     "<b>Contras:</b> Bancos US bloquean wires a Cuba. Pagos requieren creatividad legal. "
     "Riesgo OFAC si OFAC re-clasifica MIPYMEs como GAESA-linked."),

    ("E", "Esperar reset político (3-5 años)",
     "Cuba en colapso terminal. Es posible (no probable) que en 3-5 años haya transición política "
     "que abra la isla a inversión US masiva tipo Vietnam 1995. Si pasa, primeros movers ganan mucho.<br/>"
     "<b>Pros:</b> Si pasa, ROI 10-50x. Conoces el mercado/idioma.<br/>"
     "<b>Contras:</b> NO va a pasar en 1-2 años. Probabilidad de transición pacífica baja. "
     "Más probable: colapso humanitario sin reforma."),
]

for letra, titulo, desc in estrategias:
    row = Table([[
        Paragraph(f"<font size=16 color='#FFFFFF'><b>{letra}</b></font>", BODY_C),
        Paragraph(f"<font color='#002A8F'><b>{titulo}</b></font><br/>"
                  f"<font size=9>{desc}</font>", BODY_L)
    ]], colWidths=[0.55*inch, 6.25*inch])
    row.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0),BLUE),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),8),
        ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("BOX",(0,0),(-1,-1),0.5,colors.HexColor("#E5E7EB")),
    ]))
    elems.append(row)
    elems.append(Spacer(1, 0.06*inch))

elems.append(PageBreak())

# ═══════════════════════ 6. OPINIÓN HONESTA ═══════════════════════
elems.append(Paragraph("6️⃣  MI OPINIÓN HONESTA — ¿VALE LA PENA?", H1))
elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph(
    "<font color='#CF142B'><b>Te voy a ser brutalmente honesto, sin endulzarlo.</b></font>", BODY))

elems.append(Spacer(1, 0.1*inch))

# Verdict box
verd_box = Table([[
    Paragraph(
        "<font size=14 color='#CF142B'><b>NO, no vale la pena invertir en Cuba directamente en 2026.</b></font><br/><br/>"
        "Específicamente para ti (Yoandy), que ya tienes operaciones rentables en TX (Ross Tax + Ross House "
        "Rentals + planes de Jasmine 142-unit acquisition), invertir capital o tiempo en montar negocio "
        "EN Cuba sería un <b>error estratégico</b>.<br/><br/>"
        "<b>Razones específicas:</b><br/>"
        "1. <b>Riesgo OFAC catastrófico</b> — tu LLC en TX puede ser bloqueada por una transacción mal hecha<br/>"
        "2. <b>Capital atrapado</b> — sacar dinero de Cuba es casi imposible<br/>"
        "3. <b>Apagones</b> destruyen márgenes operativos (necesitas generador = +$300/mes diesel)<br/>"
        "4. <b>Sin propiedad real</b> — el estado puede confiscar cuando quiera (ya lo hizo en 1968)<br/>"
        "5. <b>Costo de oportunidad</b> — Texas te da 8-15% cap rate; Cuba te da 0% real + riesgo total<br/>"
        "6. <b>Inestabilidad política</b> — riesgo de transición violenta o restricciones mayores<br/>"
        "7. <b>Sin remedio legal</b> — si te roban, no hay corte, no hay arbitraje, no hay nada",
        BODY_L)
]], colWidths=[6.6*inch])
verd_box.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(0,0),RED_L),
    ("BOX",(0,0),(-1,-1),1,RED),
    ("LEFTPADDING",(0,0),(-1,-1),14),
    ("RIGHTPADDING",(0,0),(-1,-1),14),
    ("TOPPADDING",(0,0),(-1,-1),12),
    ("BOTTOMPADDING",(0,0),(-1,-1),12),
]))
elems.append(verd_box)

elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph("¿Y entonces qué?", H2))
elems.append(Paragraph(
    "<b>SÍ vale la pena</b> aprovechar tu conocimiento del mercado cubano de forma indirecta:", BODY))

por_que = Table([
    [Paragraph("✅ <b>Servicios a la diáspora cubana en TX/Texas</b>", BODY),
     Paragraph("Houston, Dallas, San Antonio tienen comunidades cubanas en rápido crecimiento (~150K). "
               "Tu Ross Tax + Ross House LLC = posicionamiento perfecto. Anuncia en español, "
               "ofrece servicios bilingües, gana confianza cultural. <b>Aquí hay $1-3M/año de revenue real.</b>", BODY)],
    [Paragraph("✅ <b>Apoyar familia cubana con remesas legales</b>", BODY),
     Paragraph("Si tienes familia en la isla: $1,000/quarter es lo legal. Que esa plata les ayude a abrir "
               "su MIPYME a SU nombre. No es inversión tuya, es soporte familiar. ROI emocional alto.", BODY)],
    [Paragraph("✅ <b>Outsourcing IT desde Cuba a tu stack TX</b>", BODY),
     Paragraph("Si te animas: contrata 2-3 developers cubanos vía MIPYME software cubana, paga "
               "$15-25/hora (vs $80-150/hora dev US). Ahorro masivo para tu stack interno (Ross Tax UI, "
               "Ross House app). ZONA GRIS legal pero hay paths conocidos.", BODY)],
    [Paragraph("✅ <b>Esperar y aprender</b>", BODY),
     Paragraph("Sigue las noticias. Si en 2-4 años Cuba abre como Vietnam 1995, tendrás ventaja "
               "cultural + capital acumulado en TX para entrar. Hasta entonces, no quemes capital.", BODY)],
], colWidths=[2.2*inch, 4.6*inch])
por_que.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(0,-1),GREEN_L),
    ("VALIGN",(0,0),(-1,-1),"TOP"),
    ("LEFTPADDING",(0,0),(-1,-1),8),
    ("RIGHTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),6),
    ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ("BOX",(0,0),(-1,-1),0.5,GREEN),
    ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E5E7EB")),
]))
elems.append(por_que)

elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph("Decisión recomendada", H2))
elems.append(Paragraph(
    "<b>Sigue construyendo en Texas.</b> Cierra Jasmine (142 unidades). Cierra contrato April Tax. "
    "Crece Ross Tax con clientela cubana de Houston/Dallas. <b>En 3-5 años, cuando tengas $5-10M en NW "
    "consolidado en TX</b>, y SI Cuba cambia política, podrás entrar con verdadero poder. Pero entrar "
    "ahora con $50-200K es regalar el dinero al estado cubano y a tus competidores en Texas que sí "
    "invertirán esa plata en cap rates locales.<br/><br/>"
    "<b>La verdadera oportunidad no está en Cuba — está en la diáspora cubana en Texas.</b>", BODY))

elems.append(PageBreak())

# ═══════════════════════ 7. CHECKLIST RAPIDO ═══════════════════════
elems.append(Paragraph("7️⃣  CHECKLIST RÁPIDO — Si decides ignorar mi consejo", H1))
elems.append(Spacer(1, 0.1*inch))

elems.append(Paragraph(
    "Si después de leer todo esto aún quieres meterte en negocios EN Cuba, mínimo haz esto antes "
    "de comprometer un dólar:", BODY))

checks = [
    "✅ <b>Consulta abogado OFAC certificado</b> (no abogado de inmigración cualquiera). Costo: $400-700/hora. Antes de tocar 1 USD.",
    "✅ <b>Estructura legal escrita</b> que define qué eres tú (US Person prohibido) vs el operador (residente cubano)",
    "✅ <b>NO mezcles cuentas</b> — tu LLC TX nunca debe tocar dinero proveniente o destinado a Cuba",
    "✅ <b>Documenta TODO</b> — cada remesa familiar, con propósito específico, recibo, etc. Defensa OFAC si auditan.",
    "✅ <b>Verifica que la MIPYME no esté en SDN list</b> ni controlada por entidad sancionada",
    "✅ <b>Plan de salida</b> — antes de poner $1, define: si pasa X, salgo. (X = blackout >30 días, confiscación, sanción nueva, etc.)",
    "✅ <b>Capital máximo en riesgo = lo que puedas perder</b> sin afectar Ross Tax / Ross House",
    "✅ <b>NO firmes nada</b> que ate a Ross House Rentals LLC o Ross Tax con la operación cubana",
    "✅ <b>Diversifica geográfico</b> — máximo 5% de tu net worth expuesto a Cuba",
    "✅ <b>Revisa OFAC semanalmente</b> — las listas SDN cambian. Lo que era legal lunes puede ser felonía martes.",
]
for c in checks:
    elems.append(Paragraph(c, ParagraphStyle("ck", parent=BODY, leftIndent=10, spaceAfter=6)))

elems.append(Spacer(1, 0.25*inch))

elems.append(Paragraph(
    "<font color='#6B7280' size=8>Este reporte es análisis de inteligencia de mercado y NO constituye "
    "asesoría legal, financiera o regulatoria. Las regulaciones US-Cuba cambian frecuentemente. "
    "Consulta un abogado especializado en OFAC/CACR antes de tomar cualquier acción que involucre "
    "transacciones con Cuba o personas/entidades cubanas. Fuentes: ECLAC, BTI Index, WFP, EL PAÍS, "
    "FIU Cuba Study Group, OFAC, Debevoise &amp; Plimpton, Baker McKenzie, Trading Economics, "
    "Cibercuba, Diaz Trade Law, NFIB.<br/>"
    "Ross House Rentals · Business Intelligence · " + datetime.now().strftime("%B %Y") + "</font>",
    BODY_C))

# BUILD
doc = SimpleDocTemplate(OUT, pagesize=letter, rightMargin=0.55*inch, leftMargin=0.55*inch,
                         topMargin=0.55*inch, bottomMargin=0.5*inch,
                         title="Cuba Market Research 2026 — Business Intelligence",
                         author="Ross House Rentals · Business Intelligence")
doc.build(elems)
print(f"✅ PDF generado: {OUT}  ({os.path.getsize(OUT)/1024:.1f} KB)")

# ═══ EMAIL ════════════════════════════════════════════════════════════
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
    FileContent, FileName, FileType, Disposition)

with open(OUT, "rb") as f: pdf_bytes = f.read()
stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

mail = Mail(
    from_email=Email(os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com"), "Ross House · Business Intelligence"),
    to_emails=To("yoandyross@gmail.com"),
    subject="🇨🇺 Mercado Cubano 2026 — Investigación Completa + Mi Opinión Honesta",
)
mail.add_content(Content("text/html", f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto">
  <div style="padding:32px 24px;background:linear-gradient(135deg,#002A8F 0%,#001a5c 100%);border-radius:16px 16px 0 0;color:#fff">
    <div style="font-size:11px;letter-spacing:3px;font-weight:600;opacity:0.85;margin-bottom:8px">🇨🇺 BUSINESS INTELLIGENCE</div>
    <h1 style="margin:0;font-size:26px;line-height:1.2;font-weight:700">
      Cuba Market Research 2026
    </h1>
    <p style="margin:8px 0 0;opacity:0.95;font-size:14px">
      Marco legal · Sectores viables · Restricciones US · Opinión honesta
    </p>
  </div>

  <div style="padding:24px;background:#fff;border:1px solid #E5E7EB;border-top:0;border-radius:0 0 16px 16px">
    <p>Yoandy, esta investigación es <b>brutalmente honesta</b>. No la voy a endulzar. El TL;DR:</p>

    <div style="background:#FEE2E2;padding:16px;border-radius:8px;border-left:4px solid #CF142B;margin:20px 0">
      <div style="color:#CF142B;font-weight:bold;font-size:16px;margin-bottom:6px">⚠️ Veredicto: NO vale la pena invertir en Cuba directamente en 2026.</div>
      <div style="font-size:13px;color:#7F1D1D">
        Para ti específicamente (cubano-americano con LLC en TX y operaciones rentables): el riesgo
        OFAC + apagones + inestabilidad política + capital atrapado = ROI ajustado al riesgo NEGATIVO.
      </div>
    </div>

    <h3 style="color:#002A8F">📊 Lo que descubrí en la investigación</h3>
    <table style="width:100%;border-collapse:collapse;margin:14px 0">
      <tr>
        <td style="padding:10px;background:#FEF3C7;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:18px;font-weight:bold;color:#D97706">14+ hrs</div>
          <div style="font-size:9px;color:#6B7280">apagón diario</div>
        </td>
        <td style="padding:10px;background:#FEE2E2;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:18px;font-weight:bold;color:#CF142B">125</div>
          <div style="font-size:9px;color:#6B7280">actividades prohibidas</div>
        </td>
        <td style="padding:10px;background:#DBEAFE;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:18px;font-weight:bold;color:#002A8F">$1,000</div>
          <div style="font-size:9px;color:#6B7280">cap/quarter remesa</div>
        </td>
        <td style="padding:10px;background:#ECFDF5;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:18px;font-weight:bold;color:#059669">$2B+</div>
          <div style="font-size:9px;color:#6B7280">imports MIPYME 2025</div>
        </td>
      </tr>
    </table>

    <h3 style="color:#002A8F">🎯 Donde SÍ está la verdadera oportunidad para ti</h3>
    <p>La <b>diáspora cubana en Texas</b>. ~150K cubanos en Houston/Dallas/SA, creciendo rápido,
    con necesidades de tax services, real estate, immigration paperwork, business advisory.
    <b>Tu Ross Tax + Ross House ya está posicionado.</b> Aquí hay $1-3M/año de revenue real,
    sin tocar OFAC ni embargos.</p>

    <h3 style="color:#002A8F">📑 El PDF cubre 7 secciones</h3>
    <ol>
      <li><b>Contexto económico Cuba 2026</b> — GDP, inflación, blackouts, food crisis</li>
      <li><b>Marco legal cubano</b> — MIPYME, 125 actividades prohibidas, Decreto-Ley 114/2025</li>
      <li><b>Restricciones US</b> — Trump EO 14404 (mayo 2026), OFAC, secondary sanctions</li>
      <li><b>Ranking de 10 sectores viables</b> con capital, márgenes y riesgos</li>
      <li><b>5 estrategias para diáspora</b> — desde legal hasta zona gris</li>
      <li><b>Mi opinión honesta detallada</b> — por qué NO directamente, qué SÍ indirectamente</li>
      <li><b>Checklist de 10 pasos</b> si decides ignorar mi consejo</li>
    </ol>

    <h3 style="color:#002A8F">⚠️ Lo que NO sabías (o quizás sí)</h3>
    <ul>
      <li><b>EO 14404 de mayo 2026</b> creó SANCIONES SECUNDARIAS — bancos extranjeros que tocan Cuba pierden acceso US. Si Ross House toca una transacción mal hecha → OFAC bloquea TODO.</li>
      <li>El "loophole MIPYME" de Biden está CERRADO desde enero 2026.</li>
      <li>Multas OFAC pueden llegar a <b>$1M + 20 años cárcel</b> por violación CACR penal.</li>
      <li>Cuentapropistas (autoempleados) EXCLUIDOS de las nuevas empresas mixtas estado-privado.</li>
    </ul>

    <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0">
    <p style="font-size:11px;color:#6B7280">Generado: {stamp}<br/>
    Ross House Rentals · Business Intelligence · Análisis basado en fuentes públicas (ECLAC, BTI, WFP, OFAC, FIU Cuba Study Group, etc.).<br/>
    NO constituye asesoría legal, financiera ni regulatoria. Consulta abogado OFAC antes de cualquier acción.</p>
  </div>
</div>
"""))
mail.attachment = Attachment(
    FileContent(base64.b64encode(pdf_bytes).decode()),
    FileName("Cuba_Market_Research_2026.pdf"),
    FileType("application/pdf"),
    Disposition("attachment"),
)
sg = SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
resp = sg.send(mail)
print(f"✅ Email enviado (status={resp.status_code}) → yoandyross@gmail.com")
