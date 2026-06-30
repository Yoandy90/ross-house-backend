"""Jasmine Apartments — Email-Only Inquiry
Versión "todo por email" — sin pedir llamada telefónica.
Hace preguntas específicas que Joe puede responder con texto.
Envía PDF a yoandyross@gmail.com via SendGrid para revisión.
"""
import os, base64
from datetime import datetime
from dotenv import load_dotenv
load_dotenv('/app/ross-house-backend/.env')

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
from reportlab.lib.enums import TA_LEFT
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

OUT = '/tmp/Jasmine_Email_Only_Inquiry.pdf'

styles = getSampleStyleSheet()
NAVY = colors.HexColor('#0d1a2e')
AMBER = colors.HexColor('#f59e0b')
GRAY = colors.HexColor('#475569')
GREEN = colors.HexColor('#059669')

H1 = ParagraphStyle('H1', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=22, textColor=NAVY, spaceAfter=6, leading=26)
H2 = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=14, textColor=AMBER, spaceAfter=8, spaceBefore=14)
H3 = ParagraphStyle('H3', parent=styles['Heading3'], fontName='Helvetica-Bold', fontSize=12, textColor=GREEN, spaceAfter=6, spaceBefore=10)
SMALL = ParagraphStyle('Small', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=GRAY)
BODY = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=10.5, textColor=colors.HexColor('#0f172a'), leading=15, spaceAfter=8, alignment=TA_LEFT)
LETTER_S = ParagraphStyle('Letter', parent=BODY, leftIndent=10, rightIndent=10, spaceAfter=10)
QBOX = ParagraphStyle('QBox', parent=BODY, leftIndent=14, rightIndent=10, spaceAfter=6, fontSize=10.5)

doc = SimpleDocTemplate(OUT, pagesize=LETTER, leftMargin=0.7*inch, rightMargin=0.7*inch, topMargin=0.6*inch, bottomMargin=0.6*inch, title='Jasmine Apartments — Email-Only Inquiry')
e = []

# ─── COVER ───
e.append(Paragraph('Ross House Rentals LLC', H1))
e.append(Paragraph('Email-Only Inquiry · Jasmine Apartments (sin llamada telefónica)',
    ParagraphStyle('sub', parent=BODY, fontSize=12, textColor=GRAY, spaceAfter=4)))
e.append(Paragraph(f'Versiones ES + EN listas para copiar · Generado: {datetime.utcnow().strftime("%B %d, %Y")}', SMALL))
e.append(Spacer(1, 0.15*inch))

facts = [
    ['Destinatario', 'Joe Kuruvila'],
    ['Email', 'joe3359@gmail.com'],
    ['Asunto sugerido (ES)', 'Interés en Jasmine Apartments — algunas preguntas (Ross House Rentals)'],
    ['Asunto sugerido (EN)', 'Interest in Jasmine Apartments — a few questions (Ross House Rentals)'],
    ['Tono', 'Profesional, directo, sin presión'],
    ['Objetivo', 'Que Joe responda por email con datos específicos — SIN llamada'],
    ['Regla M&A', 'NO mencionar precio. Que él lo diga primero.'],
]
ft = Table(facts, colWidths=[1.9*inch, 4.6*inch])
ft.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
    ('FONTSIZE', (0,0), (-1,-1), 9.5),
    ('TEXTCOLOR', (0,0), (0,-1), GRAY),
    ('FONTNAME', (1,0), (1,-1), 'Helvetica-Bold'),
    ('TEXTCOLOR', (1,0), (1,-1), NAVY),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('LINEBELOW', (0,0), (-1,-2), 0.4, colors.HexColor('#e2e8f0')),
    ('TOPPADDING', (0,0), (-1,-1), 6),
    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
]))
e.append(ft)
e.append(Spacer(1, 0.15*inch))

e.append(Paragraph(
    '<b>¿Por qué esta versión?</b> Algunas personas — especialmente vendedores experimentados — '
    'prefieren no comprometerse a una llamada en la primera interacción. Una llamada los obliga a '
    'preparar respuestas en tiempo real. Un email les deja revisar números, pensar y responder '
    'con tranquilidad. Para ti es <b>incluso mejor</b>: tendrás sus respuestas <i>por escrito</i>, '
    'lo que es oro puro si esto avanza a LOI o negociación.', BODY))
e.append(PageBreak())

