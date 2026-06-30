"""Envía el directorio completo de financiamiento (Tier 1-9 lenders) a yoandyross@gmail.com como email + PDF adjunto."""
import os, base64
from datetime import datetime
from dotenv import load_dotenv
load_dotenv('/app/ross-house-backend/.env')

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether
from reportlab.lib.enums import TA_LEFT
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

OUT = '/tmp/Jasmine_Financing_Directory.pdf'

styles = getSampleStyleSheet()
NAVY = colors.HexColor('#0d1a2e')
AMBER = colors.HexColor('#f59e0b')
AMBER_DARK = colors.HexColor('#d97706')
GRAY = colors.HexColor('#475569')
GREEN = colors.HexColor('#059669')

H1 = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=22, textColor=NAVY, spaceAfter=8, leading=26)
H2 = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=15, textColor=AMBER_DARK, spaceAfter=8, spaceBefore=16)
H3 = ParagraphStyle('H3', parent=styles['Heading3'], fontName='Helvetica-Bold', fontSize=12, textColor=NAVY, spaceAfter=4, spaceBefore=10)
SMALL = ParagraphStyle('Small', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=GRAY)
BODY = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=10, textColor=colors.HexColor('#0f172a'), leading=14, spaceAfter=6, alignment=TA_LEFT)
LENDER = ParagraphStyle('Lender', parent=BODY, leftIndent=8, rightIndent=8, spaceAfter=8, fontSize=9.5)

doc = SimpleDocTemplate(OUT, pagesize=LETTER, leftMargin=0.6*inch, rightMargin=0.6*inch, topMargin=0.55*inch, bottomMargin=0.55*inch, title='Jasmine Apartments — Financing Directory')
e = []

# COVER
e.append(Paragraph('🏢 Jasmine Apartments', H1))
e.append(Paragraph('Directorio completo de financiamiento — 142 unidades · Dumas, TX',
    ParagraphStyle('sub', parent=BODY, fontSize=13, textColor=GRAY, spaceAfter=4)))
e.append(Paragraph(f'Preparado para Yoandy Ross · {datetime.utcnow().strftime("%B %d, %Y")}', SMALL))
e.append(Spacer(1, 0.2*inch))

# Capital Stack
e.append(Paragraph('💼 Estructura de capital propuesta', H2))
stack = [
    ['Tramo', 'Monto', '%', 'Fuente'],
    ['Senior Debt (agency loan)', '$5,625,000', '75%', 'Fannie / Freddie / HUD'],
    ['Seller Financing', '$750,000', '10%', 'Joe Kuruvila (negociar)'],
    ['Sponsor Equity (tú)', '$375,000', '5%', 'DSCR cash-out SFR + propio'],
    ['LP Equity (inversionistas)', '$750,000', '10%', 'Sindicación 506(b)/(c)'],
    ['TOTAL', '$7,500,000', '100%', ''],
]
t = Table(stack, colWidths=[2.2*inch, 1.4*inch, 0.7*inch, 2.5*inch])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), NAVY),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
    ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#fef3c7')),
    ('FONTSIZE', (0,0), (-1,-1), 9.5),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cbd5e1')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
]))
e.append(t)
e.append(Spacer(1, 0.2*inch))

# Action plan top 5
e.append(Paragraph('🎯 Top 5 — Llama esta semana', H2))
top5 = [
    ['#', 'Institución', 'Phone', 'Producto', 'Por qué'],
    ['1', 'Happy State Bank (Amarillo)', '(806) 379-7777', 'Local CRE loan', 'Relación local Panhandle'],
    ['2', 'Walker & Dunlop (Dallas)', '(214) 800-3000', 'Freddie/Fannie SBL', '#1 originator TX'],
    ['3', 'Kiavi', '(855) 543-5837', 'DSCR refi SFRs', 'Unlock equity rápido'],
    ['4', 'Greystone (Dallas)', '(469) 522-2300', 'Fannie DUS / HUD', 'Top 3 originator'],
    ['5', 'First United Bank (Dumas)', '(806) 935-3522', 'Local relationship', 'Banco de tu pueblo'],
]
t = Table(top5, colWidths=[0.3*inch, 2.0*inch, 1.2*inch, 1.4*inch, 1.9*inch])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), AMBER_DARK),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE', (0,0), (-1,-1), 8.5),
    ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cbd5e1')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
]))
e.append(t)
e.append(PageBreak())

