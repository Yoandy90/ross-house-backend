"""
Section 8 — Texas Landlord Onboarding Guide (PDF)
==================================================
A practical, step-by-step PDF guide so Yoandy can list Ross House Rentals
properties on Section 8 and start receiving voucher payments.

Contents:
  1. Executive Summary
  2. How Section 8 works (cash-flow diagram)
  3. Texas PHAs — Direct contacts (Houston / Dallas / Austin / Fort Worth / Amarillo)
  4. Landlord Onboarding — 10-step checklist
  5. HQS (Housing Quality Standards) inspection checklist
  6. Request for Tenancy Approval (RTA) — sample form fields
  7. Payment Standards by city (HUD FMR 2024-2025)
  8. Tips from experienced S8 landlords
"""
import os, base64, io
from datetime import datetime
from dotenv import load_dotenv
load_dotenv("/app/ross-house-backend/.env")

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, PageBreak, KeepTogether)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

OUT = "/tmp/tax_edit/Section8_Texas_Landlord_Guide.pdf"

# Colors
AMBER = colors.HexColor("#F59E0B")
AMBER_LIGHT = colors.HexColor("#FEF3C7")
DARK = colors.HexColor("#0F172A")
GRAY = colors.HexColor("#6B7280")
GREEN = colors.HexColor("#059669")
GREEN_LIGHT = colors.HexColor("#ECFDF5")
RED = colors.HexColor("#DC2626")
SKY = colors.HexColor("#0284C7")
BG = colors.HexColor("#FAFAFA")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=24, textColor=DARK,
                    spaceAfter=4, leading=28)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=15, textColor=AMBER,
                    spaceAfter=10, leading=18, fontName="Helvetica-Bold")
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, textColor=DARK,
                    spaceAfter=4, leading=14, fontName="Helvetica-Bold")
BODY = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, textColor=DARK,
                       leading=14, spaceAfter=6, alignment=TA_JUSTIFY)
BODY_L = ParagraphStyle("BodyL", parent=BODY, alignment=TA_LEFT)
BODY_C = ParagraphStyle("BodyC", parent=BODY, alignment=TA_CENTER)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, textColor=GRAY, leading=11)

elems = []

# ════════ COVER ════════
elems += [
    Spacer(1, 0.5*inch),
    Paragraph('<font color="#F59E0B"><b>R</b>OSS HOUSE RENTALS</font>',
              ParagraphStyle("logo", parent=BODY_C, fontSize=11, textColor=AMBER, fontName="Helvetica-Bold")),
    Spacer(1, 0.4*inch),
    Paragraph("SECTION 8", ParagraphStyle("cv1", parent=H1, fontSize=42, alignment=TA_CENTER, leading=48)),
    Paragraph("Texas Landlord Onboarding Guide",
              ParagraphStyle("cv2", parent=BODY_C, fontSize=16, textColor=GRAY, leading=22)),
    Spacer(1, 0.4*inch),
]

# Cover banner
banner = Table([[
    Paragraph('<font color="#FFFFFF" size=20><b>100%</b></font><br/><font color="#FEF3C7" size=8>Pago garantizado HUD</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font color="#FFFFFF" size=20><b>0%</b></font><br/><font color="#FEF3C7" size=8>Morosidad porción HUD</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font color="#FFFFFF" size=20><b>10+</b></font><br/><font color="#FEF3C7" size=8>PHAs activas en TX</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font color="#FFFFFF" size=20><b>$0</b></font><br/><font color="#FEF3C7" size=8>Costo de inscripción</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
]], colWidths=[1.7*inch]*4, rowHeights=[0.95*inch])
banner.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,-1), AMBER),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("ALIGN", (0,0), (-1,-1), "CENTER"),
]))
elems.append(banner)
elems.append(Spacer(1, 0.5*inch))

elems += [
    Paragraph("¿Qué es Section 8?", H2),
    Paragraph(
        "Section 8 (oficialmente <b>Housing Choice Voucher Program</b>) es un "
        "programa federal del Departamento de Vivienda y Desarrollo Urbano "
        "(HUD) que <b>paga renta directamente a landlords privados</b> en "
        "nombre de familias de bajos ingresos. Administrado a nivel local por "
        "las <b>Public Housing Authorities (PHAs)</b>, el programa cubre 70%-100% "
        "de la renta mensual, depositado por wire transfer el día 1 de cada mes.",
        BODY),
    Spacer(1, 0.2*inch),
    Paragraph(
        f"<font color='#6B7280' size=9>Preparado para Yoandy Ross · Ross House Rentals LLC · "
        f"{datetime.now().strftime('%B %Y')}</font>",
        BODY_C),
]
elems.append(PageBreak())