# ─── ES VERSION ───
e.append(Paragraph('🇪🇸 EMAIL EN ESPAÑOL', H2))
e.append(Paragraph('Asunto: <b>Interés en Jasmine Apartments — algunas preguntas (Ross House Rentals)</b>', BODY))
e.append(Spacer(1, 0.1*inch))

es_intro = [
    'Estimado Sr. Kuruvila:',
    '',
    'Buen día. Mi nombre es <b>Yoandy Ross</b>, propietario de <b>Ross House Rentals LLC</b>, '
    'una empresa local en Dumas, Texas, dedicada a la inversión y administración de propiedades '
    'residenciales en el Texas Panhandle.',
    '',
    'Vi que <b>Jasmine Apartments</b> aparece en listados públicos (LoopNet / Showcase) y quería '
    'contactarlo directamente. Soy un comprador <b>local y serio</b> — no un fondo de fuera del estado '
    'ni un broker buscando comisión.',
    '',
    'Para no quitarle tiempo con una llamada todavía, le hago un par de preguntas concretas por email. '
    'Cuando tenga un momento, le agradecería mucho su respuesta — aunque sea breve. Con eso ya puedo '
    'avanzar internamente y, si tiene sentido para ambos, después coordinamos los próximos pasos.',
]
for ln in es_intro:
    e.append(Spacer(1, 0.05*inch) if not ln else Paragraph(ln, LETTER_S))

e.append(Paragraph('Preguntas (responda solo lo que prefiera compartir en esta etapa):', H3))
es_q = [
    '<b>1.</b> ¿Sigue activamente disponible para la venta el portafolio completo de Jasmine Apartments (las 5 propiedades / 142 unidades)?',
    '<b>2.</b> ¿Cuál es su <b>precio actual de venta</b> ("asking price")?',
    '<b>3.</b> ¿Cuál es la <b>tasa de ocupación promedio</b> de los últimos 12 meses?',
    '<b>4.</b> ¿Cuál es el <b>NOI</b> aproximado del último año fiscal (o GPR + gastos operativos si prefiere)?',
    '<b>5.</b> ¿Año de construcción y fecha aproximada de las últimas renovaciones mayores (techos, HVAC, plomería)?',
    '<b>6.</b> ¿Está abierto a <b>seller financing</b> parcial, o prefiere venta en efectivo / financiamiento agency (Fannie/Freddie)?',
    '<b>7.</b> ¿Cuál es el <b>motivo principal</b> de la venta (jubilación, 1031 exchange, reasignación de portafolio)?',
    '<b>8.</b> ¿Estaría dispuesto a firmar un <b>NDA</b> para compartir el T-12 y rent roll completo en una segunda etapa?',
    '<b>9.</b> ¿Cuántas <b>ofertas o LOIs</b> ha recibido hasta el momento (sin necesidad de detalles)?',
    '<b>10.</b> ¿Tiene un <b>timeline preferido</b> para cerrar la operación?',
]
for q in es_q:
    e.append(Paragraph(q, QBOX))

es_close = [
    '',
    'No hay prisa — responda cuando le sea cómodo. Si prefiere abordar solo algunas preguntas en este primer email, está perfecto. Lo importante es abrir la conversación.',
    '',
    'Muchas gracias por su tiempo, Sr. Kuruvila. Quedo atento.',
    '',
    'Saludos cordiales,',
    '',
    '<b>Yoandy Ross</b><br/>'
    'Owner · Ross House Rentals LLC<br/>'
    '✉️ info@rosshouserentals.com<br/>'
    '📞 (806) 934-2018<br/>'
    '🌐 www.rosshouserentals.com<br/>'
    '📍 305 Bruce Ave, Dumas, TX 79029',
]
for ln in es_close:
    e.append(Spacer(1, 0.05*inch) if not ln else Paragraph(ln, LETTER_S))
e.append(PageBreak())

# ─── EN VERSION ───
e.append(Paragraph('🇺🇸 ENGLISH EMAIL', H2))
e.append(Paragraph('Subject: <b>Interest in Jasmine Apartments — a few questions (Ross House Rentals)</b>', BODY))
e.append(Spacer(1, 0.1*inch))