# TIER 1
e.append(Paragraph('🏦 TIER 1 — Agency Loans (mejores términos)', H2))
e.append(Paragraph('<b>Tasas 2026:</b> 5.25-6.25% fijo · LTV 75-85% · DSCR 1.20-1.25x mínimo · 30-year amort', BODY))
e.append(Spacer(1, 0.1*inch))

tier1_items = [
    ('Fannie Mae DUS',
     'Loan size $750K-$6M+ · LTV 80% · DSCR 1.25x · 30yr amort + balloon 5/7/10 · Tasa ~5.75-6.25% fijo · Acceso vía DUS Lender autorizado'),
    ('Freddie Mac Optigo SBL ⭐ MEJOR FIT',
     '$1M-$7.5M · LTV 80% · DSCR 1.20x · 30yr amort + hybrid ARM · Tasa ~5.50-6.00% inicial · Proceso 45-60 días (más rápido que DUS)'),
    ('HUD/FHA 223(f)',
     'Hasta 85% LTV · 35yr amort fijo · DSCR 1.176x · Tasa ~5.25-5.75% fijo · Assumable · Proceso 4-6 meses, MIP 0.60% anual · Mejor para hold de 10+ años'),
]
for name, desc in tier1_items:
    e.append(Paragraph(f'<b>{name}</b>', H3))
    e.append(Paragraph(desc, LENDER))

e.append(Paragraph('🏛️ TIER 2 — Correspondent Lenders / Agency Brokers', H2))
e.append(Paragraph('Estos brokers tienen acceso directo a Fannie/Freddie/HUD. Llama a 2-3 simultáneamente.', BODY))
e.append(Spacer(1, 0.08*inch))

tier2 = [
    ('1. Walker & Dunlop', 'walkerdunlop.com · Dallas (214) 800-3000 · #1 originator Fannie DUS en USA. Foco fuerte TX.'),
    ('2. Greystone', 'greyco.com · Dallas (469) 522-2300 · Top 3 originator. Especialistas HUD 223(f).'),
    ('3. Berkadia', 'berkadia.com · Dallas (469) 366-5900 · Owned by Berkshire Hathaway + Jefferies.'),
    ('4. Newmark Multifamily Capital', 'nmrk.com · Houston/Dallas team activo TX.'),
    ('5. JLL Capital Markets', 'jll.com · Houston (713) 888-4000 · Cubre TX. Buenos $5M+.'),
    ('6. CBRE Capital Markets', 'cbre.com · Más enfocados $10M+ pero también menores.'),
    ('7. Capital One Multifamily', 'capitalone.com/commercial · (212) 850-7300 · DUS y Freddie lender directo.'),
    ('8. Arbor Realty Trust', 'arbor.com · (516) 506-4200 · Especialistas SBL ($1M-$7.5M).'),
]
for name, desc in tier2:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

e.append(PageBreak())

# TIER 3
e.append(Paragraph('🏪 TIER 3 — Bancos Locales Texas (relación + flexibilidad)', H2))
e.append(Paragraph('Más rápido al cierre, más flexible LTV/DSCR. Tasas ligeramente más altas (6.5-7.5%), balloons 5-7 años.', BODY))
e.append(Spacer(1, 0.08*inch))
e.append(Paragraph('<b>Texas Panhandle (cerca Dumas)</b>', H3))
panhandle = [
    ('Happy State Bank (Amarillo) ⭐ TOP LOCAL', 'happybank.com · (806) 379-7777 · Conocen Dumas, prestan a operadores locales.'),
    ('Amarillo National Bank', 'anb.com · (806) 378-8000 · Más grande del Panhandle. Apetito CRE.'),
    ('First United Bank (Dumas)', 'firstunitedbank.com · 401 S Dumas Ave · (806) 935-3522 · Sucursal local, relación cara a cara.'),
    ('Centennial Bank', 'my100bank.com · Tras fusión con Happy, apetito grande TX multifamily.'),
]
for name, desc in panhandle:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

