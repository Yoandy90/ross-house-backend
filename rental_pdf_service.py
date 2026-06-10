"""
Rental Contract PDF Service — Ross House Rentals LLC
=====================================================
Texas Chapter 92 Compliant Bilingual (ES/EN) Lease Agreement Generator.
Generates professional PDF contracts, addendums, and legal notices.
Uses ReportLab for PDF generation.
"""
import io
import os
import base64
import logging
from datetime import datetime, timedelta
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether, ListFlowable, ListItem,
    Image as RLImage
)

logger = logging.getLogger(__name__)

# ─── Default Company Configuration ───────────────────────────────
DEFAULT_COMPANY = {
    "name": "Ross House Rentals LLC",
    "address": "305 Bruce Ave, Dumas, TX 79029",
    "phone": "(806) 934-2018",
    "email": "info@rosshouserentals.com",
    "website": "www.rosshouserentals.com",
    "state": "Texas",
    "county": "Moore",
}

# ─── Color Palette (Ross House Rentals Brand) ────────────────────
BRAND_RED = colors.HexColor('#ED1B33')       # Primary brand red
BRAND_CHARCOAL = colors.HexColor('#231F20')  # Secondary charcoal/near-black
NAVY = colors.HexColor('#1E3A5F')            # Trust, legal, contracts
BLUE = colors.HexColor('#2b6cb0')
LIGHT_BLUE = colors.HexColor('#ebf4ff')
DARK_GRAY = colors.HexColor('#231F20')       # Use brand charcoal
GRAY = colors.HexColor('#4a5568')
LIGHT_GRAY = colors.HexColor('#f7fafc')
BORDER_GRAY = colors.HexColor('#cbd5e0')
MUTED_GRAY = colors.HexColor('#718096')
AMBER = colors.HexColor('#92400e')
RED = colors.HexColor('#ED1B33')             # Use brand red
GREEN = colors.HexColor('#276749')
BRAND_CREAM = colors.HexColor('#FAF3E8')     # Warm cream background
BRAND_GOLD = colors.HexColor('#F5A623')      # Premium gold accent


def _get_logo_path():
    """Find Ross House Rentals logo in assets folder"""
    base = os.path.dirname(os.path.abspath(__file__))
    # Prioritize Ross House Rentals logo
    for name in ['ross_house_logo.png', 'company_logo.png', 'ross_logo.png']:
        path = os.path.join(base, 'assets', name)
        if os.path.exists(path):
            return path
    # Also check memory folder as fallback
    memory_path = '/app/memory/ross_house_logo.png'
    if os.path.exists(memory_path):
        return memory_path
    return None


def _build_styles():
    """Build all PDF styles"""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='DocTitle', fontName='Helvetica-Bold', fontSize=14,
        textColor=BRAND_CHARCOAL, spaceAfter=2, alignment=TA_CENTER, leading=18
    ))
    styles.add(ParagraphStyle(
        name='DocSubtitle', fontName='Helvetica', fontSize=10,
        textColor=GRAY, spaceAfter=2, alignment=TA_CENTER, leading=13
    ))
    styles.add(ParagraphStyle(
        name='CompanyInfo', fontName='Helvetica', fontSize=8,
        textColor=GRAY, spaceAfter=1, alignment=TA_CENTER, leading=11
    ))
    styles.add(ParagraphStyle(
        name='SectionNum', fontName='Helvetica-Bold', fontSize=11,
        textColor=BRAND_CHARCOAL, spaceBefore=14, spaceAfter=6, leading=14
    ))
    styles.add(ParagraphStyle(
        name='SubSection', fontName='Helvetica-Bold', fontSize=9,
        textColor=DARK_GRAY, spaceBefore=8, spaceAfter=4, leading=12
    ))
    styles.add(ParagraphStyle(
        name='Body', fontName='Helvetica', fontSize=8.5,
        textColor=DARK_GRAY, leading=12, alignment=TA_JUSTIFY,
        spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        name='BodyBold', fontName='Helvetica-Bold', fontSize=8.5,
        textColor=DARK_GRAY, leading=12, alignment=TA_JUSTIFY,
        spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        name='BodySmall', fontName='Helvetica', fontSize=7.5,
        textColor=GRAY, leading=10, alignment=TA_JUSTIFY,
        spaceAfter=3
    ))
    styles.add(ParagraphStyle(
        name='LegalRef', fontName='Helvetica-Oblique', fontSize=7,
        textColor=MUTED_GRAY, leading=9, spaceAfter=2
    ))
    styles.add(ParagraphStyle(
        name='Footer', fontName='Helvetica', fontSize=6.5,
        textColor=MUTED_GRAY, leading=9
    ))
    styles.add(ParagraphStyle(
        name='Warning', fontName='Helvetica-Bold', fontSize=8,
        textColor=RED, leading=11, spaceAfter=4
    ))
    styles.add(ParagraphStyle(
        name='AddendumTitle', fontName='Helvetica-Bold', fontSize=10,
        textColor=AMBER, spaceBefore=10, spaceAfter=6, alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        name='InitialLine', fontName='Helvetica', fontSize=8,
        textColor=DARK_GRAY, leading=11, spaceBefore=6, spaceAfter=2
    ))
    return styles


def format_currency(amount):
    try:
        return f"${float(amount):,.2f}"
    except Exception:
        return "$0.00"