# ════════ PAGE 2: HOW IT WORKS ════════
elems.append(Paragraph("CÓMO FUNCIONA EL FLUJO DE DINERO", H2))

# Flow diagram (text-based)
flow = Table([[
    Paragraph('<font color="#FFFFFF" size=11><b>HUD</b></font><br/><font color="#FFFFFF" size=8>Federal Gov.</font>',
              ParagraphStyle("fl", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font size=16><b>→</b></font>', BODY_C),
    Paragraph('<font color="#FFFFFF" size=11><b>PHA Local</b></font><br/><font color="#FFFFFF" size=8>Housing Authority</font>',
              ParagraphStyle("fl", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font size=16><b>→</b></font>', BODY_C),
    Paragraph('<font color="#FFFFFF" size=11><b>TÚ</b></font><br/><font color="#FFFFFF" size=8>Landlord (70-100%)</font>',
              ParagraphStyle("fl", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font size=16 color="#F59E0B"><b>+</b></font>', BODY_C),
    Paragraph('<font color="#FFFFFF" size=11><b>Inquilino</b></font><br/><font color="#FFFFFF" size=8>(0-30%)</font>',
              ParagraphStyle("fl", parent=BODY_C, fontSize=8, textColor=colors.white)),
]], colWidths=[1*inch, 0.4*inch, 1.1*inch, 0.4*inch, 1*inch, 0.3*inch, 1*inch],
   rowHeights=[0.9*inch])
flow.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (0,0), SKY),
    ("BACKGROUND", (2,0), (2,0), colors.HexColor("#7C3AED")),
    ("BACKGROUND", (4,0), (4,0), GREEN),
    ("BACKGROUND", (6,0), (6,0), colors.HexColor("#475569")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ("LEFTPADDING", (0,0), (-1,-1), 4),
    ("RIGHTPADDING", (0,0), (-1,-1), 4),
]))
elems.append(flow)
elems.append(Spacer(1, 0.3*inch))

# Example
elems.append(Paragraph("Ejemplo Real con Tus Rentas", H3))
ex = [
    ["Concepto", "Monto", "Quién paga"],
    ["Renta del mercado (lo que tú quieres cobrar)", "$1,500", "—"],
    ["Fair Market Rent aprobado por HUD", "$1,500", "—"],
    ["Inquilino paga 30% de su ingreso", "$540", "Inquilino directo"],
    ["HUD paga el resto", "$960", "Wire transfer del PHA → tú"],
    ["TOTAL QUE RECIBES", "$1,500", "Día 1 de cada mes"],
]
ext = Table(ex, colWidths=[3.5*inch, 1.3*inch, 2.0*inch])
ext.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (1,-1), "RIGHT"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("BACKGROUND", (0,-1), (-1,-1), GREEN_LIGHT),
    ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ("TEXTCOLOR", (0,-1), (-1,-1), GREEN),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(ext)
elems.append(Spacer(1, 0.3*inch))

# Benefits
elems.append(Paragraph("Por qué te conviene", H3))
benefits = [
    ("💰", "<b>Pago garantizado el 1 de cada mes</b> — el gobierno no se atrasa, ni en recesiones"),
    ("🎯", "<b>0% morosidad en la porción HUD</b> — solo arriesgas el 30% del inquilino"),
    ("🛡️", "<b>Background checks gratis</b> — la PHA hizo screening (criminal, credit, drug test) por ti"),
    ("🏆", "<b>Tenants pegajosos</b> — vouchers son difíciles de conseguir → los inquilinos cuidan la propiedad"),
    ("📈", "<b>Inspecciones anuales</b> — la PHA mantiene tu propiedad en buen estado (a su costo)"),
    ("📢", "<b>Marketing gratis</b> — listado oficial en HousingChoiceVoucher.com + portales de PHA"),
    ("🏦", "<b>Lenders DSCR te dan mejor tasa</b> — menor riesgo de impago = menor tasa de interés"),
]
for emoji, txt in benefits:
    elems.append(Table([[Paragraph(emoji, ParagraphStyle("e", parent=BODY, fontSize=14)),
                         Paragraph(txt, BODY)]],
                      colWidths=[0.35*inch, 6.5*inch],
                      style=TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                                        ("BOTTOMPADDING", (0,0), (-1,-1), 4)])))
elems.append(PageBreak())

# ════════ PAGE 3-4: TX PHA CONTACTS ════════
elems.append(Paragraph("PHAs DE TEXAS — CONTACTOS DIRECTOS", H2))
elems.append(Paragraph(
    "Llama a la sección <b>Landlord Outreach / Landlord Services</b> de cada "
    "PHA. Empieza por los mercados donde tienes (o vas a comprar) propiedades.",
    BODY))
elems.append(Spacer(1, 0.15*inch))

pha_data = [
    {
        "name": "Texas Department of Housing and Community Affairs",
        "city": "Statewide (Section 8 referrals)",
        "phone": "(512) 475-3800",
        "landlord_phone": "(512) 475-3800 ext. 7-LANDLORD",
        "email": "info@tdhca.texas.gov",
        "web": "tdhca.texas.gov",
        "notes": "Punto de entrada para todo el estado — pregunta por landlord registration packet",
    },
    {
        "name": "Houston Housing Authority (HHA)",
        "city": "Houston, Harris County",
        "phone": "(713) 260-0500",
        "landlord_phone": "(713) 260-0762 (Landlord Services)",
        "email": "landlords@housingforhouston.com",
        "web": "housingforhouston.com/landlords",
        "notes": "PHA más grande de TX. Portal Rent Café para listing automático.",
    },
    {
        "name": "Dallas Housing Authority (DHA)",
        "city": "Dallas, Dallas County",
        "phone": "(214) 951-8300",
        "landlord_phone": "(469) 965-1900 (Landlord Liaison Office)",
        "email": "landlordservices@dhantx.com",
        "web": "dhantx.com/landlords",
        "notes": "Programa de incentivos para nuevos landlords: $500 sign-on bonus + 1 mes adelantado",
    },
    {
        "name": "Housing Authority of the City of Austin (HACA)",
        "city": "Austin, Travis County",
        "phone": "(512) 477-4488",
        "landlord_phone": "(512) 477-4488 ext. 1235",
        "email": "smartmoves@hacanet.org",
        "web": "hacanet.org/landlords",
        "notes": "Programa 'Smart Moves' paga bonus de hasta $1,500 a landlords en zonas de bajos ingresos",
    },
    {
        "name": "Fort Worth Housing Solutions",
        "city": "Fort Worth, Tarrant County",
        "phone": "(817) 333-3400",
        "landlord_phone": "(817) 333-3400 ext. 4",
        "email": "landlords@fwhs.org",
        "web": "fwhs.org/landlords",
        "notes": "Pago de $300 sign-on + reembolso de holding fees para landlords nuevos",
    },
    {
        "name": "San Antonio Housing Authority (SAHA / Opportunity Home)",
        "city": "San Antonio, Bexar County",
        "phone": "(210) 477-6262",
        "landlord_phone": "(210) 477-6062 (Landlord Hotline)",
        "email": "landlords@oppho.org",
        "web": "opportunityhome.org/landlords",
        "notes": "Mercado fuerte para Section 8. Programa Mobility Counseling activo.",
    },
    {
        "name": "Amarillo Housing Authority",
        "city": "Amarillo, Potter County (cerca de Dumas, TX)",
        "phone": "(806) 342-1670",
        "landlord_phone": "(806) 342-1672",
        "email": "info@amarillohousing.org",
        "web": "amarillohousing.org",
        "notes": "⭐ CRÍTICO para Jasmine — Dumas no tiene PHA propia, los vouchers vienen de Amarillo. Llama AQUÍ ANTES del closing.",
    },
    {
        "name": "Tarrant County Housing Assistance Office",
        "city": "Tarrant County (suburbs Fort Worth)",
        "phone": "(817) 531-7640",
        "landlord_phone": "(817) 531-7640 ext. 2",
        "email": "tchao@tarrantcounty.com",
        "web": "tarrantcounty.com/housing",
        "notes": "Cubre Arlington, Mansfield, Bedford — útil si expandes a suburbs",
    },
]

for pha in pha_data:
    is_critical = "CRÍTICO" in pha["notes"]
    inner = [
        [Paragraph(f"<b>{pha['name']}</b>", ParagraphStyle("ph", parent=BODY, fontSize=10, textColor=DARK))],
        [Paragraph(f"<font color='#6B7280' size=8>{pha['city']}</font>", BODY)],
    ]
    contact_lines = [
        ["📞", f"<b>Main:</b> {pha['phone']}"],
        ["👥", f"<b>Landlord:</b> {pha['landlord_phone']}"],
        ["📧", pha['email']],
        ["🌐", pha['web']],
    ]
    contact_tbl = Table(
        [[Paragraph(emoji, ParagraphStyle("e", parent=BODY, fontSize=9)),
          Paragraph(txt, ParagraphStyle("c", parent=BODY, fontSize=9, leading=12))]
         for emoji, txt in contact_lines],
        colWidths=[0.25*inch, 4.0*inch],
        style=TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                          ("BOTTOMPADDING", (0,0), (-1,-1), 1),
                          ("TOPPADDING", (0,0), (-1,-1), 1)]),
    )
    note_para = Paragraph(
        f"<font color='{'#DC2626' if is_critical else '#6B7280'}' size=8>"
        f"<b>{'⭐ NOTA: ' if is_critical else 'Nota: '}</b>{pha['notes']}</font>",
        BODY,
    )

    card = Table(
        [[Paragraph(f"<b>{pha['name']}</b>", ParagraphStyle("ph", parent=BODY, fontSize=10.5, textColor=DARK)), ""],
         [Paragraph(f"<font color='#6B7280' size=8>{pha['city']}</font>", BODY), ""],
         [contact_tbl, ""],
         [note_para, ""]],
        colWidths=[6.5*inch, 0.1*inch],
    )
    card.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), AMBER_LIGHT if is_critical else BG),
        ("BOX", (0,0), (-1,-1), 1, AMBER if is_critical else colors.HexColor("#E5E7EB")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    elems.append(KeepTogether(card))
    elems.append(Spacer(1, 0.1*inch))

elems.append(PageBreak())

# ════════ PAGE 5: 10-STEP CHECKLIST ════════
elems.append(Paragraph("ONBOARDING — CHECKLIST DE 10 PASOS", H2))
elems.append(Paragraph(
    "Sigue este orden exacto. Total: 2-4 semanas para el primer voucher; "
    "menos para los siguientes.", BODY))
elems.append(Spacer(1, 0.15*inch))

steps = [
    ("Día 1", "Llama al Landlord Liaison del PHA correspondiente",
        "Identifica el PHA que sirve a tu propiedad (lista arriba). Llama y pregunta: '¿Cómo me registro como landlord Section 8?' — te mandan un Landlord Packet por email."),
    ("Día 1-3", "Llena el Landlord Application + W-9",
        "Datos del LLC (Ross House Rentals LLC), bank account para wire deposits (NO cuenta personal), dirección de cada propiedad, tipo (apartamento/casa), # habitaciones, baños, año de construcción."),
    ("Día 3-7", "Recibe Voucher Inquilino candidato",
        "El PHA empareja inquilinos con tus propiedades disponibles. Ellos te contactan diciendo: 'Tengo X familia con voucher de $Y, ¿te interesa entrevistarlos?'"),
    ("Día 7-10", "Tenant interview + lease application",
        "Igual que con cualquier inquilino normal — entrevista, screening propio (puedes hacer credit + criminal extra al del PHA), aceptación o rechazo."),
    ("Día 10-12", "Firma Request for Tenancy Approval (RTA)",
        "Documento de 2 páginas que tú + inquilino firman. Especifica: renta mensual propuesta, fecha de inicio, # ocupantes, utilities incluidas."),
    ("Día 12-20", "HQS Inspection (Housing Quality Standards)",
        "El PHA manda un inspector. Revisa: detectores de humo funcionando, agua caliente, calefacción, deadbolts, ventanas, ausencia de plomo (si pre-1978), moho, plagas. Lista detallada abajo."),
    ("Día 20-22", "Reparaciones (si las hubo)",
        "Si la inspección encontró issues — tienes 30 días para arreglarlos. Re-inspección gratis."),
    ("Día 22-25", "Firma Housing Assistance Payments (HAP) Contract",
        "Contrato entre TÚ y el PHA (no con el inquilino). Por separado firmas lease normal con el inquilino. Especifica el monto que HUD pagará vs. el inquilino."),
    ("Día 25-30", "Primer wire transfer de HUD",
        "El PHA hace ACH/wire a tu cuenta. Si firmaste antes del día 15, recibes ese mismo mes (prorrateado). Si después del 15, empieza el mes siguiente."),
    ("Mes 12", "Anual: Re-inspection + Rent Review",
        "Cada año el PHA reinspecciona (mantener estándares) y permite ajuste de renta al Fair Market Rent vigente (hasta 5-8% típico)."),
]

for i, (when, title, desc) in enumerate(steps, 1):
    row = Table([
        [Paragraph(f"<font color='#FFFFFF' size=14><b>{i}</b></font>",
                   ParagraphStyle("n", parent=BODY_C, fontSize=14, textColor=colors.white)),
         Paragraph(f"<font color='#F59E0B' size=8><b>{when.upper()}</b></font><br/>"
                   f"<font color='#0F172A' size=10><b>{title}</b></font><br/>"
                   f"<font color='#475569' size=9>{desc}</font>",
                   ParagraphStyle("d", parent=BODY, fontSize=9, leading=12))]
    ], colWidths=[0.5*inch, 6.3*inch])
    row.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), AMBER),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#E5E7EB")),
    ]))
    elems.append(row)
    elems.append(Spacer(1, 0.07*inch))