e.append(Paragraph('<b>Texas amplio (Dallas/Houston-based)</b>', H3))
tx_wide = [
    ('Frost Bank', 'frostbank.com · (210) 220-4011 · Texano premium. Estricto pero relaciones largas.'),
    ('Texas Capital Bank', 'texascapitalbank.com · (214) 932-6600 · Foco fuerte CRE TX, deals $5M+.'),
    ('Independent Financial', 'ifinancial.com · (972) 562-9004 · Activos multifamily TX.'),
    ('Veritex Community Bank', 'veritexbank.com · Lender activo multifamily TX.'),
    ('Prosperity Bank', 'prosperitybankusa.com · Comunidad bank con multifamily portfolio.'),
    ('Cadence Bank', 'cadencebank.com · Activos CRE TX (formerly BancorpSouth).'),
]
for name, desc in tx_wide:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

e.append(PageBreak())

# TIER 4 DSCR
e.append(Paragraph('💰 TIER 4 — DSCR / Non-QM Lenders (cash-out SFR refi)', H2))
e.append(Paragraph('Para refinanciar tus SFRs actuales y extraer equity (objetivo $300K-$500K) para down payment Jasmine. No verifican income — solo cashflow propiedad (DSCR ≥ 1.0).', BODY))
e.append(Spacer(1, 0.08*inch))
dscr = [
    ('1. Kiavi ⭐ (antes LendingHome)', 'kiavi.com · (855) 543-5837 · 80% LTV · Tasas 7-8% · 21-30 días cierre · 100% online.'),
    ('2. Visio Lending (Austin, TX)', 'visiolending.com · (512) 957-7350 · DSCR 1-30 unidades. TX-based.'),
    ('3. Lima One Capital', 'limaone.com · (864) 989-4200 · DSCR + fix-and-flip + portfolio loans.'),
    ('4. CoreVest Finance', 'corevestfinance.com · (844) 819-6011 · Portfolio loans — refi 5-100 props en 1 loan.'),
    ('5. Roc Capital', 'roc360.com · (212) 607-8333 · DSCR + bridge loans investors.'),
    ('6. Easy Street Capital (Austin)', 'easystreetcap.com · (737) 244-4000 · TX foco.'),
    ('7. LendingOne', 'lendingone.com · (866) 803-2853'),
]
for name, desc in dscr:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

# TIER 5 Bridge
e.append(Paragraph('🌉 TIER 5 — Bridge / Hard Money (último recurso)', H2))
e.append(Paragraph('Solo si necesitas cerrar en 30 días o menos. Tasas 9-12%, fees altos. Refi a agency en 6-18 meses.', BODY))
e.append(Spacer(1, 0.08*inch))
bridge = [
    ('Civic Financial Services', 'civicfs.com · Bridge loans hasta $5M, cierre 7-10 días.'),
    ('Anchor Loans', 'anchorloans.com · (310) 414-6868'),
    ('RCN Capital', 'rcncapital.com · (860) 432-5858'),
    ('Stratton Equities', 'strattonequities.com'),
]
for name, desc in bridge:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

e.append(PageBreak())

# TIER 6 LP Equity
e.append(Paragraph('👥 TIER 6 — LP Equity / Sindicación', H2))
e.append(Paragraph('Para los $750K-$1.5M de equity de inversores ("LPs"). Tú GP/sponsor. Estructura típica 70/30 split + 8% preferred return.', BODY))
e.append(Spacer(1, 0.08*inch))
e.append(Paragraph('<b>A. Plataformas Crowdfunding (506(c))</b>', H3))
cf = [
    ('CrowdStreet', 'crowdstreet.com · Min $25K · Premium RE crowdfunding · Costo sponsor $25K-$50K + 1-3%.'),
    ('RealtyMogul', 'realtymogul.com · Min $5K-$35K'),
    ('EquityMultiple', 'equitymultiple.com · Min $10K-$20K'),
    ('Cadre', 'cadre.com · Multifamily institucional. Más selectivos.'),
]
for name, desc in cf:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