en_intro = [
    'Dear Mr. Kuruvila:',
    '',
    'Good day. My name is <b>Yoandy Ross</b>, owner of <b>Ross House Rentals LLC</b>, a local '
    'Dumas, Texas company focused on the investment and management of residential rental '
    'properties in the Texas Panhandle.',
    '',
    'I noticed that <b>Jasmine Apartments</b> is listed on public platforms (LoopNet / Showcase) '
    'and I wanted to contact you directly. I am a <b>local and serious buyer</b> — not an '
    'out-of-state fund or a broker seeking commission.',
    '',
    'To avoid taking up your time with a phone call just yet, I have a few specific questions '
    'I would appreciate answering by email. Whenever you have a moment, even a brief reply would '
    'be very helpful. With that, I can move forward internally, and if it makes sense for both of '
    'us, we can coordinate next steps later.',
]
for ln in en_intro:
    e.append(Spacer(1, 0.05*inch) if not ln else Paragraph(ln, LETTER_S))

e.append(Paragraph('Questions (please answer only what you are comfortable sharing at this stage):', H3))
en_q = [
    '<b>1.</b> Is the entire Jasmine Apartments portfolio (the 5 properties / 142 units) still actively available for sale?',
    '<b>2.</b> What is your <b>current asking price</b>?',
    '<b>3.</b> What is the <b>average occupancy rate</b> over the last 12 months?',
    '<b>4.</b> What is the approximate <b>NOI</b> for the last fiscal year (or GPR + operating expenses if you prefer)?',
    '<b>5.</b> Year built and approximate date of the most recent major renovations (roofs, HVAC, plumbing)?',
    '<b>6.</b> Are you open to partial <b>seller financing</b>, or do you prefer a cash sale / agency financing (Fannie/Freddie)?',
    '<b>7.</b> What is the <b>main reason</b> for selling (retirement, 1031 exchange, portfolio rebalancing)?',
    '<b>8.</b> Would you be willing to sign an <b>NDA</b> to share the T-12 and full rent roll in a second step?',
    '<b>9.</b> How many <b>offers or LOIs</b> have you received so far (no details needed)?',
    '<b>10.</b> Do you have a <b>preferred timeline</b> to close the transaction?',
]
for q in en_q:
    e.append(Paragraph(q, QBOX))

en_close = [
    '',
    'No rush — please reply whenever it is convenient for you. If you prefer to address only a few of these questions in this first email, that is perfectly fine. The important thing is to open the conversation.',
    '',
    'Thank you very much for your time, Mr. Kuruvila. I look forward to hearing back.',
    '',
    'Best regards,',
    '',
    '<b>Yoandy Ross</b><br/>'
    'Owner · Ross House Rentals LLC<br/>'
    '✉️ info@rosshouserentals.com<br/>'
    '📞 (806) 934-2018<br/>'
    '🌐 www.rosshouserentals.com<br/>'
    '📍 305 Bruce Ave, Dumas, TX 79029',
]
for ln in en_close:
    e.append(Spacer(1, 0.05*inch) if not ln else Paragraph(ln, LETTER_S))
e.append(PageBreak())

# ─── FOLLOW-UP ───
e.append(Paragraph('📨 FOLLOW-UP CORTO (si no responde en 7-10 días)', H2))
fu = [
    '<b>Versión ES:</b>',
    '',
    'Asunto: <b>Re: Interés en Jasmine Apartments — algunas preguntas (Ross House Rentals)</b>',
    '',
    'Hola Joe,',
    '',
    'Solo paso a confirmar que recibió mi correo anterior. Entiendo perfectamente si está ocupado.',
    '',
    'Si las preguntas que mandé son muchas para responder de una sola vez, no hay problema — '
    'puede responderme solo las 2 o 3 más importantes para usted (precio actual y motivo de venta '
    'serían las claves para mí en esta etapa).',
    '',
    'Si en este momento no quiere avanzar, también es totalmente válido — solo dígame y lo dejo descansar.',
    '',
    'Gracias,<br/>Yoandy Ross<br/>(806) 934-2018',
    '',
    '─────────────────────────────',
    '',
    '<b>English version:</b>',
    '',
    'Subject: <b>Re: Interest in Jasmine Apartments — a few questions (Ross House Rentals)</b>',
    '',
    'Hi Joe,',
    '',
    'Just confirming you received my previous email. I completely understand if you are busy.',
    '',
    'If the list of questions I sent is too long to answer all at once, no problem — feel free to '
    'reply with just the 2 or 3 most important ones for you (current asking price and reason for '
    'selling would be the key ones for me at this stage).',
    '',
    'If you don\'t want to move forward at this time, that is also completely fine — just let me '
    'know and I will let it rest.',
    '',
    'Thanks,<br/>Yoandy Ross<br/>(806) 934-2018',
]
for ln in fu:
    e.append(Spacer(1, 0.05*inch) if not ln else Paragraph(ln, LETTER_S))
