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


def _get_logo_path(variant: str = "light"):
    """Find Ross House Rentals logo in assets folder.

    `variant`:
      - "light" (default): original logo with black 'ROSS HOUSE' text — use
        on white/light backgrounds.
      - "dark": white-text version — use on dark/black backgrounds (PDF
        headers, branded banners).
    """
    base = os.path.dirname(os.path.abspath(__file__))
    if variant == "dark":
        candidates = ['ross_house_logo_dark.png', 'ross_house_logo_white.png',
                      'ross_house_logo.png', 'company_logo.png']
    else:
        candidates = ['ross_house_logo.png', 'company_logo.png', 'ross_logo.png']
    for name in candidates:
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


def _get_initials(name: str) -> str:
    """Extract initials from a full name (e.g., 'John Smith' -> 'J.S.')"""
    if not name:
        return "____"
    parts = name.strip().split()
    if len(parts) == 0:
        return "____"
    initials = '.'.join([p[0].upper() for p in parts if p]) + '.'
    return initials if initials != '.' else "____"


def _format_signature_date(date_val) -> str:
    """Format signature date from various input formats"""
    if not date_val:
        return "____________"
    try:
        if isinstance(date_val, str):
            # Handle ISO format strings
            if 'T' in date_val:
                date_val = date_val.split('T')[0]
            # Try to parse and reformat
            from datetime import datetime as dt
            if '-' in date_val:
                parsed = dt.strptime(date_val[:10], '%Y-%m-%d')
            elif '/' in date_val:
                parsed = dt.strptime(date_val, '%m/%d/%Y')
            else:
                return date_val[:10]
            return parsed.strftime('%m/%d/%Y')
        elif hasattr(date_val, 'strftime'):
            return date_val.strftime('%m/%d/%Y')
        else:
            return str(date_val)[:10]
    except Exception:
        return str(date_val)[:10] if date_val else "____________"


def _build_initials_line(tenant_name: str, signature_data: dict, styles) -> str:
    """
    Build the initials line with real data if signature exists.
    Returns a formatted Paragraph-compatible string.
    """
    initials = _get_initials(tenant_name)
    
    # Get signature date from various possible fields
    sig_date = None
    if signature_data:
        sig_date = (signature_data.get('signed_at') or 
                    signature_data.get('tenant_signed_at') or 
                    signature_data.get('admin_signed_at') or
                    signature_data.get('updated_at'))
    
    date_str = _format_signature_date(sig_date) if sig_date else datetime.utcnow().strftime('%m/%d/%Y')
    
    # If contract is signed, show actual initials and date
    if signature_data and (signature_data.get('image_data') or signature_data.get('signed_at')):
        return f"Tenant Initials / Iniciales del Arrendatario: <b>{initials}</b>    Date / Fecha: <b>{date_str}</b>"
    else:
        return "Tenant Initials / Iniciales del Arrendatario: ________    Date / Fecha: ____________"