e.append(Paragraph('<b>B. Sindicación privada 506(b) — Friends & Family</b>', H3))
e.append(Paragraph(
    'No requiere registro SEC. Solo a inversores con "preexisting relationship". Max 35 non-accredited. '
    '<b>Tu lista objetivo:</b> family offices Amarillo/Lubbock (oil money) · doctores y dentistas locales (clientes Ross Tax) · '
    'otros landlords del Panhandle · CPA networks · RE investor clubs Amarillo.', LENDER))

e.append(Paragraph('<b>Software gestión LP:</b>', H3))
sw = [
    ('AppFolio Investor', 'appfolio.com — software gestión LP'),
    ('Juniper Square', 'junipersquare.com — más institucional'),
    ('DealCheck', 'dealcheck.io — análisis y compartir con LPs'),
    ('InvestNext', 'investnext.com'),
]
for name, desc in sw:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

# TIER 7 Seller Financing
e.append(Paragraph('🤝 TIER 7 — Seller Financing (negociar con Joe)', H2))
e.append(Paragraph('<b>MEJOR vía para reducir cash al cierre.</b> Joe presta 10-15% del precio. Subordinado al senior debt.', BODY))
e.append(Paragraph(
    '<b>Términos a proponer:</b> Monto 10-15% ($750K-$1.125M) · Tasa 6-7% fijo · Plazo 5-7 años con balloon · '
    'Amort 25-30 años · Sin prepayment penalty desde año 2 · 2nd lien + subordination agreement.', LENDER))
e.append(Paragraph(
    '<b>Por qué Joe podría aceptar:</b> (1) Tax deferral via installment sale, (2) Higher net proceeds (7% > Treasuries), '
    '(3) Cierre más rápido, (4) Interés vested en que la operación funcione.', LENDER))

e.append(PageBreak())

# TIER 8 Creative
e.append(Paragraph('🏠 TIER 8 — Creative Structures', H2))
creative = [
    ('Master Lease + Opción a Compra', 'Opera 12-24 meses pagando lease, bloqueas precio hoy, acumulas equity. Pruebas el deal antes del 100%.'),
    ('Wraparound Mortgage', 'Joe te da hipoteca que envuelve la suya. Solo si su loan es asumible o sin due-on-sale activa.'),
    ('Subject-To', 'Tomas la propiedad subject to su hipoteca. Solo si tasa es baja (3-4% pre-2022).'),
    ('1031 Exchange Partner', 'Encuentra inversor vendiendo otra prop con 45/180 días para reinvertir. Él aporta como LP, defiere impuestos.'),
    ('Note Purchase', 'Si Joe vende a tercero con seller financing, compras la nota a descuento. Raro pero posible.'),
]
for name, desc in creative:
    e.append(Paragraph(f'<b>{name}</b> — {desc}', LENDER))

# Action Plan
e.append(Paragraph('✅ Acción Plan 30 días', H2))
e.append(Paragraph('<b>Semana 1 — Discovery calls:</b>', H3))
e.append(Paragraph('☐ Happy State Bank (Amarillo) · ☐ Amarillo National Bank · ☐ First United Dumas · ☐ Walker & Dunlop · ☐ Greystone · ☐ Arbor Realty', LENDER))
e.append(Paragraph('<b>Semana 2 — DSCR cash-out refi de SFRs:</b>', H3))
e.append(Paragraph('☐ Quote Kiavi (online 5 min) · ☐ Quote Visio (TX-based) · ☐ Quote Lima One · ☐ Calcular equity extraible ($300K-$500K objetivo)', LENDER))
e.append(Paragraph('<b>Semana 3 — Capital partners:</b>', H3))
e.append(Paragraph('☐ Crear deck "Jasmine Opportunity" 1-pager + pro forma · ☐ Lista 20 LPs potenciales (doctores, dentistas, Ross Tax clients, contactos Cubanos US) · ☐ Considerar CrowdStreet listing', LENDER))
e.append(Paragraph('<b>Semana 4 — Negociación Joe:</b>', H3))
e.append(Paragraph('☐ Esperar respuesta email (ya abierto 30-Jun) · ☐ Validar T-12 cuando lo mande · ☐ Proponer estructura con seller financing 10-15% · ☐ NDA + LOI si acepta termsheet', LENDER))