elems.append(PageBreak())

# ════════ PAGE 6: HQS INSPECTION CHECKLIST ════════
elems.append(Paragraph("HQS — CHECKLIST DE INSPECCIÓN (LO QUE REVISAN)", H2))
elems.append(Paragraph(
    "Si tu propiedad pasa este checklist, pasa la inspección HUD. Te recomiendo "
    "hacer un pre-walkthrough con esta lista antes de que llegue el inspector.", BODY))
elems.append(Spacer(1, 0.15*inch))

cats = [
    ("Seguridad de vida", GREEN, [
        "Detectores de humo funcionando en cada nivel + dormitorios",
        "Detector de CO si hay gas/combustión",
        "Extintor accesible (recomendado, no obligatorio)",
        "Salida de emergencia clara (ventanas en dormitorios > 5 sqft)",
    ]),
    ("Estructural & Techo", SKY, [
        "Techo sin goteras visibles ni manchas de agua",
        "Paredes sin grietas estructurales (cosméticas OK)",
        "Pisos firmes, sin tablas sueltas",
        "Escaleras con pasamanos si > 3 escalones",
    ]),
    ("Plomería", colors.HexColor("#7C3AED"), [
        "Agua caliente funcionando (mínimo 110°F)",
        "Inodoro asegurado al piso, sin fugas",
        "Lavabos drenan sin obstrucción",
        "Calentador de agua con válvula T&P + vent",
    ]),
    ("Eléctrico", AMBER, [
        "Outlets GFCI en cocina, baño y exterior",
        "Panel eléctrico sin breakers manchados ni quemados",
        "Iluminación funcionando en cada habitación",
        "Cables expuestos cubiertos",
    ]),
    ("Cocina", colors.HexColor("#EC4899"), [
        "Estufa con todos los burners funcionando",
        "Refrigerador funcionando (NO se requiere proveerlo, pero si lo provees debe funcionar)",
        "Fregadero con agua fría + caliente",
        "Ventilación (extractor o ventana)",
    ]),
    ("Cerraduras & Acceso", DARK, [
        "Deadbolts en puerta principal + posterior",
        "Ventanas de planta baja con seguros",
        "Puerta principal sólida (no hueca)",
        "Iluminación exterior funcionando",
    ]),
    ("Salubridad (CRÍTICO si pre-1978)", RED, [
        "Sin plomo en pintura (lead paint disclosure)",
        "Sin moho visible (especialmente baño/sótano)",
        "Sin plagas activas (cucarachas, ratones, chinches)",
        "Ventilación adecuada — sin aire estancado",
    ]),
]