e.append(PageBreak())

# ─── TIPS ───
e.append(Paragraph('💡 CONSEJOS ESTRATÉGICOS PARA ESTE EMAIL', H2))
tips = [
    '<b>1. Manda este email desde info@rosshouserentals.com</b>, no desde Gmail personal. Es 10x más profesional y demuestra que detrás hay una empresa real.',
    '<b>2. Mándalo un martes o miércoles entre 9-11 AM (hora central).</b> Esos son los horarios con mayor tasa de apertura para emails B2B.',
    '<b>3. NO menciones tu precio aún.</b> La pregunta #2 ("¿cuál es su asking price?") es la más importante. Si él responde con un número, ya tienes el ancla de él, no la tuya.',
    '<b>4. NO ofrezcas estructura de financiamiento todavía.</b> La pregunta #6 explora si está abierto a seller financing sin que tú propongas nada. Si dice "sí", tienes apalancamiento adicional.',
    '<b>5. Documenta CADA respuesta que él dé por email.</b> Si en el futuro hay LOI o discusión de términos, ese histórico vale oro. Guarda cada email en una carpeta dedicada.',
    '<b>6. Si él responde con preguntas hacia ti (capital, experiencia, timeline), responde con sinceridad pero sin números exactos.</b> Ejemplo: "Tengo financiamiento en proceso con bancos locales + capital privado, capacidad estimada en el rango medio de 7 cifras, depende de los términos finales".',
    '<b>7. Si él responde solo con una frase tipo "Sí, todavía está disponible — llámame al X teléfono".</b> Entonces sí, ahí ya es momento de llamar. Pero deja que él tome esa iniciativa.',
    '<b>8. Si él responde NO o que ya está vendido.</b> Agradece y deja la puerta abierta: "Entiendo. Si en el futuro cambia la situación, no dude en contactarme." A veces los deals se caen en due diligence y te buscan después.',
    '<b>9. Si él manda T-12 o rent roll sin pedir NDA.</b> Eres muy afortunado — significa que confía. Procesa los números YA y vuelve con preguntas inteligentes específicas (no genéricas).',
    '<b>10. Tu siguiente paso si NO responde después del follow-up (día 14).</b> Ahí sí, levantas el teléfono: (806) 922-7222 (su oficina local en TX). Pero el email-first te da la mejor primera impresión posible.',
]
for t in tips:
    e.append(Paragraph(t, LETTER_S))

doc.build(e)
print(f'✅ PDF: {OUT} ({os.path.getsize(OUT)/1024:.1f} KB)')

# ─── SEND EMAIL ───
api_key = os.environ['SENDGRID_API_KEY']
from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')

with open(OUT, 'rb') as f:
    pdf_b64 = base64.b64encode(f.read()).decode()