# Tips
e.append(Paragraph('💡 Tips estratégicos', H2))
tips = [
    'Llama 3-5 lenders simultáneamente. Compara term sheets REALES, no rates publicados.',
    'NO firmes nada hasta tener LOI signed con Joe. Pre-aprobación soft sirve, no commitment.',
    'Pre-califica para el monto MAX. Más fácil bajar que subir después.',
    'Rate lock SOLO cuando estés cerca de cierre. Locks tempranos cuestan basis points.',
    'NDA con cada lender antes de compartir T-12 de Joe (Joe te exigirá esto).',
    'Tu major leverage: experiencia operativa local + alta ocupación esperada. Sell that.',
    'Si Joe rechaza seller financing: ajusta capital stack a 25% equity en lugar de 15%.',
    'Cash reserves: NUNCA bajes de 6 meses de OPEX + debt service.',
]
for t in tips:
    e.append(Paragraph(f'• {t}', LENDER))

# Recursos
e.append(Paragraph('📚 Recursos adicionales', H2))
res = [
    'Fannie Mae DUS lender list — fanniemae.com/multifamily/dus-lenders',
    'Freddie Mac Optigo lender list — mf.freddiemac.com/lenders',
    'HUD/FHA approved MAP lenders — hud.gov/program_offices/housing/mfh/lender/maplenders',
    'MBA lender directory — mba.org',
    'Multifamily Lender Magazine — multifamilybiz.com',
]
for r in res:
    e.append(Paragraph(f'• {r}', LENDER))

doc.build(e)
print(f'✅ PDF: {OUT} ({os.path.getsize(OUT)/1024:.1f} KB)')

# ─── SEND EMAIL ───
api_key = os.environ['SENDGRID_API_KEY']
from_email = (os.environ['SENDGRID_FROM_EMAIL'], 'Yoandy Ross — Ross House Rentals')

with open(OUT, 'rb') as f:
    pdf_b64 = base64.b64encode(f.read()).decode()