def _build_dual_initials_block(tenant_name: str, tenant_sig: dict, admin_sig: dict, landlord_name: str = "Ross House Rentals LLC"):
    """
    Build a complete initials block for addendums with BOTH tenant and landlord/admin initials.
    Returns a list of formatted strings for multiple Paragraph elements.
    
    For a signed contract, this will show:
    - Tenant Initials: J.S.    Date: 02/15/2026
    - Landlord Initials: R.H.  Date: 02/15/2026
    
    For unsigned contract, shows blank lines: ________
    """
    tenant_initials = _get_initials(tenant_name)
    landlord_initials = _get_initials(landlord_name)
    
    # Tenant signature date
    tenant_date = "____________"
    if tenant_sig:
        t_date = (tenant_sig.get('signed_at') or tenant_sig.get('tenant_signed_at') or tenant_sig.get('updated_at'))
        if t_date:
            tenant_date = f"<b>{_format_signature_date(t_date)}</b>"
        if tenant_sig.get('image_data') or tenant_sig.get('signed_at'):
            tenant_initials = f"<b>{tenant_initials}</b>"
        else:
            tenant_initials = "________"
            tenant_date = "____________"
    else:
        tenant_initials = "________"
    
    # Admin/Landlord signature date
    landlord_date = "____________"
    if admin_sig:
        a_date = (admin_sig.get('signed_at') or admin_sig.get('admin_signed_at') or admin_sig.get('updated_at'))
        if a_date:
            landlord_date = f"<b>{_format_signature_date(a_date)}</b>"
        if admin_sig.get('image_data') or admin_sig.get('signed_at'):
            landlord_initials = f"<b>{landlord_initials}</b>"
        else:
            landlord_initials = "________"
            landlord_date = "____________"
    else:
        landlord_initials = "________"
    
    lines = [
        f"Tenant Initials / Iniciales del Arrendatario: {tenant_initials}    Date / Fecha: {tenant_date}",
        f"Landlord Initials / Iniciales del Arrendador: {landlord_initials}    Date / Fecha: {landlord_date}",
    ]
    return lines


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

    # ─── EXTRACT SIGNATURE DATA FOR INITIALS ─────────────────────────
    tenant_name = contract.get('tenant_name', '')
    tenant_sig = contract.get('signature') or contract.get('tenant_signature') or {}
    
    # Get admin signature from contract or from saved signature in config
    admin_sig = contract.get('admin_signature') or {}
    if not admin_sig.get('image_data') and config:
        admin_sig = config.get('saved_admin_signature') or {}
    
    # Get landlord/company name for initials
    landlord_name = co.get('name', 'Ross House Rentals LLC')
    
    # Build dual initials block (both tenant AND landlord) for addendums
    def add_initials_block():
        """Helper to add dual initials block to elements"""
        initials_lines = _build_dual_initials_block(tenant_name, tenant_sig, admin_sig, landlord_name)
        for line in initials_lines:
            elements.append(Paragraph(line, styles['InitialLine']))

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
        elements.append(Spacer(1, 6))
        add_initials_block()
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
        elements.append(Spacer(1, 6))
        add_initials_block()
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
        elements.append(Spacer(1, 6))
        add_initials_block()
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
        elements.append(Spacer(1, 6))
        add_initials_block()
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
        elements.append(Spacer(1, 6))
        add_initials_block()
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
    add_initials_block()
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

    # Admin/Landlord signature (from contract or from saved admin signature in config)
    # First try to get from contract, if not found, use the saved_admin_signature from config
    if not admin_sig or not admin_sig.get('image_data'):
        # Check if saved admin signature was passed via config
        saved_admin_sig = config.get('saved_admin_signature') if config else None
        if saved_admin_sig and saved_admin_sig.get('image_data'):
            admin_sig = saved_admin_sig
            logger.info("Using saved admin signature from config for contract PDF")
    
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
    """Generate a Texas 3-Day Notice to Vacate (TX Property Code §24.005) — premium design."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.45 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        title="Aviso de 3 Días — Three-Day Notice to Vacate",
    )
    base_styles = getSampleStyleSheet()
    elements = []

    co = {**DEFAULT_COMPANY}
    if config:
        for k in ['name', 'address', 'phone', 'email', 'website', 'state', 'county']:
            if config.get(k):
                co[k] = config[k]

    # ─── Brand styles ─────────────────────────────────────────
    S = {}
    S['hero_title'] = ParagraphStyle('hero_title', parent=base_styles['Normal'],
        fontSize=20, leading=24, textColor=colors.white,
        fontName='Helvetica-Bold', alignment=TA_LEFT)
    S['hero_sub'] = ParagraphStyle('hero_sub', parent=base_styles['Normal'],
        fontSize=9, leading=12, textColor=colors.HexColor('#FFD1D1'),
        fontName='Helvetica', alignment=TA_LEFT)
    S['legal_ref'] = ParagraphStyle('legal_ref', parent=base_styles['Normal'],
        fontSize=9, leading=12, textColor=GRAY,
        fontName='Helvetica-Oblique', alignment=TA_CENTER, spaceAfter=8)
    S['notice_title'] = ParagraphStyle('notice_title', parent=base_styles['Normal'],
        fontSize=16, leading=20, textColor=BRAND_RED,
        fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
    S['section'] = ParagraphStyle('section', parent=base_styles['Normal'],
        fontSize=8, leading=11, textColor=MUTED_GRAY,
        fontName='Helvetica-Bold', spaceAfter=4)
    S['lbl'] = ParagraphStyle('lbl', parent=base_styles['Normal'],
        fontSize=8, leading=11, textColor=MUTED_GRAY,
        fontName='Helvetica')
    S['val'] = ParagraphStyle('val', parent=base_styles['Normal'],
        fontSize=11, leading=15, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold')
    S['body'] = ParagraphStyle('body', parent=base_styles['Normal'],
        fontSize=10.5, leading=15, textColor=BRAND_CHARCOAL,
        fontName='Helvetica', alignment=TA_JUSTIFY, spaceAfter=6)
    S['body_es'] = ParagraphStyle('body_es', parent=base_styles['Normal'],
        fontSize=10.5, leading=15, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Oblique', alignment=TA_JUSTIFY, spaceAfter=6)
    S['warning_box'] = ParagraphStyle('warning_box', parent=base_styles['Normal'],
        fontSize=10.5, leading=14, textColor=colors.HexColor('#92400E'),
        fontName='Helvetica-Bold', alignment=TA_JUSTIFY)
    S['amount_big'] = ParagraphStyle('amount_big', parent=base_styles['Normal'],
        fontSize=22, leading=26, textColor=BRAND_RED,
        fontName='Helvetica-Bold', alignment=TA_CENTER)
    S['action_label'] = ParagraphStyle('action_label', parent=base_styles['Normal'],
        fontSize=11, leading=14, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold', alignment=TA_LEFT, spaceAfter=2)
    S['action_body'] = ParagraphStyle('action_body', parent=base_styles['Normal'],
        fontSize=10, leading=13, textColor=BRAND_CHARCOAL,
        fontName='Helvetica', alignment=TA_LEFT, spaceAfter=0)
    S['footer'] = ParagraphStyle('footer', parent=base_styles['Normal'],
        fontSize=7.5, leading=10, textColor=MUTED_GRAY,
        fontName='Helvetica', alignment=TA_CENTER)
    S['sig_label'] = ParagraphStyle('sig_label', parent=base_styles['Normal'],
        fontSize=8.5, leading=11, textColor=MUTED_GRAY,
        fontName='Helvetica')
    S['sig_val'] = ParagraphStyle('sig_val', parent=base_styles['Normal'],
        fontSize=10, leading=13, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold')

    # ─── HERO HEADER (logo + title on black) ─────────────────
    logo_path = _get_logo_path("dark")
    if logo_path:
        try:
            logo = RLImage(logo_path, width=1.1 * inch, height=1.1 * inch, kind='proportional')
        except Exception:
            logo = Paragraph(co['name'], S['hero_title'])
    else:
        logo = Paragraph(co['name'], S['hero_title'])

    title_block = [
        Paragraph("AVISO LEGAL", S['hero_title']),
        Spacer(1, 2),
        Paragraph("Notice to Vacate  •  TX Property Code §24.005", S['hero_sub']),
    ]
    hero = Table(
        [[logo, title_block]],
        colWidths=[1.25 * inch, 6.05 * inch],
        rowHeights=[1.0 * inch],
    )
    hero.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_CHARCOAL),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    elements.append(hero)
    elements.append(HRFlowable(width="100%", thickness=4, color=BRAND_RED, spaceBefore=0, spaceAfter=0))
    elements.append(Spacer(1, 14))

    # ─── Notice title ─────────────────────────────────────────
    elements.append(Paragraph("AVISO DE TRES (3) DÍAS PARA DESALOJAR", S['notice_title']))
    elements.append(Paragraph("Three-Day Notice to Vacate", S['notice_title']))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "Bajo el Código de Propiedad de Texas, Sección §24.005  •  Under Texas Property Code §24.005",
        S['legal_ref']
    ))
    elements.append(Spacer(1, 8))

    # ─── Recipient info card ──────────────────────────────────
    today_es = datetime.utcnow().strftime('%d de %B de %Y')
    today_es_map = {
        'January':'Enero','February':'Febrero','March':'Marzo','April':'Abril',
        'May':'Mayo','June':'Junio','July':'Julio','August':'Agosto',
        'September':'Septiembre','October':'Octubre','November':'Noviembre','December':'Diciembre'
    }
    for en, es in today_es_map.items():
        today_es = today_es.replace(en, es)
    today_en = datetime.utcnow().strftime('%B %d, %Y')

    info_rows = [
        [Paragraph("FECHA  •  DATE", S['lbl']), Paragraph(f"{today_es}  /  {today_en}", S['val'])],
        [Paragraph("DESTINATARIO  •  TO", S['lbl']), Paragraph(contract.get('tenant_name', 'INQUILINO / TENANT'), S['val'])],
        [Paragraph("PROPIEDAD  •  PROPERTY", S['lbl']), Paragraph(contract.get('property_address', '—'), S['val'])],
    ]
    if contract.get('contract_number'):
        info_rows.append([
            Paragraph("CONTRATO  •  LEASE N.°", S['lbl']),
            Paragraph(contract['contract_number'], S['val'])
        ])

    info_card = Table(info_rows, colWidths=[1.6 * inch, 5.7 * inch])
    info_card.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9FAFB')),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, BORDER_GRAY),
    ]))
    elements.append(info_card)
    elements.append(Spacer(1, 16))

    # ─── Amount owed box (only for nonpayment) ────────────────
    if reason == 'nonpayment' and amount_owed > 0:
        amount_box = Table(
            [
                [Paragraph("MONTO TOTAL ADEUDADO  •  TOTAL AMOUNT OWED", S['section'])],
                [Paragraph(f"${amount_owed:,.2f}", S['amount_big'])],
            ],
            colWidths=[7.3 * inch],
        )
        amount_box.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF5F7')),
            ('BOX', (0, 0), (-1, -1), 2, BRAND_RED),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(amount_box)
        elements.append(Spacer(1, 14))

    # ─── Notice body ──────────────────────────────────────────
    if reason == 'nonpayment':
        elements.append(Paragraph(
            "Por la presente se le <b>notifica formalmente</b> que se encuentra en incumplimiento "
            "de su contrato de arrendamiento por <b>falta de pago de renta</b>. "
            "Se le requiere tomar una de las siguientes acciones:",
            S['body_es']
        ))
        elements.append(Paragraph(
            "<i>You are hereby formally notified that you are in default of your lease "
            "agreement for <b>nonpayment of rent</b>. You are required to take one of the "
            "following actions:</i>",
            S['body']
        ))
    else:
        elements.append(Paragraph(
            "Por la presente se le <b>notifica formalmente</b> que su derecho de ocupación "
            "de la propiedad arriba mencionada ha sido <b>terminado</b>.",
            S['body_es']
        ))
        elements.append(Paragraph(
            "<i>You are hereby formally notified that your right to occupy the above-referenced "
            "property has been <b>terminated</b>.</i>",
            S['body']
        ))

    elements.append(Spacer(1, 8))

    # ─── Actions (numbered cards) ─────────────────────────────
    action_1_body = []
    if reason == 'nonpayment':
        action_1_body = [
            Paragraph("OPCIÓN 1  •  OPTION 1", S['section']),
            Paragraph("Pagar el monto total adeudado", S['action_label']),
            Paragraph("Pay the full amount owed within THREE (3) DAYS from the date of this notice.", S['action_body']),
            Paragraph("Pagar dentro de TRES (3) DÍAS a partir de la fecha de este aviso.", S['action_body']),
        ]
    action_2_body = [
        Paragraph(("OPCIÓN 2  •  OPTION 2" if reason == 'nonpayment' else "ACCIÓN REQUERIDA  •  REQUIRED ACTION"), S['section']),
        Paragraph("Desalojar la propiedad", S['action_label']),
        Paragraph("Vacate the premises within THREE (3) DAYS from the date of this notice.", S['action_body']),
        Paragraph("Desalojar las instalaciones dentro de TRES (3) DÍAS a partir de la fecha de este aviso.", S['action_body']),
    ]

    if action_1_body:
        actions = Table(
            [[action_1_body, '', action_2_body]],
            colWidths=[3.45 * inch, 0.4 * inch, 3.45 * inch],
        )
        actions.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#F9FAFB')),
            ('BACKGROUND', (2, 0), (2, 0), colors.HexColor('#F9FAFB')),
            ('BOX', (0, 0), (0, 0), 0.5, BORDER_GRAY),
            ('BOX', (2, 0), (2, 0), 0.5, BORDER_GRAY),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ]))
    else:
        actions = Table([[action_2_body]], colWidths=[7.3 * inch])
        actions.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9FAFB')),
            ('BOX', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('LEFTPADDING', (0, 0), (-1, -1), 14),
            ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ]))
    elements.append(actions)
    elements.append(Spacer(1, 16))

    # ─── Warning box ──────────────────────────────────────────
    warning_box = Table(
        [[Paragraph(
            "<b>⚠️ ADVERTENCIA LEGAL  •  LEGAL WARNING</b><br/><br/>"
            "Si no cumple con este aviso, se podrán iniciar procedimientos legales en su contra "
            "por <b>desalojo</b> y recuperación de todos los montos adeudados, incluyendo "
            "honorarios de abogado y costos judiciales según lo permite la ley de Texas.<br/><br/>"
            "<i>If you fail to comply with this notice, legal proceedings may be initiated against you "
            "for <b>eviction</b> and recovery of all amounts owed, including attorney's fees and court "
            "costs as permitted by Texas law.</i>",
            S['warning_box']
        )]],
        colWidths=[7.3 * inch],
    )
    warning_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FEF3C7')),
        ('LINEABOVE', (0, 0), (-1, 0), 2, colors.HexColor('#F59E0B')),
        ('LINEBELOW', (0, 0), (-1, 0), 2, colors.HexColor('#F59E0B')),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
    ]))
    elements.append(warning_box)
    elements.append(Spacer(1, 28))

    # ─── Signature block ──────────────────────────────────────
    sig = Table(
        [
            [Paragraph('_' * 38, S['sig_label']), '', Paragraph('_' * 24, S['sig_label'])],
            [Paragraph("Arrendador  •  Landlord", S['sig_label']), '', Paragraph("Fecha  •  Date", S['sig_label'])],
            [Paragraph(co['name'], S['sig_val']), '', Paragraph(today_en, S['sig_val'])],
        ],
        colWidths=[3.5 * inch, 0.6 * inch, 3.2 * inch],
    )
    sig.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(sig)
    elements.append(Spacer(1, 20))

    # ─── Footer ───────────────────────────────────────────────
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        f"<b>{co['name']}</b>  •  {co['address']}  •  {co['phone']}  •  {co['email']}",
        S['footer']
    ))
    elements.append(Paragraph(
        f"Documento generado el {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC",
        S['footer']
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
    Generate a premium, modern PDF receipt for a rental payment.
    Bilingual ES/EN with Ross House Rentals branding.
    Returns base64-encoded PDF string.
    """
    from reportlab.platypus.flowables import Flowable
    co = {**DEFAULT_COMPANY, **(config or {})}
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.45 * inch, bottomMargin=0.45 * inch,
        title=f"Recibo de Renta — {payment.get('receipt_number','')}",
        author=co['name'],
    )

    # ─── Styles ───────────────────────────────────────────────
    base_styles = getSampleStyleSheet()
    S = {}
    S['hero_title'] = ParagraphStyle('hero_title', parent=base_styles['Normal'],
        fontSize=24, leading=28, textColor=colors.white,
        fontName='Helvetica-Bold', alignment=TA_LEFT, spaceAfter=0)
    S['hero_sub'] = ParagraphStyle('hero_sub', parent=base_styles['Normal'],
        fontSize=10, leading=14, textColor=colors.HexColor('#FFD1D1'),
        fontName='Helvetica', alignment=TA_LEFT, spaceAfter=0)
    S['hero_meta'] = ParagraphStyle('hero_meta', parent=base_styles['Normal'],
        fontSize=8.5, leading=12, textColor=colors.white,
        fontName='Helvetica', alignment=TA_RIGHT)
    S['hero_meta_b'] = ParagraphStyle('hero_meta_b', parent=base_styles['Normal'],
        fontSize=10, leading=13, textColor=colors.white,
        fontName='Helvetica-Bold', alignment=TA_RIGHT)
    S['amount_big'] = ParagraphStyle('amount_big', parent=base_styles['Normal'],
        fontSize=42, leading=46, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=2)
    S['amount_lbl'] = ParagraphStyle('amount_lbl', parent=base_styles['Normal'],
        fontSize=9, leading=12, textColor=MUTED_GRAY,
        fontName='Helvetica', alignment=TA_CENTER, spaceAfter=0,
        kerning=2)
    S['status_paid'] = ParagraphStyle('status_paid', parent=base_styles['Normal'],
        fontSize=10, leading=14, textColor=colors.white,
        fontName='Helvetica-Bold', alignment=TA_CENTER)
    S['section'] = ParagraphStyle('section', parent=base_styles['Normal'],
        fontSize=8, leading=11, textColor=MUTED_GRAY,
        fontName='Helvetica-Bold', alignment=TA_LEFT, spaceAfter=4)
    S['lbl'] = ParagraphStyle('lbl', parent=base_styles['Normal'],
        fontSize=7.5, leading=10, textColor=MUTED_GRAY,
        fontName='Helvetica', spaceAfter=2)
    S['val'] = ParagraphStyle('val', parent=base_styles['Normal'],
        fontSize=10.5, leading=14, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold')
    S['val_small'] = ParagraphStyle('val_small', parent=base_styles['Normal'],
        fontSize=8.5, leading=12, textColor=GRAY,
        fontName='Helvetica')
    S['table_th'] = ParagraphStyle('table_th', parent=base_styles['Normal'],
        fontSize=8.5, leading=11, textColor=colors.white,
        fontName='Helvetica-Bold')
    S['table_td'] = ParagraphStyle('table_td', parent=base_styles['Normal'],
        fontSize=10, leading=14, textColor=BRAND_CHARCOAL,
        fontName='Helvetica')
    S['table_td_amount'] = ParagraphStyle('table_td_amount', parent=base_styles['Normal'],
        fontSize=10, leading=14, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold', alignment=TA_RIGHT)
    S['total_label'] = ParagraphStyle('total_label', parent=base_styles['Normal'],
        fontSize=11, leading=15, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold', alignment=TA_LEFT)
    S['total_amount'] = ParagraphStyle('total_amount', parent=base_styles['Normal'],
        fontSize=16, leading=20, textColor=BRAND_RED,
        fontName='Helvetica-Bold', alignment=TA_RIGHT)
    S['footer'] = ParagraphStyle('footer', parent=base_styles['Normal'],
        fontSize=7.5, leading=11, textColor=MUTED_GRAY,
        fontName='Helvetica', alignment=TA_CENTER)
    S['footer_bold'] = ParagraphStyle('footer_bold', parent=base_styles['Normal'],
        fontSize=8.5, leading=12, textColor=BRAND_CHARCOAL,
        fontName='Helvetica-Bold', alignment=TA_CENTER)
    S['stamp'] = ParagraphStyle('stamp', parent=base_styles['Normal'],
        fontSize=28, leading=34, textColor=colors.HexColor('#10B981'),
        fontName='Helvetica-Bold', alignment=TA_CENTER)

    elements = []

    # ─── Resolve dates and basic data ────────────────────────
    receipt_number = payment.get('receipt_number', 'N/A')
    payment_date_raw = payment.get('payment_date', '')
    try:
        if isinstance(payment_date_raw, datetime):
            payment_dt = payment_date_raw
        else:
            payment_dt = datetime.fromisoformat(str(payment_date_raw).replace(' ', 'T').split('.')[0])
        payment_date = payment_dt.strftime('%d %b %Y')
        payment_time = payment_dt.strftime('%I:%M %p')
    except Exception:
        payment_date = str(payment_date_raw)[:10]
        payment_time = ''

    tenant_name = (tenant.get('name') if tenant else None) or payment.get('tenant_name', 'N/A')
    tenant_num = (tenant.get('tenant_number') if tenant else None) or ''
    tenant_email = (tenant.get('email') if tenant else None) or payment.get('tenant_email', '')
    property_addr = (contract.get('property_address') if contract else None) or payment.get('property_address', 'N/A')
    contract_num = (contract.get('contract_number') if contract else None) or 'N/A'

    period_month_es = {
        'january':'Enero','february':'Febrero','march':'Marzo','april':'Abril',
        'may':'Mayo','june':'Junio','july':'Julio','august':'Agosto',
        'september':'Septiembre','october':'Octubre','november':'Noviembre','december':'Diciembre'
    }
    period_month_raw = (payment.get('period_month') or '').lower()
    period_month = period_month_es.get(period_month_raw, period_month_raw.capitalize())
    period_year = payment.get('period_year', '')

    amount = float(payment.get('amount', 0) or 0)
    late_fee = float(payment.get('late_fee', 0) or 0)
    total_paid = float(payment.get('total_paid', payment.get('amount', 0)) or 0)

    payment_method_raw = (payment.get('payment_method') or 'N/A').lower()
    method_map = {
        'card': 'Tarjeta de Crédito/Débito',
        'credit_card': 'Tarjeta de Crédito',
        'debit_card': 'Tarjeta de Débito',
        'stripe': 'Tarjeta (Stripe)',
        'ach': 'Transferencia ACH',
        'bank_transfer': 'Transferencia Bancaria',
        'cash': 'Efectivo',
        'check': 'Cheque',
        'autopay': 'Pago Automático',
    }
    payment_method = method_map.get(payment_method_raw, payment_method_raw.replace('_', ' ').title())

    status = (payment.get('status') or 'completed').lower()

    # ─── HERO HEADER (red banner with logo on black background) ──
    logo_path = _get_logo_path("dark")
    if logo_path:
        try:
            logo = RLImage(logo_path, width=1.25 * inch, height=1.25 * inch, kind='proportional')
        except Exception:
            logo = Paragraph(f"<b>{co['name']}</b>", S['hero_title'])
    else:
        logo = Paragraph(f"<b>{co['name']}</b>", S['hero_title'])

    title_block = [
        Paragraph("RECIBO DE PAGO", S['hero_title']),
        Spacer(1, 2),
        Paragraph("Payment Receipt — Renta / Rent", S['hero_sub']),
    ]

    meta_block = [
        Paragraph("RECIBO N.°", S['hero_sub']),
        Paragraph(f"<b>{receipt_number}</b>", S['hero_meta_b']),
        Spacer(1, 4),
        Paragraph("FECHA / DATE", S['hero_sub']),
        Paragraph(f"<b>{payment_date}</b>", S['hero_meta_b']),
    ]

    hero = Table(
        [[logo, title_block, meta_block]],
        colWidths=[1.15 * inch, 3.6 * inch, 2.65 * inch],
        rowHeights=[1.1 * inch],
    )
    hero.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_CHARCOAL),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(hero)

    # Red accent bar
    elements.append(HRFlowable(width="100%", thickness=4, color=BRAND_RED, spaceBefore=0, spaceAfter=0))
    elements.append(Spacer(1, 18))

    # ─── AMOUNT HERO + STATUS BADGE ─────────────────────────
    if status == 'completed':
        badge_text = "✓ PAGADO  •  PAID"
        badge_color = colors.HexColor('#10B981')
    elif status == 'pending':
        badge_text = "PENDIENTE  •  PENDING"
        badge_color = colors.HexColor('#F59E0B')
    else:
        badge_text = status.upper()
        badge_color = MUTED_GRAY

    amount_block = [
        Paragraph("MONTO TOTAL PAGADO  •  TOTAL PAID", S['amount_lbl']),
        Spacer(1, 4),
        Paragraph(f"${total_paid:,.2f}", S['amount_big']),
        Spacer(1, 8),
    ]

    badge_table = Table(
        [[Paragraph(badge_text, S['status_paid'])]],
        colWidths=[2.4 * inch],
        rowHeights=[0.28 * inch],
    )
    badge_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), badge_color),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ROUNDEDCORNERS', [12, 12, 12, 12]),
    ]))
    amount_block.append(badge_table)

    amount_wrap = Table([[a] for a in amount_block],
                       colWidths=[7.5 * inch])
    amount_wrap.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(amount_wrap)
    elements.append(Spacer(1, 22))

    # ─── INFO CARDS (Tenant + Property) ─────────────────────
    tenant_card = [
        Paragraph("INQUILINO  •  TENANT", S['section']),
        Paragraph(tenant_name, S['val']),
    ]
    if tenant_num:
        tenant_card.append(Paragraph(f"ID: {tenant_num}", S['val_small']))
    if tenant_email:
        tenant_card.append(Paragraph(tenant_email, S['val_small']))

    prop_card = [
        Paragraph("PROPIEDAD  •  PROPERTY", S['section']),
        Paragraph(property_addr, S['val']),
        Paragraph(f"Contrato N.°: {contract_num}", S['val_small']),
    ]

    info_cards = Table(
        [[tenant_card, '', prop_card]],
        colWidths=[3.6 * inch, 0.2 * inch, 3.6 * inch],
    )
    info_cards.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#F9FAFB')),
        ('BACKGROUND', (2, 0), (2, 0), colors.HexColor('#F9FAFB')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('BOX', (0, 0), (0, 0), 0.5, BORDER_GRAY),
        ('BOX', (2, 0), (2, 0), 0.5, BORDER_GRAY),
        # Middle column = transparent gap
        ('BACKGROUND', (1, 0), (1, 0), colors.white),
        ('LEFTPADDING', (1, 0), (1, 0), 0),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
    ]))
    elements.append(info_cards)
    elements.append(Spacer(1, 18))

    # ─── PAYMENT DETAILS TABLE ─────────────────────────────
    elements.append(Paragraph("DETALLE DEL PAGO  •  PAYMENT DETAILS", S['section']))
    elements.append(Spacer(1, 4))

    detail_rows = [
        [
            Paragraph("Concepto / Description", S['table_th']),
            Paragraph("Período / Period", S['table_th']),
            Paragraph("Monto / Amount", S['table_th']),
        ],
        [
            Paragraph("Renta Mensual  •  Monthly Rent", S['table_td']),
            Paragraph(f"{period_month} {period_year}", S['table_td']),
            Paragraph(f"${amount:,.2f}", S['table_td_amount']),
        ],
    ]
    if late_fee > 0:
        detail_rows.append([
            Paragraph("Cargo por Mora  •  Late Fee", S['table_td']),
            Paragraph(f"{period_month} {period_year}", S['table_td']),
            Paragraph(f"${late_fee:,.2f}", S['table_td_amount']),
        ])

    detail_table = Table(detail_rows,
        colWidths=[3.8 * inch, 1.9 * inch, 1.8 * inch],
    )
    detail_table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_CHARCOAL),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('TOPPADDING', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        # Body
        ('TOPPADDING', (0, 1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, BORDER_GRAY),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9FAFB')]),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
    ]))
    elements.append(detail_table)

    # ─── TOTAL BAR ──────────────────────────────────────────
    total_bar = Table(
        [[
            Paragraph("TOTAL PAGADO  •  TOTAL PAID", S['total_label']),
            Paragraph(f"${total_paid:,.2f}", S['total_amount']),
        ]],
        colWidths=[5.7 * inch, 1.8 * inch],
    )
    total_bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF5F7')),
        ('LINEABOVE', (0, 0), (-1, 0), 2, BRAND_RED),
        ('LINEBELOW', (0, 0), (-1, 0), 2, BRAND_RED),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(total_bar)
    elements.append(Spacer(1, 18))

    # ─── PAYMENT METHOD STRIP ──────────────────────────────
    method_strip = Table(
        [[
            Paragraph("MÉTODO DE PAGO", S['lbl']),
            Paragraph(payment_method, S['val']),
            Paragraph("PROCESADO", S['lbl']),
            Paragraph(f"{payment_date}  {payment_time}", S['val']),
        ]],
        colWidths=[1.4 * inch, 2.4 * inch, 1.3 * inch, 2.4 * inch],
    )
    method_strip.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9FAFB')),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(method_strip)
    elements.append(Spacer(1, 28))

    # ─── PAID STAMP (decorative) ───────────────────────────
    if status == 'completed':
        elements.append(Paragraph("✓ PAGO RECIBIDO", S['stamp']))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            "Este recibo confirma que su pago ha sido procesado y recibido exitosamente.",
            ParagraphStyle('thanks', parent=base_styles['Normal'],
                fontSize=9, leading=12, textColor=GRAY,
                fontName='Helvetica-Oblique', alignment=TA_CENTER)
        ))
        elements.append(Paragraph(
            "This receipt confirms your payment has been successfully processed and received.",
            ParagraphStyle('thanks2', parent=base_styles['Normal'],
                fontSize=8, leading=11, textColor=MUTED_GRAY,
                fontName='Helvetica-Oblique', alignment=TA_CENTER)
        ))
        elements.append(Spacer(1, 20))

    # ─── FOOTER ─────────────────────────────────────────────
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"{co['name']}", S['footer_bold']))
    elements.append(Paragraph(
        f"{co['address']}  •  {co['phone']}  •  {co['email']}",
        S['footer']
    ))
    if co.get('website'):
        elements.append(Paragraph(co['website'], S['footer']))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"Documento generado el {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC  •  Recibo: {receipt_number}",
        ParagraphStyle('gen', parent=base_styles['Normal'],
            fontSize=6.5, leading=9, textColor=MUTED_GRAY,
            fontName='Helvetica', alignment=TA_CENTER)
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return base64.b64encode(pdf_bytes).decode('utf-8')