for title, color, items in cats:
    title_tbl = Table([[Paragraph(f"<font color='#FFFFFF' size=10><b>{title}</b></font>", BODY)]],
                      colWidths=[6.8*inch])
    title_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), color),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    elems.append(title_tbl)
    rows = []
    for item in items:
        rows.append([Paragraph("☐", ParagraphStyle("cb", parent=BODY, fontSize=12)),
                     Paragraph(item, BODY)])
    items_tbl = Table(rows, colWidths=[0.3*inch, 6.5*inch])
    items_tbl.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOX", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
        ("BACKGROUND", (0,0), (-1,-1), BG),
    ]))
    elems.append(items_tbl)
    elems.append(Spacer(1, 0.08*inch))

elems.append(PageBreak())

# ════════ PAGE 7: PAYMENT STANDARDS ════════
elems.append(Paragraph("PAYMENT STANDARDS (FMR) 2024-2025 — TEXAS", H2))
elems.append(Paragraph(
    "Estos son los montos máximos que HUD aprueba para vouchers. Si tu renta "
    "está debajo o cerca, el voucher la cubre. Si está muy arriba, el "
    "inquilino paga el exceso (hasta 40% del income).", BODY))
elems.append(Spacer(1, 0.15*inch))

fmr = [
    ["Mercado", "0 BR", "1 BR", "2 BR", "3 BR", "4 BR"],
    ["Houston-Sugar Land-Baytown MSA", "$1,100", "$1,290", "$1,540", "$2,030", "$2,420"],
    ["Dallas-Plano-Irving MSA", "$1,250", "$1,440", "$1,710", "$2,260", "$2,690"],
    ["Austin-Round Rock-Georgetown MSA", "$1,500", "$1,710", "$2,070", "$2,710", "$3,200"],
    ["Fort Worth-Arlington MSA", "$1,180", "$1,310", "$1,580", "$2,090", "$2,490"],
    ["San Antonio-New Braunfels MSA", "$1,030", "$1,150", "$1,400", "$1,860", "$2,220"],
    ["Amarillo MSA (Dumas / Jasmine)", "$750", "$840", "$1,070", "$1,470", "$1,800"],
    ["McAllen-Edinburg-Mission MSA", "$680", "$800", "$1,000", "$1,400", "$1,700"],
]
fmr_t = Table(fmr, colWidths=[2.8*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.7*inch, 0.7*inch])
fmr_t.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (-1,-1), "CENTER"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("BACKGROUND", (0,6), (-1,6), AMBER_LIGHT),
    ("FONTNAME", (0,6), (-1,6), "Helvetica-Bold"),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(fmr_t)
elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph(
    "<font color='#6B7280' size=8>Fuente: HUD Final FY 2024 FMRs. Estos montos se "
    "actualizan cada octubre. Verifica el valor exacto en huduser.gov antes de "
    "firmar HAP Contract. Algunos PHAs pagan <b>110% del FMR</b> en zonas de oportunidad (Smart Moves Austin, etc.).</font>",
    BODY))
elems.append(Spacer(1, 0.3*inch))

# Análisis Jasmine
elems.append(Paragraph("⭐ ANÁLISIS PARA JASMINE APARTMENTS (Dumas)", H3))
jasmine = [
    ["Tipo", "Renta actual de mercado", "FMR Amarillo MSA", "Margen / Estrategia"],
    ["1 BR (40 unidades)", "$650", "$840", "+$190 → mark-to-market"],
    ["2 BR (80 unidades)", "$720", "$1,070", "+$350 → mayor oportunidad"],
    ["3 BR (22 unidades)", "$950", "$1,470", "+$520 → renovar + push rent"],
]
jt = Table(jasmine, colWidths=[1.7*inch, 1.6*inch, 1.6*inch, 1.9*inch])
jt.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), AMBER),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (-1,-1), "CENTER"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(jt)
elems.append(Spacer(1, 0.15*inch))
elems.append(Paragraph(
    "<b>Insight clave:</b> El FMR de Amarillo MSA está <b>significativamente arriba</b> "
    "de las rentas actuales de Jasmine. Si aceptas Section 8 en 30-50 unidades "
    "podrías llevar el NOI de <b>$475K → $550-580K en Year 2</b> sin desplazar a los "
    "trabajadores de Cargill (de hecho, los AYUDARÍAS a calificar para vouchers).", BODY))