html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#0f172a;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9;padding:24px 12px;">
  <tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">

      <!-- Header navy -->
      <tr><td bgcolor="#0d1a2e" style="background:#0d1a2e;padding:28px 32px;">
        <div style="display:inline-block;background:#3b2a06;border:1px solid #b45309;padding:5px 12px;border-radius:999px;">
          <span style="color:#fbbf24;font-size:11px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;">Acquisitions · Financing</span>
        </div>
        <div style="margin-top:10px;color:#ffffff;font-size:22px;font-weight:800;">🏢 Ross House Rentals</div>
      </td></tr>

      <!-- Content -->
      <tr><td style="padding:32px 32px 16px 32px;">
        <div style="font-size:13px;font-weight:700;color:#d97706;text-transform:uppercase;letter-spacing:1px;">Directorio completo de financiamiento</div>
        <h1 style="font-size:26px;font-weight:800;color:#0f172a;margin:8px 0 4px 0;line-height:1.25;">Jasmine Apartments — Capital Stack</h1>
        <div style="font-size:14px;color:#64748b;margin-bottom:22px;">9 tiers de prestamistas + acción plan 30 días</div>

        <p style="font-size:15px;line-height:1.65;">Hola Yoandy,</p>
        <p style="font-size:14px;line-height:1.7;color:#334155;">
          Te adjunto el <strong>directorio completo de financiamiento</strong> para Jasmine Apartments
          ($7.5M target, 142 unidades). Es una guía táctica con teléfonos, websites y por qué llamar a
          cada uno primero.
        </p>

        <div style="margin:18px 0;padding:16px 18px;background:#fef3c7;border-left:4px solid #f59e0b;border-radius:8px;font-size:13px;color:#78350f;line-height:1.65;">
          <strong>📋 Capital Stack propuesto:</strong>
          <ul style="margin:8px 0 0 18px;padding:0;line-height:1.7;">
            <li>75% <strong>Senior debt</strong> (Fannie/Freddie agency loan) → $5.625M</li>
            <li>10% <strong>Seller financing</strong> (negociar con Joe) → $750K</li>
            <li>5% <strong>Sponsor equity</strong> (tú — vía DSCR refi de SFRs) → $375K</li>
            <li>10% <strong>LP equity</strong> (inversionistas — sindicación) → $750K</li>
          </ul>
        </div>

        <div style="margin:18px 0;padding:16px 18px;background:#ecfdf5;border-left:4px solid #10b981;border-radius:8px;font-size:13px;color:#065f46;line-height:1.65;">
          <strong>🎯 Top 5 llamadas esta semana:</strong>
          <ol style="margin:8px 0 0 18px;padding:0;line-height:1.85;">
            <li><strong>Happy State Bank (Amarillo)</strong> · (806) 379-7777 — local Panhandle</li>
            <li><strong>Walker &amp; Dunlop (Dallas)</strong> · (214) 800-3000 — #1 agency TX</li>
            <li><strong>Kiavi</strong> · (855) 543-5837 — DSCR refi de tus SFRs (online, 5 min)</li>
            <li><strong>Greystone (Dallas)</strong> · (469) 522-2300 — top 3 agency originator</li>
            <li><strong>First United Bank (Dumas)</strong> · (806) 935-3522 — tu banco local</li>
          </ol>
        </div>

        <p style="font-size:14px;line-height:1.7;color:#334155;">
          <strong>El PDF adjunto (5 páginas)</strong> tiene los 9 tiers completos: agency loans, correspondent
          brokers, bancos locales TX, DSCR lenders, bridge/hard money, LP equity (CrowdStreet etc.),
          seller financing, structures creativas (master lease, 1031 partner, wrap) y SBA.
        </p>

        <p style="font-size:14px;line-height:1.7;color:#334155;">
          También incluye <strong>plan de acción de 30 días</strong> (semana por semana) y 8 tips estratégicos
          basados en M&amp;A best practices.
        </p>

        <p style="font-size:13px;color:#64748b;margin-top:18px;">
          Guardado también en <code style="background:#f1f5f9;padding:2px 6px;border-radius:4px;">memory/JASMINE_FINANCING.md</code>
          como referencia editable.
        </p>
      </td></tr>

      <!-- Footer -->
      <tr><td bgcolor="#f8fafc" style="background:#f8fafc;padding:24px 32px;border-top:1px solid #e2e8f0;">
        <div style="font-size:12px;color:#64748b;line-height:1.6;text-align:center;">
          <strong style="color:#0f172a;">Ross House Rentals</strong> · Dumas, TX · (806) 934-2018<br>
          <a href="https://www.rosshouserentals.com" style="color:#d97706;text-decoration:none;">www.rosshouserentals.com</a>
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

msg = Mail(
    from_email=from_email,
    to_emails='yoandyross@gmail.com',
    subject='💰 Jasmine Apartments — Directorio completo de financiamiento (9 tiers, 40+ lenders)',
    plain_text_content='Directorio de financiamiento Jasmine Apartments. Top 5 calls: Happy State Bank (806) 379-7777, Walker & Dunlop (214) 800-3000, Kiavi (855) 543-5837, Greystone (469) 522-2300, First United Bank Dumas (806) 935-3522. PDF adjunto con detalles.',
    html_content=html,
)
att = Attachment()
att.file_content = FileContent(pdf_b64)
att.file_name = FileName('Jasmine_Financing_Directory_Ross_House.pdf')
att.file_type = FileType('application/pdf')
att.disposition = Disposition('attachment')
msg.attachment = att

resp = SendGridAPIClient(api_key).send(msg)
print(f'✅ Email enviado a yoandyross@gmail.com — status: {resp.status_code}')
