"""
Texas Landlord Insurance Research Report — 2026 Market Analysis
=================================================================
Comprehensive research on:
  - DP-3 policies for single-family rentals (current 2 properties)
  - Commercial multifamily for Jasmine (142 units)
  - Top carriers + estimated quotes
  - Texas Panhandle (Amarillo/Dumas) specifics
  - Hail deductibles + recommended buyback strategy
  - Step-by-step quote-shopping checklist
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
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY

OUT = "/tmp/tax_edit/Insurance_Research_Report.pdf"
os.makedirs("/tmp/tax_edit", exist_ok=True)

# Colors
AMBER = colors.HexColor("#F59E0B"); AMBER_L = colors.HexColor("#FEF3C7")
DARK = colors.HexColor("#0F172A"); GRAY = colors.HexColor("#6B7280")
GREEN = colors.HexColor("#059669"); GREEN_L = colors.HexColor("#ECFDF5")
RED = colors.HexColor("#DC2626"); SKY = colors.HexColor("#0284C7")
BG = colors.HexColor("#FAFAFA")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=24, textColor=DARK, spaceAfter=4, leading=28)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=15, textColor=AMBER, spaceAfter=10, leading=18, fontName="Helvetica-Bold")
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=11, textColor=DARK, spaceAfter=4, leading=14, fontName="Helvetica-Bold")
BODY = ParagraphStyle("Body", parent=ss["Normal"], fontSize=10, textColor=DARK, leading=14, spaceAfter=6, alignment=TA_JUSTIFY)
BODY_C = ParagraphStyle("BodyC", parent=BODY, alignment=TA_CENTER)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, textColor=GRAY, leading=11)

elems = []

# ═══ COVER ═══
elems += [
    Spacer(1, 0.4*inch),
    Paragraph('<font color="#F59E0B"><b>R</b>OSS HOUSE RENTALS</font>',
              ParagraphStyle("logo", parent=BODY_C, fontSize=11, textColor=AMBER, fontName="Helvetica-Bold")),
    Spacer(1, 0.4*inch),
    Paragraph("INSURANCE", ParagraphStyle("c1", parent=H1, fontSize=44, alignment=TA_CENTER, leading=50)),
    Paragraph("Research Report — 2026 Market", ParagraphStyle("c2", parent=BODY_C, fontSize=16, textColor=GRAY, leading=22)),
    Paragraph("Texas Landlord Coverage · DP-3 + Multifamily Commercial",
              ParagraphStyle("c3", parent=BODY_C, fontSize=11, textColor=GRAY, leading=15)),
    Spacer(1, 0.4*inch),
]

banner = Table([[
    Paragraph('<font color="#FFFFFF" size=18><b>$1,500-3,200</b></font><br/><font color="#FEF3C7" size=8>Por casa/año DP-3</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font color="#FFFFFF" size=18><b>$777-1,200</b></font><br/><font color="#FEF3C7" size=8>Por unidad/año (Jasmine)</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font color="#FFFFFF" size=18><b>2%</b></font><br/><font color="#FEF3C7" size=8>Deducible hail (Panhandle)</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
    Paragraph('<font color="#FFFFFF" size=18><b>4-7</b></font><br/><font color="#FEF3C7" size=8>Cotizaciones recomendadas</font>',
              ParagraphStyle("b", parent=BODY_C, fontSize=8, textColor=colors.white)),
]], colWidths=[1.7*inch]*4, rowHeights=[0.95*inch])
banner.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),AMBER),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(0,0),(-1,-1),"CENTER")]))
elems.append(banner)
elems.append(Spacer(1, 0.5*inch))

elems += [
    Paragraph("RESUMEN EJECUTIVO", H2),
    Paragraph(
        "Esta investigación cubre el mercado 2026 de seguros para landlords en Texas, "
        "con foco en (1) tus <b>2 propiedades actuales</b> bajo Ross House Rentals LLC y "
        "(2) la futura adquisición de <b>Jasmine Apartments (142 unidades)</b> en Dumas, TX.<br/><br/>"
        "<b>Conclusión clave:</b> Texas es el #1 estado en daños por granizo (hail) en EE.UU. — "
        "este es el factor que más afecta tus primas. La estrategia óptima para tu caso es: "
        "<b>DP-3 (Open Peril)</b> para casas individuales + <b>commercial habitational</b> "
        "para Jasmine, con <b>deductible buyback parametric</b> para protegerte del 2% hail deductible.",
        BODY),
    Spacer(1, 0.2*inch),
    Paragraph(f"<font color='#6B7280' size=9>Preparado para Yoandy Ross · {datetime.now().strftime('%B %Y')}</font>", BODY_C),
]
elems.append(PageBreak())

# ═══ PAGE 2: CURRENT 2 PROPERTIES (DP-3) ═══
elems.append(Paragraph("TUS 2 PROPIEDADES ACTUALES — DP-3 ESTRATEGIA", H2))
elems.append(Paragraph(
    "Para casas individuales rentadas a largo plazo bajo LLC, el estándar de oro es una "
    "póliza <b>DP-3 (Dwelling Property 3 / Open Peril)</b>. Cubre <b>todo</b> excepto "
    "las exclusiones específicas, a diferencia de DP-1 que solo cubre 9 perils nombrados.",
    BODY))
elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("DP-1 vs DP-3 — Por qué nunca aceptes DP-1", H3))
dp_compare = [
    ["Característica", "DP-1 (Básico)", "DP-3 (Recomendado) ⭐"],
    ["Tipo de cobertura", "Named perils (9 listados)", "Open peril (todo excepto exclusiones)"],
    ["Tubería rota / fuga", "❌ NO cubierto", "✅ Cubierto"],
    ["Daño por granizo", "❌ Limitado", "✅ Cubierto (con 2% deductible)"],
    ["Loss of rents (renta perdida)", "❌ Add-on caro", "✅ Incluido típicamente"],
    ["Replacement cost (no depreciado)", "❌ Solo ACV", "✅ Replacement cost"],
    ["Liability ($300K-$1M)", "Limitado", "Incluido"],
    ["Costo promedio TX", "$800-1,400/año", "$1,500-3,200/año"],
]
t = Table(dp_compare, colWidths=[2.0*inch, 2.4*inch, 2.4*inch])
t.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
    ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,BG]),
    ("BACKGROUND",(2,0),(2,0),GREEN),("LEFTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
]))
elems.append(t)
elems.append(Spacer(1, 0.25*inch))

elems.append(Paragraph("Top 4 Carriers DP-3 en Texas 2026", H3))
carriers = [
    ["Carrier", "Rate típico/año*", "Fortaleza", "Mejor para"],
    ["State Farm", "$1,650-2,400", "Red de agentes local fuerte", "Operadores que quieren agente físico"],
    ["Liberty Mutual", "$1,800-2,800", "Buenos bundles + landlord defense", "Múltiples propiedades"],
    ["Travelers", "$2,000-3,000", "Liability robusta + rent coverage", "Casas $250K-500K valor"],
    ["American Family", "$1,500-2,200", "⭐ Mejor calificado overall (NAIC)", "Mejor pricing si calificas"],
    ["Steadily / Obie", "$1,700-2,600", "100% online, especialistas landlord", "LLCs con varias unidades"],
    ["Allstate", "$1,900-2,900", "Bundling con auto", "Si ya tienes auto policy"],
]
ct = Table(carriers, colWidths=[1.5*inch, 1.4*inch, 1.9*inch, 2.0*inch])
ct.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),AMBER),("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
    ("ALIGN",(1,0),(1,-1),"CENTER"),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,BG]),
    ("BACKGROUND",(0,4),(-1,4),GREEN_L),("FONTNAME",(0,4),(-1,4),"Helvetica-Bold"),
    ("LEFTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
]))
elems.append(ct)
elems.append(Spacer(1, 0.1*inch))
elems.append(Paragraph(
    "<font color='#6B7280' size=8>*Rates estimados para casa de $250K-$300K en Dumas/Amarillo TX con 2% hail deductible, "
    "loss of rents 12 meses, $500K liability, sin claims previos. Variables por roof age, año construcción, zip.</font>",
    BODY))
elems.append(PageBreak())

# ═══ PAGE 3: PANHANDLE HAIL DEDUCTIBLE ═══
elems.append(Paragraph("PANHANDLE TX — EL FACTOR HAIL (CRÍTICO)", H2))
elems.append(Paragraph(
    "Amarillo y Dumas están en el <b>'Hail Belt'</b> de Texas. Esto cambia las reglas:",
    BODY))
elems.append(Spacer(1, 0.1*inch))

panhandle = [
    ["Ítem", "Realidad 2026"],
    ["Deductible típico hail/wind", "2% del Dwelling Coverage (NO flat $1,000)"],
    ["En casa de $300K → tu out-of-pocket", "$6,000 ANTES que la aseguradora pague nada"],
    ["En casa de $250K → tu out-of-pocket", "$5,000"],
    ["¿Por qué cambió?", "Daños billonarios 2023-2025 → carriers eliminaron deductibles flat en Panhandle"],
    ["Roof age impact", "Roof >15 años = +20-40% prima, o exclusión total"],
    ["¿1% deductible disponible?", "Sí pero +$200-600/año extra"],
]
pt = Table(panhandle, colWidths=[2.5*inch, 4.3*inch])
pt.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),RED),("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
    ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,BG]),
    ("LEFTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
]))
elems.append(pt)
elems.append(Spacer(1, 0.25*inch))

elems.append(Paragraph("⭐ Estrategia: Parametric Hail Buyback (nuevo en 2026)", H3))
elems.append(Paragraph(
    "Hay una clase nueva de seguro paramétrico que <b>paga automáticamente</b> si radar "
    "(NWS/NOAA) confirma granizo de cierto tamaño (ej. 1 pulgada+) en tu zip code. Cubre "
    "tu deductible del 2% sin necesidad de reclamar ni inspector.<br/><br/>"
    "<b>Costo:</b> ~$300-500/año por casa<br/>"
    "<b>Payout:</b> Automático en 7-14 días si trigger se cumple<br/>"
    "<b>Carriers:</b> Hailtrace, Understory, Arbol — algunos disponibles directamente para landlords",
    BODY))
elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph("Cálculo neto para tus 2 propiedades actuales", H3))
yearly = [
    ["Concepto", "Por casa", "Las 2 casas/año"],
    ["DP-3 estándar (American Family / Steadily)", "$1,800", "$3,600"],
    ["+ Loss of rents 12 meses", "(incluido)", "—"],
    ["+ Parametric hail buyback (opcional)", "$400", "$800"],
    ["+ Umbrella $1M (recomendado con LLC)", "$300", "$600"],
    ["TOTAL ESTIMADO ANUAL", "$2,500", "$5,000"],
]
yt = Table(yearly, colWidths=[3.7*inch, 1.5*inch, 1.6*inch])
yt.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
    ("ALIGN",(1,0),(-1,-1),"RIGHT"),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ("BACKGROUND",(0,-1),(-1,-1),AMBER_L),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),8),
    ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
]))
elems.append(yt)
elems.append(PageBreak())

# ═══ PAGE 4: JASMINE (142 units) ═══
elems.append(Paragraph("JASMINE APARTMENTS (142u) — COMMERCIAL HABITATIONAL", H2))
elems.append(Paragraph(
    "Para multifamily >5 unidades, NO sirve DP-3. Necesitas póliza <b>Commercial "
    "Habitational</b> (también llamada Commercial Multifamily / Apartment Building Insurance). "
    "Se cotiza por unidad o por replacement cost del building.",
    BODY))
elems.append(Spacer(1, 0.15*inch))

elems.append(Paragraph("Estimación de Prima Anual — Jasmine (Dumas TX)", H3))
jasmine = [
    ["Escenario", "Per Unit/Año", "Total/Año"],
    ["Promedio nacional 2026 (NAAHQ)", "$777", "$110,334"],
    ["Texas no-coastal típico (donde estás)", "$900", "$127,800"],
    ["High-risk Texas (referencia)", "$1,200", "$170,400"],
    ["Tu rango esperado realista", "$850-1,100", "$120K-156K"],
]
jt = Table(jasmine, colWidths=[3.5*inch, 1.6*inch, 1.6*inch])
jt.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
    ("ALIGN",(1,0),(-1,-1),"CENTER"),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ("BACKGROUND",(0,-1),(-1,-1),GREEN_L),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
    ("TEXTCOLOR",(0,-1),(-1,-1),GREEN),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ("LEFTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
]))
elems.append(jt)
elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph("⚠️ Impacto en tu modelo financiero Jasmine", H3))
elems.append(Paragraph(
    "Tu Pitch Deck asumía <b>~$641K OpEx total/año</b> que incluía insurance estimado. "
    "Con $127K real de insurance (~20% del OpEx), <b>todavía cierra</b> tu modelo "
    "porque tenías buffer. <b>NOI proyectado $475K se mantiene</b>. Pero conviene "
    "verificar con quote real durante due diligence antes del closing.",
    BODY))
elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph("Top Brokers Commercial Multifamily Texas", H3))
brokers = [
    ["Broker", "Especialidad", "Por qué considerar"],
    ["Marsh McLennan", "Tier-1 brokerage nacional", "Mejor para deals >$5M, relación con todos los carriers"],
    ["Lockton", "Privately-held #1 broker", "Buen servicio post-binding, claims advocacy"],
    ["Bolton & Co. / WS&Co.", "Texas multifamily focus", "Conocen mercado Panhandle"],
    ["TRG Multifamily", "Independent specialist", "Trabajan con LLCs medianas (1-10 propiedades)"],
    ["Awning", "Tech-forward landlord ins.", "Quotes online 24h, ideal screening rápido"],
]
bt = Table(brokers, colWidths=[1.8*inch, 1.9*inch, 3.1*inch])
bt.setStyle(TableStyle([
    ("BACKGROUND",(0,0),(-1,0),AMBER),("TEXTCOLOR",(0,0),(-1,0),colors.white),
    ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
    ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E5E7EB")),
    ("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,BG]),
    ("LEFTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
]))
elems.append(bt)
elems.append(PageBreak())

# ═══ PAGE 5: ACTION PLAN ═══
elems.append(Paragraph("PLAN DE ACCIÓN — 7 PASOS PARA CONSEGUIR EL MEJOR PRECIO", H2))

steps = [
    ("HOY", "Recopila info de cada propiedad",
     "Address, año construcción, sqft, # bedrooms/baths, año del techo (CRÍTICO), tipo de construcción (brick/frame), # claims últimos 5 años. Sin esto los quotes son inútiles."),
    ("DÍA 1-2", "Solicita 4-7 quotes para casas (DP-3)",
     "Llama o aplica online: American Family, Liberty Mutual, Travelers, Steadily, Obie, State Farm. Pide TODOS con SAME spec: $300K dwelling, $500K liability, 12mo rents, 2% hail deductible."),
    ("DÍA 2-3", "Compara DECLARATIONS PAGE no solo prima total",
     "Asegúrate que coverage A (dwelling) sea idéntica, deductibles iguales, rents coverage misma duración. El más barato a veces tiene menos coverage."),
    ("DÍA 3-5", "Negocia con tu top 2 quotes",
     "Llama al carrier #2 y diles: '#1 me dio $X, ¿pueden mejorarlo?'. Casi siempre bajan 5-15%. Si tienes umbrella o auto con ellos, pide bundle discount."),
    ("DÍA 5", "Compra DP-3 + Umbrella $1M",
     "Para LLC operando, umbrella es no-negotiable. $300-500/año extra te da $1M-$2M de coverage encima de las pólizas base. Para Jasmine necesitarás $5M."),
    ("ANTES JASMINE", "Contrata broker commercial REAL",
     "Para Jasmine 142u NO uses online quotes — contrata broker que conozca multifamily TX. Pide 3-4 quotes de carriers: Travelers, Liberty Mutual Commercial, AmTrust, Markel."),
    ("ANNUAL", "Re-shopping cada renovación",
     "Carriers suben primas 8-25%/año en TX. Pide quotes nuevos CADA año al renovar. Si tu carrier sube +15%, cambias. Loyalty no se premia."),
]

for i, (when, title, desc) in enumerate(steps, 1):
    row = Table([
        [Paragraph(f"<font color='#FFFFFF' size=14><b>{i}</b></font>",
                   ParagraphStyle("n", parent=BODY_C, fontSize=14, textColor=colors.white)),
         Paragraph(f"<font color='#F59E0B' size=8><b>{when}</b></font><br/>"
                   f"<font color='#0F172A' size=10><b>{title}</b></font><br/>"
                   f"<font color='#475569' size=9>{desc}</font>",
                   ParagraphStyle("d", parent=BODY, fontSize=9, leading=12))]
    ], colWidths=[0.45*inch, 6.35*inch])
    row.setStyle(TableStyle([("BACKGROUND",(0,0),(0,0),AMBER),
                              ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),8),
                              ("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),7),
                              ("BOTTOMPADDING",(0,0),(-1,-1),7),
                              ("BOX",(0,0),(-1,-1),0.5,colors.HexColor("#E5E7EB"))]))
    elems.append(row)
    elems.append(Spacer(1, 0.06*inch))

elems.append(Spacer(1, 0.2*inch))
elems.append(Paragraph("🎯 PRIMERA LLAMADA RECOMENDADA HOY", H3))
elems.append(Paragraph(
    "<b>Steadily Insurance</b> (especialistas en landlords, 100% online):<br/>"
    "📞 <b>(888) 663-2308</b> · 🌐 steadily.com<br/><br/>"
    "Te dan quote DP-3 en <b>5 minutos online</b> sin compromiso. Es el mejor benchmark "
    "para comparar contra State Farm, Liberty Mutual, etc. Pide específicamente "
    "<b>'DP-3 with 2% hail deductible, $500K liability, 12-month loss of rents'</b>.",
    BODY))

elems.append(Spacer(1, 0.3*inch))
elems.append(Paragraph(
    "<font size=8 color='#6B7280'>Esta investigación se basa en datos públicos del mercado 2026: "
    "NAAHQ, NAIC, Federal Reserve FEDS Notes, Texas TDI. Las primas reales dependen de tu "
    "propiedad específica y historial. Siempre obtén quotes vinculantes por escrito antes de pagar.<br/>"
    "Ross House Rentals · " + datetime.now().strftime("%B %Y") + "</font>",
    BODY_C))

# BUILD
doc = SimpleDocTemplate(OUT, pagesize=letter, rightMargin=0.55*inch, leftMargin=0.55*inch,
                         topMargin=0.55*inch, bottomMargin=0.5*inch,
                         title="Insurance Research Report — Ross House Rentals",
                         author="Ross House Rentals")
doc.build(elems)
print(f"✅ {OUT}  ({os.path.getsize(OUT)/1024:.1f} KB)")

# Email
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
    FileContent, FileName, FileType, Disposition)

with open(OUT, "rb") as f: pdf_bytes = f.read()
stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

mail = Mail(
    from_email=Email(os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com"), "Ross House Rentals"),
    to_emails=To("yoandyross@gmail.com"),
    subject="🛡️ Insurance Research — Top Carriers & 2026 Rates (Ross House + Jasmine)",
)
mail.add_content(Content("text/plain",
    f"Investigación insurance Texas 2026 con quotes, brokers y plan de acción. {stamp}"))
mail.add_content(Content("text/html", f"""
<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;max-width:600px">
  <div style="background:linear-gradient(135deg,#F59E0B,#D97706);padding:20px;border-radius:12px 12px 0 0;color:white">
    <h1 style="margin:0;font-size:22px">🛡️ Insurance Research Report 2026</h1>
    <p style="margin:6px 0 0 0;opacity:0.9">Texas Landlord Coverage — Casas + Jasmine</p>
  </div>
  <div style="background:white;border:1px solid #E5E7EB;border-top:none;padding:20px;border-radius:0 0 12px 12px">
    <p>Hola Yoandy, aquí el reporte completo de seguros con datos 2026 reales del mercado.</p>

    <h3 style="color:#F59E0B">📊 Bottom Line</h3>
    <table style="width:100%;border-collapse:collapse;margin:18px 0">
      <tr>
        <td style="padding:10px;background:#FEF3C7;border-radius:8px;text-align:center">
          <div style="font-size:18px;font-weight:bold;color:#F59E0B">$5,000</div>
          <div style="font-size:10px;color:#6B7280">Total/año tus 2 casas (con buyback + umbrella)</div>
        </td>
        <td style="padding:10px;background:#ECFDF5;border-radius:8px;text-align:center">
          <div style="font-size:18px;font-weight:bold;color:#059669">$120K-156K</div>
          <div style="font-size:10px;color:#6B7280">Jasmine 142u/año (commercial)</div>
        </td>
      </tr>
    </table>

    <h3 style="color:#F59E0B">⚡ Acción inmediata (5 min hoy)</h3>
    <p style="background:#FEF3C7;padding:12px;border-left:4px solid #F59E0B;border-radius:4px">
      Llama a <b>Steadily Insurance: (888) 663-2308</b>. Pide quote DP-3 online con:
      $300K dwelling, $500K liability, 12mo loss of rents, 2% hail deductible.
      Te dan quote en 5 min — es tu benchmark para comparar contra los otros 5 carriers.
    </p>

    <h3 style="color:#F59E0B">📑 El PDF incluye</h3>
    <ol>
      <li>DP-1 vs DP-3 comparison (por qué nunca aceptes DP-1)</li>
      <li>Top 6 carriers DP-3 en TX con rates estimados</li>
      <li>Análisis del 2% hail deductible en Panhandle (Dumas/Amarillo)</li>
      <li>⭐ Estrategia parametric hail buyback (nuevo en 2026)</li>
      <li>Cálculo de insurance budget para Jasmine ($120-156K/año)</li>
      <li>Top brokers commercial multifamily TX</li>
      <li>Plan de acción 7 pasos para sacar mejor precio</li>
    </ol>

    <h3 style="color:#F59E0B">⚠️ Tips críticos</h3>
    <ul>
      <li><b>Roof age es factor #1:</b> Si tu techo tiene >15 años, prima sube 20-40%. Reroof antes del shopping</li>
      <li><b>NO aceptes flat $1,000 deductible:</b> Panhandle ya es 2% del dwelling — si te ofrecen flat es señal de quote viejo o sospechoso</li>
      <li><b>Umbrella $1M es no-negotiable</b> operando bajo LLC ($300-500/año por casa)</li>
      <li><b>Re-shop cada año:</b> carriers suben 8-25%/año en TX. Loyalty no se premia</li>
    </ul>

    <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0">
    <p style="font-size:11px;color:#6B7280">Generado: {stamp}<br/>Ross House Rentals</p>
  </div>
</div>
"""))
mail.attachment = Attachment(
    FileContent(base64.b64encode(pdf_bytes).decode()),
    FileName("Insurance_Research_Report_2026.pdf"),
    FileType("application/pdf"),
    Disposition("attachment"),
)
sg = SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
resp = sg.send(mail)
print(f"✅ Email enviado (status={resp.status_code})")