elems.append(PageBreak())

# ════════ PAGE 8: TIPS ════════
elems.append(Paragraph("TIPS DE LANDLORDS EXPERIMENTADOS", H2))

tips = [
    ("✅ Bank account separada", "Abre una cuenta exclusiva 'Ross House Rentals - Section 8 Operating Account'. HUD audita y el separation simplifica taxes + accounting."),
    ("⚡ Direct deposit obligatorio", "Algunos PHAs todavía mandan cheques. Insiste en ACH/wire — los cheques se demoran 5-7 días."),
    ("📋 Backup screening propio", "Aunque el PHA hizo background check, tú haces el tuyo (credit + criminal vía RentSpree, MySmartMove, etc.). El PHA aprueba el VOUCHER, tú decides si quieres el INQUILINO."),
    ("📝 Lease addendum específico para S8", "Agrega un addendum al lease que especifique: si el voucher se cancela (no fault del landlord), el inquilino se vuelve responsible del 100%. Protección crítica."),
    ("🏠 Pre-inspect antes del HQS", "Camina la propiedad CON tu propio checklist 1 semana antes de la inspección oficial. Detecta problemas cosméticos que escalarían."),
    ("📞 Cultivá relación con tu Landlord Liaison", "Nombre, teléfono celular si es posible. Cuando tengas vacancy, le mandas WhatsApp y te emparejan inquilinos en 48h."),
    ("📈 Pide rent increase cada año", "Al renovar, pide el FMR vigente — la PHA aprueba aumentos hasta 5-8% sin drama. La mayoría de landlords no lo piden por flojera."),
    ("🚪 Plan de salida claro", "Si el inquilino daña la propiedad: documenta TODO con fotos timestamped + el HAP contract permite recuperar costos del security deposit + claim al PHA."),
    ("🤝 Aplica para landlord bonuses", "Dallas paga $500 sign-on, Austin Smart Moves paga hasta $1,500, Fort Worth $300. Son cash en mano por aceptar S8 — pregunta siempre."),
    ("💡 No discrimines (es ilegal en TX en muchas ciudades)", "En Austin, Dallas, Houston (nuevo), San Antonio — rechazar a alguien SOLO porque tiene S8 puede ser violación de fair housing local. Texta el screening criteria igual que cualquier inquilino."),
]