def _number_to_words(n):
    """Simple number to English words for amounts"""
    ones = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
            'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
            'seventeen', 'eighteen', 'nineteen']
    tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
    try:
        n = int(float(n))
        if n < 20:
            return ones[n]
        if n < 100:
            return tens[n // 10] + ('' if n % 10 == 0 else '-' + ones[n % 10])
        if n < 1000:
            return ones[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + _number_to_words(n % 100))
        if n < 10000:
            return _number_to_words(n // 1000) + ' thousand' + ('' if n % 1000 == 0 else ' ' + _number_to_words(n % 1000))
        return str(n)
    except Exception:
        return str(n)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN CONTRACT GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_rental_contract_pdf(contract: dict, config: dict = None, tenant_photo_url: str = None) -> str:
    """
    Generate a comprehensive Texas-compliant bilingual lease agreement PDF.
    Returns base64-encoded PDF string.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.5 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch
    )

    styles = _build_styles()
    elements = []

    # Merge config with defaults
    co = {**DEFAULT_COMPANY}
    if config:
        for k in ['name', 'address', 'phone', 'email', 'website', 'state', 'county']:
            if config.get(k):
                co[k] = config[k]

    # Get addendums from contract
    addendums = contract.get('addendums', {})
    pets_allowed = addendums.get('pets', False)
    pet_details = addendums.get('pet_details', {})
    mold_addendum = addendums.get('mold', True)  # Default to True (required by TX)
    bedbug_addendum = addendums.get('bedbug', True)  # Default to True
    military_addendum = addendums.get('military', True)  # SCRA
    lead_paint = addendums.get('lead_paint', False)

    # Payment method
    pm_type = contract.get('payment_method_type', 'cash')

    # ─── HEADER WITH LOGO ────────────────────────────────────────────
    logo_path = _get_logo_path()
    if logo_path:
        try:
            # Ross House Rentals logo is 2.29:1 aspect ratio — render correctly
            logo = RLImage(logo_path, width=2.5 * inch, height=1.1 * inch)
            logo.hAlign = 'CENTER'

            header_data = [
                [logo],
                [Spacer(1, 4)],
                [Paragraph(f"{co['address']}  •  {co['phone']}  •  {co['email']}", styles['CompanyInfo'])],
                [Paragraph(co.get('website', ''), styles['CompanyInfo'])],
            ]
            ht = Table(header_data, colWidths=[480])
            ht.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
            elements.append(ht)
        except Exception as e:
            logger.warning(f"Could not load logo: {e}")
            elements.append(Paragraph(co['name'].upper(), styles['DocTitle']))
            elements.append(Paragraph(f"{co['address']}  •  {co['phone']}  •  {co['email']}", styles['CompanyInfo']))
    else:
        elements.append(Paragraph(co['name'].upper(), styles['DocTitle']))
        elements.append(Paragraph(f"{co['address']}  •  {co['phone']}  •  {co['email']}", styles['CompanyInfo']))

    elements.append(Spacer(1, 6))
    elements.append(HRFlowable(width="100%", thickness=2, color=BRAND_RED))
    elements.append(Spacer(1, 4))

    # ─── BILINGUAL TITLE ─────────────────────────────────────────────
    elements.append(Paragraph(
        "RESIDENTIAL LEASE AGREEMENT / CONTRATO DE ARRENDAMIENTO RESIDENCIAL",
        styles['DocTitle']
    ))
    elements.append(Paragraph(
        f"Contract No. / Contrato N°: <b>{contract.get('contract_number', 'N/A')}</b>",
        styles['DocSubtitle']
    ))
    elements.append(Paragraph(
        f"Date / Fecha: {datetime.utcnow().strftime('%m/%d/%Y')}",
        styles['DocSubtitle']
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_GRAY))
    elements.append(Spacer(1, 10))

    # ─── LEGAL NOTICE ────────────────────────────────────────────────
    elements.append(Paragraph(
        "<i>This lease agreement is governed by the laws of the State of Texas, including Texas Property Code "
        "Chapter 92. Both English and Spanish versions are provided; in case of conflict, the English version prevails.</i>",
        styles['BodySmall']
    ))
    elements.append(Paragraph(
        "<i>Este contrato de arrendamiento se rige por las leyes del Estado de Texas, incluido el Código de "
        "Propiedad de Texas Capítulo 92. Se proporcionan versiones en inglés y español; en caso de conflicto, "
        "prevalece la versión en inglés.</i>",
        styles['BodySmall']
    ))
    elements.append(Spacer(1, 8))

    # ═══ SECTION 1: PARTIES / PARTES ══════════════════════════════════
    section = 1
    elements.append(Paragraph(
        f"{section}. PARTIES / PARTES DEL CONTRATO", styles['SectionNum']
    ))

    parties_data = [
        ['LANDLORD / ARRENDADOR', 'TENANT / ARRENDATARIO'],
        [co['name'], contract.get('tenant_name', '')],
        [co['address'], contract.get('tenant_phone', '')],
        [f"{co['phone']} / {co['email']}", contract.get('tenant_email', '')],
    ]
    t = Table(parties_data, colWidths=[235, 235])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_BLUE),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8))

    # ─── TENANT IDENTIFICATION PHOTO ─────────────────────────────────
    if tenant_photo_url:
        try:
            import urllib.request
            photo_data = urllib.request.urlopen(tenant_photo_url, timeout=10).read()
            photo_buffer = io.BytesIO(photo_data)
            tenant_photo = RLImage(photo_buffer, width=1.2 * inch, height=1.5 * inch)
            tenant_photo.hAlign = 'LEFT'

            photo_table_data = [
                [Paragraph("<b>Tenant Identification Photo / Foto de Identificación del Arrendatario</b>", styles['BodySmall']), ''],
                [tenant_photo, Paragraph(
                    f"<b>{contract.get('tenant_name', 'N/A')}</b><br/>"
                    f"Photo taken at Ross House Rentals office<br/>"
                    f"Foto tomada en la oficina de Ross House Rentals<br/>"
                    f"Date / Fecha: {datetime.utcnow().strftime('%m/%d/%Y')}",
                    styles['BodySmall']
                )],
            ]
            pt = Table(photo_table_data, colWidths=[110, 360])
            pt.setStyle(TableStyle([
                ('SPAN', (0, 0), (1, 0)),
                ('BACKGROUND', (0, 0), (-1, 0), LIGHT_GRAY),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ]))
            elements.append(pt)
            elements.append(Spacer(1, 8))
        except Exception as e:
            logger.warning(f"Could not include tenant photo in contract: {e}")

    # ═══ SECTION 2: PROPERTY / PROPIEDAD ═════════════════════════════
    section += 1
    elements.append(Paragraph(
        f"{section}. PREMISES / PROPIEDAD EN ARRENDAMIENTO", styles['SectionNum']
    ))
    elements.append(Paragraph(
        f"Landlord leases to Tenant the property located at: <b>{contract.get('property_address', 'N/A')}</b> "
        f"(Property No. {contract.get('property_number', 'N/A')}), in the County of {co.get('county', 'Moore')}, "
        f"State of {co.get('state', 'Texas')}, together with all improvements, fixtures, and appurtenances thereto.",
        styles['Body']
    ))
    elements.append(Paragraph(
        f"El Arrendador arrienda al Arrendatario la propiedad ubicada en: <b>{contract.get('property_address', 'N/A')}</b> "
        f"(Propiedad N° {contract.get('property_number', 'N/A')}), en el Condado de {co.get('county', 'Moore')}, "
        f"Estado de {co.get('state', 'Texas')}, junto con todas las mejoras, accesorios y pertenencias.",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 3: LEASE TERM / PLAZO ═══════════════════════════════
    section += 1
    start = contract.get('start_date', 'N/A')
    end = contract.get('end_date', 'Month-to-Month')
    is_mtm = not end or end == ''
    end_display = end if end else 'Month-to-Month / Mes a Mes'

    elements.append(Paragraph(
        f"{section}. LEASE TERM / PLAZO DEL ARRENDAMIENTO", styles['SectionNum']
    ))
    elements.append(Paragraph(
        f"This Lease begins on <b>{start}</b> and ends on <b>{end_display}</b>. "
        f"{'This is a month-to-month tenancy that may be terminated by either party with 30 days written notice.' if is_mtm else 'Upon expiration, this lease shall automatically convert to a month-to-month tenancy unless renewed in writing.'}",
        styles['Body']
    ))
    elements.append(Paragraph(
        f"Este Contrato comienza el <b>{start}</b> y termina el <b>{end_display}</b>. "
        f"{'Esta es una tenencia de mes a mes que puede ser terminada por cualquiera de las partes con 30 días de aviso por escrito.' if is_mtm else 'Al vencimiento, este contrato se convertirá automáticamente en una tenencia de mes a mes a menos que se renueve por escrito.'}",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 4: RENT & PAYMENT / RENTA Y PAGO ════════════════════
    section += 1
    rent = contract.get('rent_amount', 0)
    deposit = contract.get('deposit_amount', 0)
    due_day = contract.get('payment_due_day', 1)
    late_fee = contract.get('late_fee_amount', 50)
    grace_days = contract.get('late_fee_grace_days', 5)

    elements.append(Paragraph(
        f"{section}. RENT AND PAYMENTS / RENTA Y PAGOS", styles['SectionNum']
    ))

    # Payment method labels
    pm_labels = {
        'cash': 'Cash / Efectivo',
        'card': f"Credit/Debit Card / Tarjeta ({contract.get('vault_display', '')})",
        'ach': f"ACH Bank Debit / Débito Bancario ({contract.get('vault_display', '')})",
        'transfer': 'Bank Transfer / Transferencia Bancaria',
    }
    pm_label = pm_labels.get(pm_type, 'Cash / Efectivo')

    terms_data = [
        ['Concept / Concepto', 'Detail / Detalle'],
        ['Monthly Rent / Renta Mensual', f"{format_currency(rent)} ({_number_to_words(rent)} dollars)"],
        ['Security Deposit / Depósito de Seguridad', format_currency(deposit)],
        ['Due Date / Día de Pago', f"Day {due_day} of each month / Día {due_day} de cada mes"],
        ['Payment Method / Método de Pago', pm_label],
        ['Late Fee / Cargo por Mora', f"{format_currency(late_fee)} after {grace_days} grace days / después de {grace_days} días de gracia"],
        ['Grace Period / Período de Gracia', f"{grace_days} calendar days / días calendario"],
    ]
    t = Table(terms_data, colWidths=[180, 290])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('BACKGROUND', (0, 1), (0, -1), LIGHT_GRAY),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 6))

    # ─── 4.1 Acceleration Clause ─────────────────────────────────────
    elements.append(Paragraph(f"{section}.1 ACCELERATION CLAUSE / CLÁUSULA DE ACELERACIÓN", styles['SubSection']))
    elements.append(Paragraph(
        "If Tenant fails to pay rent within <b>10 days</b> after the due date, all remaining rent for the entire lease term "
        "shall become immediately due and payable at Landlord's option. Landlord may pursue legal remedies including "
        "but not limited to eviction proceedings under Texas Property Code §24.005.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Si el Arrendatario no paga la renta dentro de <b>10 días</b> después de la fecha de vencimiento, toda la renta "
        "restante del plazo completo del contrato será exigible de inmediato a opción del Arrendador. El Arrendador "
        "podrá perseguir remedios legales incluyendo pero no limitado a procedimientos de desalojo bajo el Código "
        "de Propiedad de Texas §24.005.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Ref: Texas Property Code §92.019 — Tenant's Right to Deduct for Repairs (does not affect acceleration rights)",
        styles['LegalRef']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 5: SECURITY DEPOSIT / DEPÓSITO ══════════════════════
    section += 1
    elements.append(Paragraph(
        f"{section}. SECURITY DEPOSIT / DEPÓSITO DE SEGURIDAD (Texas Property Code §92.101-§92.109)",
        styles['SectionNum']
    ))
    elements.append(Paragraph(
        f"Tenant shall pay a security deposit of <b>{format_currency(deposit)}</b> upon execution of this lease. "
        "The deposit shall be held by Landlord as security for Tenant's faithful performance of all obligations. "
        "Per Texas Property Code §92.103, the deposit shall be refunded within <b>30 days</b> after Tenant "
        "surrenders the premises, less any lawful deductions for: (a) unpaid rent; (b) damages beyond normal "
        "wear and tear; (c) breach of lease; (d) cleaning costs to restore the unit to move-in condition.",
        styles['Body']
    ))
    elements.append(Paragraph(
        f"El Arrendatario pagará un depósito de seguridad de <b>{format_currency(deposit)}</b> al ejecutar este contrato. "
        "El depósito será retenido por el Arrendador como garantía del cumplimiento fiel de todas las obligaciones. "
        "Según el Código de Propiedad de Texas §92.103, el depósito será reembolsado dentro de <b>30 días</b> "
        "después de que el Arrendatario entregue las instalaciones, menos deducciones legales por: (a) renta impaga; "
        "(b) daños más allá del desgaste normal; (c) incumplimiento del contrato; (d) costos de limpieza.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Tenant must provide Landlord with a forwarding address in writing. If Tenant fails to provide a "
        "forwarding address, Landlord's obligation to refund the deposit or provide an itemized accounting "
        "does not begin until the tenant provides the address. (TX Prop. Code §92.107)",
        styles['BodySmall']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 6: LANDLORD OBLIGATIONS / OBLIGACIONES DEL ARRENDADOR ═
    section += 1
    elements.append(Paragraph(
        f"{section}. LANDLORD'S OBLIGATIONS / OBLIGACIONES DEL ARRENDADOR (TX Prop. Code §92.051-§92.061)",
        styles['SectionNum']
    ))
    landlord_obligations_en = [
        "Landlord shall make diligent effort to repair or remedy a condition that materially affects the physical health or safety of an ordinary tenant if: (a) Tenant gives notice of the condition; (b) Tenant is not delinquent in rent at the time notice is given (§92.052).",
        "Landlord shall install and maintain smoke detectors (§92.251-§92.261) and provide working locks, including a doorknob lock, deadbolt, sliding door pin lock, and peephole on each exterior door (§92.151-§92.170).",
        "Landlord shall not interrupt utilities or remove doors/windows as retaliation (§92.008-§92.009).",
        "Landlord shall give Tenant at least 24 hours' notice before entering the premises, except in emergencies.",
    ]
    for ob in landlord_obligations_en:
        elements.append(Paragraph(f"• {ob}", styles['BodySmall']))

    elements.append(Spacer(1, 3))
    landlord_obligations_es = [
        "El Arrendador hará un esfuerzo diligente para reparar una condición que afecte materialmente la salud o seguridad física de un inquilino ordinario si: (a) el Arrendatario notifica la condición; (b) el Arrendatario no está en mora con la renta al momento de la notificación (§92.052).",
        "El Arrendador instalará y mantendrá detectores de humo (§92.251-§92.261) y proporcionará cerraduras funcionales, incluyendo cerradura de perilla, cerrojo, pasador de puerta corrediza y mirilla en cada puerta exterior (§92.151-§92.170).",
        "El Arrendador no interrumpirá servicios públicos ni removerá puertas/ventanas como represalia (§92.008-§92.009).",
        "El Arrendador dará al Arrendatario al menos 24 horas de aviso antes de ingresar, excepto en emergencias.",
    ]
    for ob in landlord_obligations_es:
        elements.append(Paragraph(f"• {ob}", styles['BodySmall']))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 7: TENANT OBLIGATIONS / OBLIGACIONES DEL ARRENDATARIO ═
    section += 1
    elements.append(Paragraph(
        f"{section}. TENANT'S OBLIGATIONS / OBLIGACIONES DEL ARRENDATARIO", styles['SectionNum']
    ))
    tenant_obligations = [
        ("Pay rent on the due date. / Pagar la renta en la fecha acordada."),
        ("Maintain the property in good condition and report damages immediately. / Mantener la propiedad en buen estado y reportar daños inmediatamente."),
        ("Do not make structural modifications without written Landlord approval. / No realizar modificaciones estructurales sin autorización escrita del Arrendador."),
        ("Do not sublease or assign this lease without prior written consent. / No subarrendar ni ceder este contrato sin consentimiento previo por escrito."),
        ("Comply with all local ordinances, housing codes, and applicable laws. / Cumplir con todas las ordenanzas locales, códigos de vivienda y leyes aplicables."),
        ("Return the premises in the same condition (normal wear excepted) upon lease termination. / Devolver la propiedad en las mismas condiciones (desgaste normal excluido) al terminar el contrato."),
        ("Allow Landlord access for inspection with 24-hour notice. / Permitir al Arrendador acceso para inspección con aviso de 24 horas."),
        ("Not engage in criminal activity on or near the premises. / No participar en actividad criminal en o cerca de las instalaciones."),
        ("Properly dispose of garbage and maintain cleanliness. / Desechar basura adecuadamente y mantener la limpieza."),
    ]
    for i, ob in enumerate(tenant_obligations, 1):
        elements.append(Paragraph(f"  {i}. {ob}", styles['Body']))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 8: UTILITIES / SERVICIOS ════════════════════════════
    section += 1
    elements.append(Paragraph(
        f"{section}. UTILITIES / SERVICIOS PÚBLICOS", styles['SectionNum']
    ))
    elements.append(Paragraph(
        "Tenant shall be responsible for payment of all utilities including electricity, gas, water, sewer, "
        "trash, internet, and cable unless otherwise specified. Landlord shall not be liable for any interruption "
        "of utility services not caused by Landlord's negligence.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "El Arrendatario será responsable del pago de todos los servicios públicos incluyendo electricidad, gas, "
        "agua, alcantarillado, basura, internet y cable a menos que se especifique lo contrario. El Arrendador "
        "no será responsable por ninguna interrupción de servicios no causada por negligencia del Arrendador.",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION 9: ACH AUTHORIZATION (if applicable) ════════════════
    if pm_type == 'ach':
        section += 1
        elements.append(Paragraph(
            f"{section}. ACH DEBIT AUTHORIZATION / AUTORIZACIÓN DE DÉBITO BANCARIO (ACH)",
            styles['SectionNum']
        ))
        elements.append(Paragraph(
            f"I, <b>{contract.get('tenant_name', '___')}</b>, hereby authorize <b>{co['name']}</b> to initiate "
            f"automatic ACH debit entries from my bank account in the amount of <b>{format_currency(rent)}</b> "
            f"on day <b>{due_day}</b> of each month for rent payment of the property at "
            f"<b>{contract.get('property_address', '')}</b>.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "This authorization shall remain in effect until revoked in writing with a minimum of <b>30 days</b> "
            "advance notice. I understand I may revoke this authorization at any time by written notice to "
            f"{co['name']} at {co['address']} or via email to {co['email']}. In case of erroneous debits, "
            "I have the right to request a reversal within 60 days per NACHA regulations.",
            styles['Body']
        ))
        elements.append(Spacer(1, 2))
        elements.append(Paragraph(
            f"Yo, <b>{contract.get('tenant_name', '___')}</b>, autorizo a <b>{co['name']}</b> a iniciar "
            f"débitos automáticos ACH de mi cuenta bancaria por <b>{format_currency(rent)}</b> "
            f"el día <b>{due_day}</b> de cada mes. Esta autorización permanecerá vigente hasta ser revocada "
            "por escrito con mínimo <b>30 días</b> de anticipación conforme a regulaciones NACHA.",
            styles['Body']
        ))
        elements.append(Spacer(1, 6))

    # ═══ SECTION: TERMINATION & NOTICES ══════════════════════════════
    section += 1
    elements.append(Paragraph(
        f"{section}. TERMINATION AND NOTICES / TERMINACIÓN Y AVISOS (TX Prop. Code §24.005)",
        styles['SectionNum']
    ))
    elements.append(Paragraph(
        "Either party may terminate this lease with <b>30 days'</b> written notice to the other party. "
        "Landlord may terminate the lease immediately and issue a <b>3-day notice to vacate</b> for: "
        "(a) nonpayment of rent; (b) holding over after lease expiration; (c) criminal activity on the premises. "
        "Upon lease termination, Tenant shall vacate and surrender the premises in clean condition.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Cualquiera de las partes puede terminar este contrato con <b>30 días</b> de aviso por escrito. "
        "El Arrendador puede terminar inmediatamente con un <b>aviso de 3 días para desalojar</b> por: "
        "(a) falta de pago; (b) permanencia después del vencimiento; (c) actividad criminal en las instalaciones. "
        "Al terminar el contrato, el Arrendatario desalojará y entregará en condiciones limpias.",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION: SPECIAL CONDITIONS ═════════════════════════════════
    if contract.get('special_conditions') or contract.get('terms'):
        section += 1
        elements.append(Paragraph(
            f"{section}. SPECIAL CONDITIONS / CONDICIONES ESPECIALES", styles['SectionNum']
        ))
        if contract.get('terms'):
            elements.append(Paragraph(contract['terms'], styles['Body']))
        if contract.get('special_conditions'):
            elements.append(Paragraph(contract['special_conditions'], styles['Body']))
        elements.append(Spacer(1, 6))

    # ═══ SECTION: GOVERNING LAW / LEY APLICABLE ═════════════════════
    section += 1
    elements.append(Paragraph(
        f"{section}. GOVERNING LAW / LEY APLICABLE", styles['SectionNum']
    ))
    elements.append(Paragraph(
        f"This Lease shall be governed by and construed in accordance with the laws of the State of "
        f"{co.get('state', 'Texas')}, including the Texas Property Code Chapter 92 (Residential Tenancies). "
        "Any disputes arising under this lease shall be resolved in the courts of "
        f"{co.get('county', 'Moore')} County, Texas.",
        styles['Body']
    ))
    elements.append(Paragraph(
        f"Este Contrato se regirá por las leyes del Estado de {co.get('state', 'Texas')}, incluyendo el "
        "Código de Propiedad de Texas Capítulo 92 (Arrendamientos Residenciales). Cualquier disputa se "
        f"resolverá en los tribunales del Condado de {co.get('county', 'Moore')}, Texas.",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))

    # ═══ SECTION: ENTIRE AGREEMENT ═══════════════════════════════════
    section += 1
    elements.append(Paragraph(
        f"{section}. ENTIRE AGREEMENT / ACUERDO COMPLETO", styles['SectionNum']
    ))
    elements.append(Paragraph(
        "This Lease, including all addenda and attachments, constitutes the entire agreement between "
        "the parties. No oral agreements or modifications shall be binding unless made in writing and "
        "signed by both parties.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Este Contrato, incluyendo todos los addenda y anexos, constituye el acuerdo completo entre las "
        "partes. Ningún acuerdo oral será vinculante a menos que se haga por escrito y sea firmado por "
        "ambas partes.",
        styles['Body']
    ))

    # ═══════════════════════════════════════════════════════════════════
    # ADDENDUMS
    # ═══════════════════════════════════════════════════════════════════
    addendum_letter = 'A'

    # ─── ADDENDUM A: MOLD DISCLOSURE (TX §92.151-§92.157) ───────────
    if mold_addendum:
        elements.append(PageBreak())
        elements.append(Paragraph(
            f"ADDENDUM {addendum_letter}: MOLD INFORMATION AND PREVENTION / "
            f"ADDENDUM {addendum_letter}: INFORMACIÓN Y PREVENCIÓN DE MOHO",
            styles['AddendumTitle']
        ))
        elements.append(Paragraph("(Per Texas Property Code §92.151-§92.157)", styles['LegalRef']))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(
            "<b>DISCLOSURE:</b> Mold is found virtually everywhere in the environment — both indoors and outdoors. "
            "Landlord has no knowledge of the presence of mold on the premises as of the date of this lease. "
            "Tenant is responsible for preventing conditions that lead to mold growth by:",
            styles['Body']
        ))
        mold_prevention = [
            "Maintaining adequate ventilation, especially in bathrooms and kitchens.",
            "Using exhaust fans during and after bathing and cooking.",
            "Immediately reporting any water leaks, drips, or moisture to Landlord.",
            "Not blocking air vents or HVAC returns.",
            "Keeping the premises reasonably clean and dry.",
            "Promptly notifying Landlord of any visible mold, mildew, or musty odors.",
        ]
        for m in mold_prevention:
            elements.append(Paragraph(f"• {m}", styles['BodySmall']))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            "<b>DIVULGACIÓN:</b> El moho se encuentra prácticamente en todas partes del medio ambiente. "
            "El Arrendador no tiene conocimiento de la presencia de moho en las instalaciones a la fecha de "
            "este contrato. El Arrendatario es responsable de prevenir condiciones que lleven al crecimiento "
            "de moho manteniendo ventilación adecuada, reportando fugas inmediatamente, y notificando al "
            "Arrendador de cualquier moho visible o olores a humedad.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________",
            styles['InitialLine']
        ))
        addendum_letter = chr(ord(addendum_letter) + 1)

    # ─── ADDENDUM B: BED BUG DISCLOSURE ─────────────────────────────
    if bedbug_addendum:
        elements.append(Spacer(1, 12))
        elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_GRAY))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            f"ADDENDUM {addendum_letter}: BED BUG ADDENDUM / "
            f"ADDENDUM {addendum_letter}: ADDENDUM DE CHINCHES",
            styles['AddendumTitle']
        ))
        elements.append(Paragraph("(Per Texas Property Code §92.131-§92.135)", styles['LegalRef']))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(
            "Landlord has no knowledge of any bed bug infestation on the premises. Tenant acknowledges that "
            "bed bugs may be introduced through luggage, furniture, clothing, or other personal items. "
            "Tenant agrees to:",
            styles['Body']
        ))
        bedbug_items = [
            "Inspect personal belongings, especially used furniture, before bringing them into the premises.",
            "Report any suspected bed bug activity to Landlord immediately in writing.",
            "Cooperate fully with any pest treatment program including preparation and follow-up instructions.",
            "Not introduce known infested items into the premises.",
        ]
        for b in bedbug_items:
            elements.append(Paragraph(f"• {b}", styles['BodySmall']))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            "El Arrendador no tiene conocimiento de infestación de chinches. El Arrendatario reconoce que "
            "las chinches pueden ser introducidas por equipaje, muebles, ropa u otros artículos personales. "
            "El Arrendatario acepta reportar cualquier actividad sospechosa inmediatamente por escrito y "
            "cooperar completamente con cualquier programa de tratamiento de plagas.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________",
            styles['InitialLine']
        ))
        addendum_letter = chr(ord(addendum_letter) + 1)

    # ─── ADDENDUM C: MILITARY / SCRA ────────────────────────────────
    if military_addendum:
        elements.append(Spacer(1, 12))
        elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_GRAY))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            f"ADDENDUM {addendum_letter}: MILITARY CLAUSE (SCRA) / "
            f"ADDENDUM {addendum_letter}: CLÁUSULA MILITAR (SCRA)",
            styles['AddendumTitle']
        ))
        elements.append(Paragraph(
            "(Servicemembers Civil Relief Act, 50 U.S.C. §§3911-4043)", styles['LegalRef']
        ))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(
            "If Tenant is or becomes a member of the U.S. Armed Forces on active duty and receives permanent "
            "change of station (PCS) orders, deployment orders of 90 days or more, or temporary duty orders "
            "of 90 days or more, Tenant may terminate this lease upon delivery of written notice to Landlord "
            "along with a copy of the military orders. The lease will terminate 30 days after the first date "
            "on which the next rental payment is due after the date the notice is delivered.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Si el Arrendatario es o se convierte en miembro de las Fuerzas Armadas de EE.UU. en servicio activo "
            "y recibe órdenes de cambio permanente de estación (PCS), órdenes de despliegue de 90 días o más, "
            "o órdenes de servicio temporal de 90 días o más, el Arrendatario puede terminar este contrato "
            "mediante aviso por escrito al Arrendador junto con una copia de las órdenes militares.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________",
            styles['InitialLine']
        ))
        addendum_letter = chr(ord(addendum_letter) + 1)

    # ─── ADDENDUM D: PET ADDENDUM ───────────────────────────────────
    if pets_allowed:
        elements.append(Spacer(1, 12))
        elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_GRAY))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            f"ADDENDUM {addendum_letter}: PET ADDENDUM / "
            f"ADDENDUM {addendum_letter}: ADDENDUM DE MASCOTAS",
            styles['AddendumTitle']
        ))
        elements.append(Spacer(1, 6))

        pet_deposit = pet_details.get('deposit', 250)
        pet_rent = pet_details.get('monthly_rent', 25)
        max_pets = pet_details.get('max_pets', 2)
        max_weight = pet_details.get('max_weight', 50)

        elements.append(Paragraph(
            f"Landlord grants permission for Tenant to keep up to <b>{max_pets}</b> domestic pet(s) on the premises "
            f"subject to the following conditions:",
            styles['Body']
        ))
        pet_conditions = [
            f"Non-refundable pet deposit of {format_currency(pet_deposit)} per pet.",
            f"Monthly pet rent of {format_currency(pet_rent)} per pet.",
            f"Maximum weight limit: {max_weight} lbs per animal.",
            "Prohibited breeds: Pit Bulls, Rottweilers, Dobermans, Wolf Hybrids, and any breed restricted by local ordinance or insurance policy.",
            "Tenant shall keep pets vaccinated and licensed per local requirements.",
            "Tenant shall immediately clean up after pets both inside and outside the premises.",
            "Tenant is liable for all damages caused by pets, including but not limited to carpet, flooring, landscaping, and fencing.",
            "Landlord reserves the right to revoke pet permission with 30 days' notice for violations.",
        ]
        for pc in pet_conditions:
            elements.append(Paragraph(f"• {pc}", styles['BodySmall']))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            f"El Arrendador otorga permiso para mantener hasta <b>{max_pets}</b> mascota(s) doméstica(s) sujeto a: "
            f"depósito no reembolsable de {format_currency(pet_deposit)} por mascota, renta mensual de "
            f"{format_currency(pet_rent)} por mascota, peso máximo de {max_weight} lbs por animal. "
            "El Arrendatario es responsable de todos los daños causados por las mascotas.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________",
            styles['InitialLine']
        ))
        addendum_letter = chr(ord(addendum_letter) + 1)

    # ─── ADDENDUM E: LEAD PAINT (Pre-1978) ──────────────────────────
    if lead_paint:
        elements.append(Spacer(1, 12))
        elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_GRAY))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            f"ADDENDUM {addendum_letter}: LEAD-BASED PAINT DISCLOSURE / "
            f"ADDENDUM {addendum_letter}: DIVULGACIÓN DE PINTURA A BASE DE PLOMO",
            styles['AddendumTitle']
        ))
        elements.append(Paragraph(
            "(Required by Federal Law 42 U.S.C. §4852d for housing built before 1978)",
            styles['LegalRef']
        ))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(
            "<b>WARNING:</b> Housing built before 1978 may contain lead-based paint. Lead from paint, paint chips, "
            "and dust can pose health hazards if not managed properly. Lead exposure is especially harmful to "
            "young children and pregnant women.",
            styles['Warning']
        ))
        elements.append(Paragraph(
            "Landlord's Disclosure: (check one)\n"
            "☐ Known lead-based paint and/or hazards are present.\n"
            "☑ Landlord has no knowledge of lead-based paint and/or hazards.\n"
            "☐ Landlord has provided all available records and reports.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Tenant acknowledges receipt of the EPA pamphlet 'Protect Your Family From Lead in Your Home.'",
            styles['Body']
        ))
        elements.append(Paragraph(
            "<b>ADVERTENCIA:</b> Las viviendas construidas antes de 1978 pueden contener pintura a base de plomo. "
            "El Arrendatario reconoce haber recibido el folleto de la EPA sobre protección contra el plomo.",
            styles['Body']
        ))
        elements.append(Paragraph(
            "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________",
            styles['InitialLine']
        ))
        addendum_letter = chr(ord(addendum_letter) + 1)

    # ─── ADDENDUM: PHOTO ID & CONSENT / CONSENTIMIENTO FOTOGRÁFICO ──
    elements.append(Spacer(1, 12))
    elements.append(HRFlowable(width="100%", thickness=1, color=BORDER_GRAY))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        f"ADDENDUM {addendum_letter}: PHOTOGRAPHIC IDENTIFICATION & CONSENT / "
        f"ADDENDUM {addendum_letter}: IDENTIFICACIÓN FOTOGRÁFICA Y CONSENTIMIENTO",
        styles['AddendumTitle']
    ))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "<b>1. AUTHORIZATION / AUTORIZACIÓN:</b> Tenant hereby authorizes Landlord, its employees, "
        "and authorized agents to take, store, and use photographic images of Tenant for the following "
        "purposes:",
        styles['Body']
    ))
    photo_purposes = [
        "Identity verification during lease execution, lease renewals, and key exchanges.",
        "Prevention of fraud, identity theft, and unauthorized occupancy.",
        "Maintaining a visual identification record in the tenant management system.",
        "Compliance with Landlord's internal security and property management policies.",
    ]
    for pp in photo_purposes:
        elements.append(Paragraph(f"• {pp}", styles['BodySmall']))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>1. AUTORIZACIÓN:</b> El Arrendatario autoriza al Arrendador, sus empleados y agentes "
        "autorizados a tomar, almacenar y utilizar imágenes fotográficas del Arrendatario para: "
        "verificación de identidad durante la firma del contrato, renovaciones y entrega de llaves; "
        "prevención de fraude, robo de identidad y ocupación no autorizada; mantenimiento de un "
        "registro de identificación visual en el sistema de gestión de inquilinos; y cumplimiento "
        "con las políticas internas de seguridad.",
        styles['Body']
    ))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>2. DATA PROTECTION / PROTECCIÓN DE DATOS:</b> Landlord agrees to:",
        styles['Body']
    ))
    protection_items = [
        "Store all photographic images securely using industry-standard encryption and access controls.",
        "Not share, sell, or distribute Tenant's photographs to any third party except as required by law or court order.",
        "Delete or destroy Tenant's photographs within 90 days after lease termination and completion of final move-out inspection.",
        "Limit access to Tenant's photographs to authorized property management personnel only.",
    ]
    for pi in protection_items:
        elements.append(Paragraph(f"• {pi}", styles['BodySmall']))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>2. PROTECCIÓN DE DATOS:</b> El Arrendador se compromete a almacenar las imágenes de "
        "forma segura con encriptación, no compartir ni distribuir las fotografías a terceros "
        "excepto por requerimiento legal, destruir las fotografías dentro de 90 días después "
        "de la terminación del contrato, y limitar el acceso solo al personal autorizado.",
        styles['Body']
    ))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>3. TENANT'S RIGHTS / DERECHOS DEL ARRENDATARIO:</b> Tenant has the right to: "
        "(a) request a copy of any photograph stored by Landlord; "
        "(b) request deletion of photographs upon lease termination; "
        "(c) withdraw consent at any time with 30 days' written notice, provided that withdrawal does not "
        "affect lawful use of photographs taken prior to withdrawal.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "<b>3. DERECHOS DEL ARRENDATARIO:</b> El Arrendatario tiene derecho a: (a) solicitar "
        "copia de cualquier fotografía almacenada; (b) solicitar eliminación al terminar el contrato; "
        "(c) retirar el consentimiento en cualquier momento con 30 días de aviso por escrito.",
        styles['Body']
    ))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>4. GOVERNING LAW / LEY APLICABLE:</b> This addendum is governed by applicable Texas state "
        "privacy laws and the Texas Business and Commerce Code. If any provision of this addendum is found "
        "to be unenforceable, the remaining provisions shall remain in full force and effect.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "<b>4. LEY APLICABLE:</b> Este addendum se rige por las leyes de privacidad del Estado de "
        "Texas y el Código de Comercio de Texas. Si alguna disposición es inaplicable, las demás "
        "permanecerán en pleno vigor.",
        styles['Body']
    ))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________",
        styles['InitialLine']
    ))
    addendum_letter = chr(ord(addendum_letter) + 1)

    # ═══════════════════════════════════════════════════════════════════
    # SIGNATURES / FIRMAS
    # ═══════════════════════════════════════════════════════════════════
    elements.append(PageBreak())
    elements.append(Paragraph(
        "SIGNATURES / FIRMAS",
        styles['SectionNum']
    ))
    elements.append(Paragraph(
        "By signing below, the parties acknowledge that they have read, understand, and agree to all terms, "
        "conditions, and addenda of this Residential Lease Agreement.",
        styles['Body']
    ))
    elements.append(Paragraph(
        "Al firmar a continuación, las partes reconocen que han leído, entendido y aceptan todos los términos, "
        "condiciones y addenda de este Contrato de Arrendamiento Residencial.",
        styles['Body']
    ))
    elements.append(Spacer(1, 20))

    # Handle digital signature
    sig = contract.get('signature') or contract.get('tenant_signature')
    admin_sig = contract.get('admin_signature')
    tenant_sig_cell = '_' * 40
    landlord_sig_cell = '_' * 40
    tenant_signed_date_str = '_______________'
    landlord_signed_date_str = '_______________'

    # Tenant signature
    if sig and sig.get('image_data'):
        try:
            sig_img_data = sig['image_data']
            if sig_img_data.startswith('data:'):
                sig_img_data = sig_img_data.split(',', 1)[1]
            sig_bytes = base64.b64decode(sig_img_data)
            sig_buffer = io.BytesIO(sig_bytes)
            tenant_sig_cell = RLImage(sig_buffer, width=180, height=55)

            signed_at = sig.get('signed_at', '')
            if isinstance(signed_at, str) and signed_at:
                tenant_signed_date_str = signed_at[:10]
            elif hasattr(signed_at, 'strftime'):
                tenant_signed_date_str = signed_at.strftime('%m/%d/%Y')
        except Exception as e:
            logger.warning(f"Could not process tenant signature: {e}")
            tenant_sig_cell = '_' * 40

    # Admin/Landlord signature (from contract or from saved admin signature)
    # First try to get from contract, if not found, fetch the default admin signature
    if not admin_sig or not admin_sig.get('image_data'):
        try:
            # Fetch the saved admin signature from database
            saved_admin_sig = db.admin_signatures.find_one({"type": "landlord_default"})
            if saved_admin_sig and saved_admin_sig.get('image_data'):
                admin_sig = saved_admin_sig
                logger.info("Using saved admin signature for contract PDF")
        except Exception as e:
            logger.warning(f"Could not fetch saved admin signature: {e}")
    
    if admin_sig and admin_sig.get('image_data'):
        try:
            admin_img_data = admin_sig['image_data']
            if admin_img_data.startswith('data:'):
                admin_img_data = admin_img_data.split(',', 1)[1]
            admin_bytes = base64.b64decode(admin_img_data)
            admin_buffer = io.BytesIO(admin_bytes)
            landlord_sig_cell = RLImage(admin_buffer, width=180, height=55)

            admin_signed_at = admin_sig.get('signed_at') or admin_sig.get('updated_at', '')
            if isinstance(admin_signed_at, str) and admin_signed_at:
                landlord_signed_date_str = admin_signed_at[:10]
            elif hasattr(admin_signed_at, 'strftime'):
                landlord_signed_date_str = admin_signed_at.strftime('%m/%d/%Y')
            else:
                landlord_signed_date_str = tenant_signed_date_str  # Use tenant date if admin date not available
        except Exception as e:
            logger.warning(f"Could not process admin signature: {e}")
            landlord_sig_cell = '_' * 40

    sig_data = [
        [tenant_sig_cell, '', landlord_sig_cell],
        ['Tenant Signature / Firma del Arrendatario', '', 'Landlord Signature / Firma del Arrendador'],
        [contract.get('tenant_name', ''), '', co['name']],
        [f'Date / Fecha: {tenant_signed_date_str}', '', f'Date / Fecha: {landlord_signed_date_str}'],
    ]
    t = Table(sig_data, colWidths=[200, 70, 200])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(t)

    # ─── DIGITAL SIGNATURE VERIFICATION ──────────────────────────────
    if sig and sig.get('hash'):
        elements.append(Spacer(1, 16))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(
            "<b>Digital Signature Verification / Verificación de Firma Digital</b>",
            styles['BodySmall']
        ))
        elements.append(Paragraph(
            f"SHA-256 Hash: {sig.get('hash', 'N/A')}", styles['Footer']
        ))
        elements.append(Paragraph(
            f"Signed by / Firmado por: {sig.get('signer_name', 'N/A')} | "
            f"IP: {sig.get('client_ip', 'N/A')} | "
            f"Method / Método: {sig.get('type', 'canvas')} | "
            f"Administered by / Administrado por: {sig.get('signed_by_admin', 'N/A')}",
            styles['Footer']
        ))

    # ─── FOOTER ──────────────────────────────────────────────────────
    elements.append(Spacer(1, 24))
    elements.append(HRFlowable(width="100%", thickness=1, color=BLUE))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"Document generated electronically on {datetime.utcnow().strftime('%m/%d/%Y %H:%M')} UTC — {co['name']}",
        styles['Footer']
    ))
    elements.append(Paragraph(
        f"{co['address']} | {co['phone']} | {co['email']} | {co.get('website', '')}",
        styles['Footer']
    ))
    elements.append(Paragraph(
        "This document is legally binding. Governed by Texas Property Code Chapter 92.",
        styles['Footer']
    ))

    # Build PDF
    try:
        doc.build(elements)
    except Exception as e:
        logger.error(f"PDF build error: {e}")
        raise

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return base64.b64encode(pdf_bytes).decode('utf-8')


# ═══════════════════════════════════════════════════════════════════════════
# 3-DAY NOTICE TO VACATE GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_3day_notice_pdf(contract: dict, config: dict = None, reason: str = 'nonpayment', amount_owed: float = 0) -> str:
    """Generate a Texas 3-Day Notice to Vacate (TX Property Code §24.005)"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch
    )
    styles = _build_styles()
    elements = []

    co = {**DEFAULT_COMPANY}
    if config:
        for k in ['name', 'address', 'phone', 'email', 'website', 'state', 'county']:
            if config.get(k):
                co[k] = config[k]

    # Header
    logo_path = _get_logo_path()
    if logo_path:
        try:
            # Ross House Rentals logo — correct 2.29:1 aspect ratio
            logo = RLImage(logo_path, width=2 * inch, height=0.87 * inch)
            logo.hAlign = 'CENTER'
            elements.append(logo)
        except Exception:
            pass

    elements.append(Spacer(1, 4))
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=2, color=RED))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        "THREE-DAY NOTICE TO VACATE / AVISO DE TRES DÍAS PARA DESALOJAR",
        ParagraphStyle('NoticeTitle', fontName='Helvetica-Bold', fontSize=14,
                      textColor=RED, alignment=TA_CENTER, spaceAfter=8)
    ))
    elements.append(Paragraph(
        "(Texas Property Code §24.005)",
        styles['LegalRef']
    ))
    elements.append(Spacer(1, 12))

    # Notice content
    today = datetime.utcnow().strftime('%B %d, %Y')
    elements.append(Paragraph(f"<b>Date / Fecha:</b> {today}", styles['Body']))
    elements.append(Paragraph(f"<b>To / Para:</b> {contract.get('tenant_name', 'TENANT')}", styles['Body']))
    elements.append(Paragraph(f"<b>Property / Propiedad:</b> {contract.get('property_address', '')}", styles['Body']))
    elements.append(Spacer(1, 8))

    if reason == 'nonpayment':
        elements.append(Paragraph(
            f"You are hereby notified that you are in default of your lease for nonpayment of rent. "
            f"The total amount owed is <b>{format_currency(amount_owed)}</b>. You are required to either:",
            styles['Body']
        ))
        elements.append(Paragraph(
            f"Por la presente se le notifica que está en incumplimiento de su contrato por falta de pago de renta. "
            f"El monto total adeudado es <b>{format_currency(amount_owed)}</b>. Se le requiere:",
            styles['Body']
        ))
    else:
        elements.append(Paragraph(
            "You are hereby notified that your right to occupancy of the above-referenced property "
            "has been terminated.",
            styles['Body']
        ))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "<b>1.</b> Pay the full amount owed within THREE (3) DAYS from the date of this notice; OR",
        styles['Body']
    ))
    elements.append(Paragraph(
        "<b>2.</b> Vacate the premises within THREE (3) DAYS from the date of this notice.",
        styles['Body']
    ))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "<b>1.</b> Pagar el monto total adeudado dentro de TRES (3) DÍAS a partir de la fecha de este aviso; O",
        styles['Body']
    ))
    elements.append(Paragraph(
        "<b>2.</b> Desalojar las instalaciones dentro de TRES (3) DÍAS a partir de la fecha de este aviso.",
        styles['Body']
    ))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(
        "If you fail to comply, legal proceedings may be initiated against you for eviction and recovery "
        "of all amounts owed, including attorney's fees and court costs as permitted by law.",
        styles['Warning']
    ))
    elements.append(Paragraph(
        "Si no cumple, se podrán iniciar procedimientos legales en su contra por desalojo y recuperación "
        "de todos los montos adeudados, incluyendo honorarios de abogado y costos judiciales.",
        styles['Warning']
    ))

    # Landlord signature
    elements.append(Spacer(1, 30))
    sig_data = [
        ['_' * 40, '', '_' * 30],
        ['Landlord / Arrendador', '', 'Date / Fecha'],
        [co['name'], '', today],
    ]
    t = Table(sig_data, colWidths=[220, 60, 190])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)

    elements.append(Spacer(1, 20))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
    elements.append(Paragraph(
        f"Generated by {co['name']} — {co['address']} | {co['phone']}",
        styles['Footer']
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return base64.b64encode(pdf_bytes).decode('utf-8')



# ═══════════════════════════════════════════════════════════════════════
# RENT PAYMENT RECEIPT PDF
# ═══════════════════════════════════════════════════════════════════════

def generate_rental_receipt_pdf(payment: dict, contract: dict = None, tenant: dict = None, config: dict = None):
    """
    Generate a professional PDF receipt for a rental payment.
    Returns base64-encoded PDF string.
    """
    co = {**DEFAULT_COMPANY, **(config or {})}
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('ReceiptTitle', parent=styles['Title'], fontSize=22, textColor=NAVY, spaceAfter=4, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle('ReceiptSubtitle', parent=styles['Normal'], fontSize=10, textColor=GRAY, alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle('SectionHead', parent=styles['Normal'], fontSize=12, textColor=NAVY, fontName='Helvetica-Bold', spaceBefore=14, spaceAfter=6))
    styles.add(ParagraphStyle('FieldLabel', parent=styles['Normal'], fontSize=9, textColor=MUTED_GRAY))
    styles.add(ParagraphStyle('FieldValue', parent=styles['Normal'], fontSize=10, textColor=DARK_GRAY, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle('SmallText', parent=styles['Normal'], fontSize=8, textColor=MUTED_GRAY, alignment=TA_CENTER))
    styles.add(ParagraphStyle('BigAmount', parent=styles['Normal'], fontSize=28, textColor=BRAND_RED, fontName='Helvetica-Bold', alignment=TA_CENTER))
    styles.add(ParagraphStyle('PaidBadge', parent=styles['Normal'], fontSize=14, textColor=GREEN, fontName='Helvetica-Bold', alignment=TA_CENTER))

    elements = []

    # ─── HEADER with Logo ─────────────────────────────────────
    logo_path = _get_logo_path()
    header_items = []
    if logo_path:
        try:
            logo = RLImage(logo_path, width=1.6 * inch, height=0.6 * inch, kind='proportional')
            header_items.append(logo)
        except Exception:
            header_items.append(Paragraph(f"<b>{co['name']}</b>", styles['ReceiptTitle']))
    else:
        header_items.append(Paragraph(f"<b>{co['name']}</b>", styles['ReceiptTitle']))

    receipt_number = payment.get('receipt_number', 'N/A')
    payment_date_raw = payment.get('payment_date', '')
    try:
        if isinstance(payment_date_raw, datetime):
            payment_date = payment_date_raw.strftime('%m/%d/%Y')
        else:
            payment_date = datetime.fromisoformat(str(payment_date_raw).replace(' ', 'T').split('.')[0]).strftime('%m/%d/%Y')
    except Exception:
        payment_date = str(payment_date_raw)[:10]

    # Header table: logo left, receipt info right
    header_right = f"""
    <b>PAYMENT RECEIPT / RECIBO DE PAGO</b><br/>
    <font size="9" color="#718096">Receipt # / Recibo #: <b>{receipt_number}</b></font><br/>
    <font size="9" color="#718096">Date / Fecha: <b>{payment_date}</b></font>
    """
    header_table = Table(
        [[header_items[0] if header_items else '', Paragraph(header_right, styles['Normal'])]],
        colWidths=[3 * inch, 4 * inch]
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6))
    elements.append(HRFlowable(width="100%", thickness=2, color=BRAND_RED))
    elements.append(Spacer(1, 14))

    # ─── PAID STATUS BADGE ─────────────────────────────────────
    status = payment.get('status', 'completed')
    if status == 'completed':
        elements.append(Paragraph("✓ PAID / PAGADO", styles['PaidBadge']))
    else:
        elements.append(Paragraph(f"Status: {status.upper()}", styles['PaidBadge']))
    elements.append(Spacer(1, 10))

    # ─── TOTAL AMOUNT ──────────────────────────────────────────
    total_paid = payment.get('total_paid', payment.get('amount', 0))
    elements.append(Paragraph(f"${total_paid:,.2f}", styles['BigAmount']))
    elements.append(Paragraph("Total Paid / Total Pagado", styles['SmallText']))
    elements.append(Spacer(1, 16))

    # ─── TENANT & PROPERTY INFO ────────────────────────────────
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
    elements.append(Spacer(1, 8))

    tenant_name = tenant.get('name', 'N/A') if tenant else payment.get('tenant_name', 'N/A')
    tenant_num = tenant.get('tenant_number', '') if tenant else ''
    property_addr = contract.get('property_address', 'N/A') if contract else payment.get('property_address', 'N/A')
    contract_num = contract.get('contract_number', 'N/A') if contract else 'N/A'

    info_data = [
        [
            Paragraph("<b>Tenant / Inquilino</b>", styles['FieldLabel']),
            Paragraph("<b>Property / Propiedad</b>", styles['FieldLabel']),
        ],
        [
            Paragraph(f"{tenant_name}<br/><font size='8' color='#718096'>{tenant_num}</font>", styles['FieldValue']),
            Paragraph(f"{property_addr}<br/><font size='8' color='#718096'>Contract / Contrato: {contract_num}</font>", styles['FieldValue']),
        ],
    ]
    info_table = Table(info_data, colWidths=[3.4 * inch, 3.4 * inch])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 14))

    # ─── PAYMENT DETAILS TABLE ─────────────────────────────────
    elements.append(Paragraph("Payment Details / Detalles del Pago", styles['SectionHead']))

    period_month = payment.get('period_month', '').capitalize()
    period_year = payment.get('period_year', '')
    amount = payment.get('amount', 0)
    late_fee = payment.get('late_fee', 0)
    payment_method = payment.get('payment_method', 'N/A').capitalize()

    detail_rows = [
        [
            Paragraph("<b>Description / Descripción</b>", styles['FieldLabel']),
            Paragraph("<b>Amount / Monto</b>", styles['FieldLabel']),
        ],
        [
            Paragraph(f"Rent / Renta — {period_month} {period_year}", styles['Normal']),
            Paragraph(f"${amount:,.2f}", styles['FieldValue']),
        ],
    ]
    if late_fee > 0:
        detail_rows.append([
            Paragraph("Late Fee / Cargo por Atraso", styles['Normal']),
            Paragraph(f"${late_fee:,.2f}", styles['FieldValue']),
        ])
    detail_rows.append([
        Paragraph("<b>TOTAL</b>", styles['FieldValue']),
        Paragraph(f"<b>${total_paid:,.2f}</b>", ParagraphStyle('TotalRight', parent=styles['FieldValue'], textColor=BRAND_RED)),
    ])

    detail_table = Table(detail_rows, colWidths=[4.8 * inch, 2 * inch])
    detail_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), LIGHT_BLUE),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, 0), 1, NAVY),
        ('LINEBELOW', (0, -2), (-1, -2), 0.5, BORDER_GRAY),
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, NAVY),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f4ff')),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 12))

    # ─── PAYMENT METHOD ────────────────────────────────────────
    method_data = [
        [
            Paragraph("<b>Payment Method / Método de Pago:</b>", styles['FieldLabel']),
            Paragraph(payment_method, styles['FieldValue']),
            Paragraph("<b>Date / Fecha:</b>", styles['FieldLabel']),
            Paragraph(payment_date, styles['FieldValue']),
        ],
    ]
    method_table = Table(method_data, colWidths=[1.8 * inch, 1.6 * inch, 1.4 * inch, 2 * inch])
    method_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
    ]))
    elements.append(method_table)
    elements.append(Spacer(1, 24))

    # ─── FOOTER ────────────────────────────────────────────────
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "This receipt confirms payment has been received. / Este recibo confirma que el pago ha sido recibido.",
        ParagraphStyle('ThankYou', parent=styles['Normal'], fontSize=9, textColor=GRAY, alignment=TA_CENTER)
    ))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"<b>{co['name']}</b> — {co['address']} | {co['phone']} | {co['email']}",
        styles['SmallText']
    ))
    elements.append(Spacer(1, 2))
    elements.append(Paragraph(
        f"Generated / Generado: {datetime.utcnow().strftime('%m/%d/%Y %I:%M %p')} UTC",
        styles['SmallText']
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return base64.b64encode(pdf_bytes).decode('utf-8')