html = """
<div style="font-family:Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;background:#f1f5f9;padding:24px;">
  <div style="background:#0d1a2e;color:#fff;padding:22px 26px;border-radius:14px 14px 0 0;">
    <div style="font-size:11px;color:#fbbf24;font-weight:800;letter-spacing:1px;text-transform:uppercase;">Email-Only Inquiry · Jasmine Apartments</div>
    <h1 style="margin:8px 0 0 0;font-size:22px;">📧 Versión sin pedir llamada — preguntas concretas por email</h1>
  </div>
  <div style="background:#fff;padding:24px 26px;border-radius:0 0 14px 14px;border:1px solid #e2e8f0;border-top:none;">
    <p style="font-size:15px;line-height:1.6;margin:0 0 14px 0;">Hola Yoandy,</p>
    <p style="font-size:14px;line-height:1.65;color:#334155;margin:0 0 14px 0;">
      Como me pediste, te preparé una <strong>versión all-in-email</strong> — sin pedir llamada,
      con 10 preguntas específicas que Joe puede responder por escrito cuando le venga bien.
      Mucho más cómodo para él y, además, te deja todas sus respuestas <strong>por escrito</strong>
      (oro puro si esto avanza a negociación).
    </p>
    <div style="margin:14px 0;padding:14px 16px;background:#ecfdf5;border-left:4px solid #10b981;border-radius:6px;font-size:13px;color:#065f46;">
      <strong>📋 Las 10 preguntas que le hago a Joe:</strong>
      <ol style="margin:8px 0 0 18px;padding:0;line-height:1.65;">
        <li>¿Sigue disponible para la venta el portafolio completo?</li>
        <li><strong>¿Cuál es su asking price actual?</strong> (la más importante)</li>
        <li>¿Tasa de ocupación promedio últimos 12 meses?</li>
        <li>¿NOI aproximado del último año fiscal?</li>
        <li>¿Año de construcción + últimas renovaciones mayores?</li>
        <li>¿Abierto a seller financing parcial?</li>
        <li>¿Motivo principal de la venta?</li>
        <li>¿Dispuesto a firmar NDA para T-12 + rent roll?</li>
        <li>¿Cuántas ofertas o LOIs ha recibido?</li>
        <li>¿Timeline preferido para cerrar?</li>
      </ol>
    </div>
    <p style="font-size:14px;line-height:1.65;color:#334155;margin:0 0 10px 0;">
      <strong>PDF adjunto (5 páginas):</strong>
    </p>
    <ul style="font-size:14px;line-height:1.7;color:#334155;margin:0 0 16px 18px;padding:0;">
      <li>📄 <strong>Pág 1:</strong> Estrategia + asuntos sugeridos para el email</li>
      <li>🇪🇸 <strong>Pág 2:</strong> Email completo en español (listo para copiar)</li>
      <li>🇺🇸 <strong>Pág 3:</strong> Email completo en inglés</li>
      <li>📨 <strong>Pág 4:</strong> Follow-up corto si no responde en 7-10 días</li>
      <li>💡 <strong>Pág 5:</strong> 10 consejos estratégicos para esta etapa</li>
    </ul>
    <div style="margin:18px 0;padding:14px 16px;background:#fffbeb;border-left:4px solid #f59e0b;border-radius:6px;font-size:13px;color:#78350f;">
      <strong>Regla M&amp;A crítica:</strong> NO menciones tu precio. La pregunta #2 es la más importante —
      si Joe te responde con su asking price, ya tienes la ancla de él y todo el margen de negociación
      a tu favor.
    </div>
    <p style="font-size:14px;line-height:1.65;color:#334155;margin:0 0 14px 0;">
      <strong>Diferencia vs la versión anterior (Soft Inquiry):</strong> aquella pedía una llamada de 15-20 min.
      Esta NO pide llamada — solo respuestas por email. Es menos fricción para él y te deja todo documentado.
      Si después él propone llamada, perfecto. Pero la iniciativa la deja él.
    </p>
    <p style="font-size:13px;color:#64748b;margin:18px 0 0 0;">
      Cuando estés listo para enviar, copia el texto de la página 2 (o 3 si prefieres inglés) en tu Gmail
      empresarial <code style="background:#f1f5f9;padding:1px 5px;border-radius:3px;">info@rosshouserentals.com</code>
      y mándalo a <code style="background:#f1f5f9;padding:1px 5px;border-radius:3px;">joe3359@gmail.com</code>.
    </p>
  </div>
  <div style="text-align:center;color:#94a3b8;font-size:11px;margin-top:14px;">
    Ross House Rentals LLC · Dumas, TX · (806) 934-2018
  </div>
</div>
"""

msg = Mail(
    from_email=from_email,
    to_emails='yoandyross@gmail.com',
    subject='📧 Jasmine Apartments — Email-Only Inquiry (10 preguntas concretas, sin pedir llamada)',
    plain_text_content='Te adjunto el PDF con la versión "email-only" — 10 preguntas concretas para Joe Kuruvila que puede responder por escrito sin necesidad de coordinar llamada.',
    html_content=html,
)
att = Attachment()
att.file_content = FileContent(pdf_b64)
att.file_name = FileName('Jasmine_Email_Only_Inquiry_Ross_House.pdf')
att.file_type = FileType('application/pdf')
att.disposition = Disposition('attachment')
msg.attachment = att

resp = SendGridAPIClient(api_key).send(msg)
print(f'✅ Email status: {resp.status_code}')