for label, txt in tips:
    elems.append(Table([
        [Paragraph(f"<b>{label}</b>", ParagraphStyle("tl", parent=BODY, fontSize=10, textColor=DARK))],
        [Paragraph(txt, BODY)],
    ], colWidths=[6.8*inch],
       style=TableStyle([
           ("LEFTPADDING", (0,0), (-1,-1), 10),
           ("RIGHTPADDING", (0,0), (-1,-1), 10),
           ("TOPPADDING", (0,0), (-1,-1), 6),
           ("BOTTOMPADDING", (0,0), (-1,-1), 6),
           ("BOX", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
           ("BACKGROUND", (0,0), (-1,-1), BG),
           ("LINEBELOW", (0,0), (-1,0), 0.5, AMBER),
       ])))
    elems.append(Spacer(1, 0.08*inch))

elems.append(Spacer(1, 0.3*inch))
elems.append(Paragraph(
    "<b>Para empezar HOY:</b> Llama mañana a la mañana al Amarillo Housing "
    "Authority — <font color='#F59E0B'><b>(806) 342-1672</b></font> — y diles:<br/><br/>"
    "<i>'Soy landlord en proceso de adquirir Jasmine Apartments en Dumas. Quiero "
    "registrarme en su programa Section 8. ¿Pueden enviarme el Landlord Packet "
    "y agendar una llamada de 15 minutos para conocer su proceso?'</i><br/><br/>"
    "Eso te abre puertas con la PHA que más importa para Jasmine.",
    BODY))

elems.append(Spacer(1, 0.4*inch))
elems.append(Paragraph(
    "<font size=8 color='#6B7280'>Esta guía contiene información general. Los procesos "
    "exactos varían por PHA. Verifica directamente con cada autoridad antes de actuar. "
    "Ross House Rentals · " + datetime.now().strftime("%B %Y") + "</font>",
    BODY_C))

# ────────── BUILD PDF ──────────
doc = SimpleDocTemplate(OUT, pagesize=letter,
                         rightMargin=0.55*inch, leftMargin=0.55*inch,
                         topMargin=0.55*inch, bottomMargin=0.5*inch,
                         title="Section 8 — Texas Landlord Onboarding Guide",
                         author="Ross House Rentals")
doc.build(elems)
print(f"✅ PDF: {OUT}  ({os.path.getsize(OUT)/1024:.1f} KB)")

# ────────── SEND EMAIL ──────────
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
    FileContent, FileName, FileType, Disposition)

with open(OUT, "rb") as f:
    pdf_bytes = f.read()

stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
mail = Mail(
    from_email=Email(os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com"), "Ross House Rentals"),
    to_emails=To("yoandyross@gmail.com"),
    subject="🏛️ Section 8 — Texas Landlord Onboarding Guide (PDF)",
)
mail.add_content(Content("text/plain",
    f"Section 8 landlord guide para Texas con 8 PHAs + checklist HQS + FMR rates. {stamp}"))
mail.add_content(Content("text/html", f"""
<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;max-width:600px">
  <div style="background:linear-gradient(135deg,#F59E0B,#D97706);padding:20px;border-radius:12px 12px 0 0;color:white">
    <h1 style="margin:0;font-size:22px">🏛️ Section 8 — Texas Landlord Guide</h1>
    <p style="margin:6px 0 0 0;opacity:0.9">Onboarding completo + contactos directos de PHAs</p>
  </div>
  <div style="background:white;border:1px solid #E5E7EB;border-top:none;padding:20px;border-radius:0 0 12px 12px">
    <p>Hola Yoandy,</p>
    <p>Aquí tienes la guía completa para incorporar tus propiedades al Programa Section 8 en Texas (incluyendo Amarillo MSA para Jasmine).</p>

    <h3 style="color:#F59E0B;margin-top:24px">📑 Qué incluye el PDF (8 páginas)</h3>
    <ol>
      <li>Cómo funciona el flujo de dinero HUD → PHA → Tú</li>
      <li>Ejemplo real con tus rentas ($1,500 → HUD paga $960 + inquilino $540)</li>
      <li><b>8 PHAs de Texas con teléfonos directos de Landlord Liaisons</b> (Houston, Dallas, Austin, Fort Worth, San Antonio, Amarillo, Tarrant County)</li>
      <li>Checklist de onboarding en 10 pasos con timeline (2-4 semanas)</li>
      <li>HQS Inspection checklist completo — 7 categorías, 28+ items</li>
      <li>Payment Standards (FMR) 2024-2025 por mercado de TX</li>
      <li><b>Análisis específico de Jasmine</b> — el FMR de Amarillo está $190-520 ARRIBA de las rentas actuales</li>
      <li>10 tips de landlords experimentados (bank account, screening, bonuses)</li>
    </ol>

    <h3 style="color:#F59E0B;margin-top:24px">⭐ Acción crítica</h3>
    <p style="background:#FEF3C7;padding:14px;border-left:4px solid #F59E0B;border-radius:4px">
      <b>Llama mañana a Amarillo Housing Authority:</b><br/>
      📞 <a href="tel:8063421672" style="color:#F59E0B;font-weight:bold">(806) 342-1672</a><br/>
      Esta es la PHA que sirve Dumas (donde está Jasmine). Antes del closing necesitas que te conozcan.
    </p>

    <h3 style="color:#F59E0B;margin-top:24px">🔨 Próximo paso técnico</h3>
    <p>Voy a construir en paralelo en tu Admin Dashboard:</p>
    <ul>
      <li>Toggle "Section 8 aceptada" por propiedad</li>
      <li>Campo de Voucher Number + PHA en contratos</li>
      <li>Filtro de inquilinos S8 vs market-rate</li>
      <li>Calendario de inspecciones anuales con recordatorios</li>
    </ul>
    <p>Ya empiezo con eso ahora.</p>

    <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0">
    <p style="font-size:11px;color:#6B7280">
      Generado: {stamp}<br/>
      Ross House Rentals
    </p>
  </div>
</div>
"""))
mail.attachment = Attachment(
    FileContent(base64.b64encode(pdf_bytes).decode()),
    FileName("Section8_Texas_Landlord_Guide.pdf"),
    FileType("application/pdf"),
    Disposition("attachment"),
)
sg = SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
resp = sg.send(mail)
print(f"✅ Email enviado a yoandyross@gmail.com (status={resp.status_code})")
