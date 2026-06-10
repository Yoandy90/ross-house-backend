"""
Consent Forms PDF Generator
Generates individual consent/authorization PDFs for:
- Background Check Authorization
- Income Verification Authorization  
- Photo/Video Consent
- ACH/Auto-Debit Authorization
"""
import io
import base64
import logging
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# STYLES
# ═══════════════════════════════════════════════════════════════════
def get_consent_styles():
    """Get styled paragraph styles for consent forms"""
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(
        name='ConsentTitle',
        fontName='Helvetica-Bold',
        fontSize=16,
        textColor=HexColor('#1a1a1a'),
        alignment=TA_CENTER,
        spaceAfter=20,
    ))
    
    styles.add(ParagraphStyle(
        name='ConsentSubtitle',
        fontName='Helvetica',
        fontSize=11,
        textColor=HexColor('#666666'),
        alignment=TA_CENTER,
        spaceAfter=30,
    ))
    
    styles.add(ParagraphStyle(
        name='ConsentBody',
        fontName='Helvetica',
        fontSize=10,
        textColor=HexColor('#333333'),
        alignment=TA_JUSTIFY,
        spaceAfter=12,
        leading=14,
    ))
    
    styles.add(ParagraphStyle(
        name='ConsentBold',
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=HexColor('#1a1a1a'),
        spaceAfter=8,
    ))
    
    styles.add(ParagraphStyle(
        name='ConsentSmall',
        fontName='Helvetica',
        fontSize=8,
        textColor=HexColor('#666666'),
        alignment=TA_CENTER,
        spaceAfter=6,
    ))
    
    return styles


def add_header(elements, styles, title: str, subtitle: str = None):
    """Add company header to consent form"""
    elements.append(Paragraph(
        "<b>ROSS HOUSE RENTALS LLC</b>",
        styles['ConsentTitle']
    ))
    if subtitle:
        elements.append(Paragraph(subtitle, styles['ConsentSubtitle']))
    elements.append(Paragraph(
        f"<b>{title}</b>",
        styles['ConsentTitle']
    ))
    elements.append(Spacer(1, 20))


def add_signature_block(elements, styles, signer_name: str, signature_data: str = None, date_signed: str = None):
    """Add signature block to consent form"""
    elements.append(Spacer(1, 30))
    
    # Signature line or image
    if signature_data:
        try:
            # Decode and add signature image
            if signature_data.startswith('data:'):
                signature_data = signature_data.split(',')[1]
            sig_bytes = base64.b64decode(signature_data)
            sig_img = Image(io.BytesIO(sig_bytes), width=2*inch, height=0.75*inch)
            elements.append(sig_img)
        except Exception as e:
            logger.warning(f"Could not add signature image: {e}")
            elements.append(Paragraph("_" * 50, styles['ConsentBody']))
    else:
        elements.append(Paragraph("_" * 50, styles['ConsentBody']))
    
    elements.append(Paragraph(f"<b>{signer_name}</b>", styles['ConsentBold']))
    elements.append(Paragraph(
        f"Fecha / Date: {date_signed or datetime.now().strftime('%m/%d/%Y')}",
        styles['ConsentBody']
    ))


# ═══════════════════════════════════════════════════════════════════
# BACKGROUND CHECK AUTHORIZATION
# ═══════════════════════════════════════════════════════════════════
def generate_background_check_consent(
    applicant_name: str,
    applicant_email: str = "",
    applicant_phone: str = "",
    applicant_ssn_last4: str = "XXXX",
    applicant_dob: str = "",
    property_address: str = "",
    signature_data: str = None,
    date_signed: str = None,
) -> str:
    """Generate Background Check Authorization PDF"""
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = get_consent_styles()
    elements = []
    
    add_header(elements, styles, 
        "AUTORIZACIÓN PARA VERIFICACIÓN DE ANTECEDENTES",
        "Background Check Authorization"
    )
    
    # Applicant Info
    elements.append(Paragraph("<b>INFORMACIÓN DEL SOLICITANTE / APPLICANT INFORMATION</b>", styles['ConsentBold']))
    
    info_data = [
        ["Nombre / Name:", applicant_name],
        ["Email:", applicant_email or "N/A"],
        ["Teléfono / Phone:", applicant_phone or "N/A"],
        ["Últimos 4 del SSN / Last 4 SSN:", f"XXX-XX-{applicant_ssn_last4}"],
        ["Fecha de Nacimiento / DOB:", applicant_dob or "N/A"],
        ["Propiedad / Property:", property_address or "N/A"],
    ]
    
    info_table = Table(info_data, colWidths=[2.5*inch, 4*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (-1, -1), HexColor('#333333')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))
    
    # Authorization Text
    elements.append(Paragraph("<b>AUTORIZACIÓN / AUTHORIZATION</b>", styles['ConsentBold']))
    
    auth_text = """
    Yo, el abajo firmante, por la presente autorizo a Ross House Rentals LLC y sus agentes designados 
    a obtener informes de verificación de antecedentes sobre mí. Esta autorización incluye, pero no se 
    limita a:
    <br/><br/>
    • Verificación de historial criminal (federal, estatal y del condado)<br/>
    • Verificación de historial de crédito<br/>
    • Verificación de historial de desalojos<br/>
    • Verificación de referencias de arrendadores anteriores<br/>
    • Verificación de empleo e ingresos<br/>
    • Verificación de identidad<br/>
    <br/>
    I, the undersigned, hereby authorize Ross House Rentals LLC and its designated agents to obtain 
    background verification reports about me. This authorization includes, but is not limited to:
    <br/><br/>
    • Criminal history verification (federal, state, and county)<br/>
    • Credit history verification<br/>
    • Eviction history verification<br/>
    • Previous landlord reference verification<br/>
    • Employment and income verification<br/>
    • Identity verification
    """
    elements.append(Paragraph(auth_text, styles['ConsentBody']))
    
    # FCRA Notice
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>AVISO FCRA / FCRA NOTICE</b>", styles['ConsentBold']))
    
    fcra_text = """
    De acuerdo con la Ley de Informe de Crédito Justo (FCRA), usted tiene derecho a recibir una copia 
    de cualquier informe obtenido y a disputar cualquier información inexacta. Si se toma alguna acción 
    adversa basada en este informe, se le notificará por escrito.
    <br/><br/>
    Under the Fair Credit Reporting Act (FCRA), you have the right to receive a copy of any report 
    obtained and to dispute any inaccurate information. If any adverse action is taken based on this 
    report, you will be notified in writing.
    """
    elements.append(Paragraph(fcra_text, styles['ConsentBody']))
    
    # Certification
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>CERTIFICACIÓN / CERTIFICATION</b>", styles['ConsentBold']))
    
    cert_text = """
    Al firmar abajo, certifico que toda la información proporcionada es verdadera y completa. Entiendo 
    que proporcionar información falsa puede resultar en la denegación de mi solicitud o terminación 
    de cualquier contrato de arrendamiento.
    <br/><br/>
    By signing below, I certify that all information provided is true and complete. I understand that 
    providing false information may result in denial of my application or termination of any lease agreement.
    """
    elements.append(Paragraph(cert_text, styles['ConsentBody']))
    
    # Signature
    add_signature_block(elements, styles, applicant_name, signature_data, date_signed)
    
    # Footer
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "Ross House Rentals LLC • (806) 934-2018 • rosshouserentals@gmail.com",
        styles['ConsentSmall']
    ))
    
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    return base64.b64encode(pdf_bytes).decode('utf-8')


# ═══════════════════════════════════════════════════════════════════
# INCOME VERIFICATION AUTHORIZATION
# ═══════════════════════════════════════════════════════════════════
def generate_income_verification_consent(
    applicant_name: str,
    employer_name: str = "",
    employer_phone: str = "",
    applicant_position: str = "",
    signature_data: str = None,
    date_signed: str = None,
) -> str:
    """Generate Income Verification Authorization PDF"""
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = get_consent_styles()
    elements = []
    
    add_header(elements, styles,
        "AUTORIZACIÓN PARA VERIFICACIÓN DE INGRESOS",
        "Income Verification Authorization"
    )
    
    # Employment Info
    elements.append(Paragraph("<b>INFORMACIÓN DE EMPLEO / EMPLOYMENT INFORMATION</b>", styles['ConsentBold']))
    
    info_data = [
        ["Nombre del Solicitante / Applicant Name:", applicant_name],
        ["Empleador / Employer:", employer_name or "N/A"],
        ["Teléfono del Empleador / Employer Phone:", employer_phone or "N/A"],
        ["Posición / Position:", applicant_position or "N/A"],
    ]
    
    info_table = Table(info_data, colWidths=[2.8*inch, 3.7*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))
    
    # Authorization
    elements.append(Paragraph("<b>AUTORIZACIÓN / AUTHORIZATION</b>", styles['ConsentBold']))
    
    auth_text = """
    Por la presente autorizo a Ross House Rentals LLC a contactar a mi empleador actual y/o anteriores 
    empleadores para verificar mi historial de empleo e ingresos. Esta autorización incluye:
    <br/><br/>
    • Verificación de fechas de empleo<br/>
    • Verificación de salario/ingresos actuales<br/>
    • Verificación de posición/título<br/>
    • Estado de empleo (tiempo completo, medio tiempo, contrato)<br/>
    • Probabilidad de empleo continuo<br/>
    <br/>
    I hereby authorize Ross House Rentals LLC to contact my current and/or previous employers to verify 
    my employment history and income. This authorization includes:
    <br/><br/>
    • Verification of employment dates<br/>
    • Verification of current salary/income<br/>
    • Verification of position/title<br/>
    • Employment status (full-time, part-time, contract)<br/>
    • Likelihood of continued employment
    """
    elements.append(Paragraph(auth_text, styles['ConsentBody']))
    
    # Release
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>LIBERACIÓN DE RESPONSABILIDAD / RELEASE OF LIABILITY</b>", styles['ConsentBold']))
    
    release_text = """
    Libero a mi empleador de cualquier responsabilidad por proporcionar la información solicitada y 
    libero a Ross House Rentals LLC de cualquier responsabilidad relacionada con el uso de esta información.
    <br/><br/>
    I release my employer from any liability for providing the requested information and release 
    Ross House Rentals LLC from any liability related to the use of this information.
    """
    elements.append(Paragraph(release_text, styles['ConsentBody']))
    
    # Signature
    add_signature_block(elements, styles, applicant_name, signature_data, date_signed)
    
    # Footer
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "Ross House Rentals LLC • (806) 934-2018 • rosshouserentals@gmail.com",
        styles['ConsentSmall']
    ))
    
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    return base64.b64encode(pdf_bytes).decode('utf-8')


# ═══════════════════════════════════════════════════════════════════
# PHOTO/VIDEO CONSENT
# ═══════════════════════════════════════════════════════════════════
def generate_photo_video_consent(
    tenant_name: str,
    property_address: str = "",
    signature_data: str = None,
    date_signed: str = None,
) -> str:
    """Generate Photo/Video Consent PDF"""
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = get_consent_styles()
    elements = []
    
    add_header(elements, styles,
        "CONSENTIMIENTO PARA FOTOGRAFÍAS Y VIDEO",
        "Photo and Video Consent"
    )
    
    # Tenant Info
    info_data = [
        ["Nombre del Inquilino / Tenant Name:", tenant_name],
        ["Dirección de la Propiedad / Property Address:", property_address or "N/A"],
        ["Fecha / Date:", date_signed or datetime.now().strftime('%m/%d/%Y')],
    ]
    
    info_table = Table(info_data, colWidths=[2.8*inch, 3.7*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))
    
    # Consent Text
    elements.append(Paragraph("<b>CONSENTIMIENTO / CONSENT</b>", styles['ConsentBold']))
    
    consent_text = """
    Por la presente otorgo mi consentimiento a Ross House Rentals LLC para:
    <br/><br/>
    <b>1. Documentación de la Propiedad</b><br/>
    Tomar fotografías y/o videos de la propiedad alquilada para propósitos de:<br/>
    • Inspecciones de entrada y salida (move-in/move-out)<br/>
    • Documentación de mantenimiento y reparaciones<br/>
    • Registro del estado de la propiedad<br/>
    • Cumplimiento de requisitos de seguro<br/>
    <br/>
    <b>2. Identificación Personal</b><br/>
    Tomar y almacenar fotografías de mi identificación oficial para:<br/>
    • Verificación de identidad<br/>
    • Archivo del contrato de arrendamiento<br/>
    • Cumplimiento con regulaciones estatales<br/>
    <br/>
    <b>3. Almacenamiento y Uso</b><br/>
    Entiendo que estas imágenes serán almacenadas de forma segura y utilizadas únicamente para los 
    propósitos mencionados. Las imágenes NO serán compartidas públicamente ni utilizadas con fines 
    de marketing sin mi consentimiento adicional por escrito.
    """
    elements.append(Paragraph(consent_text, styles['ConsentBody']))
    
    # English Version
    elements.append(Spacer(1, 15))
    
    consent_text_en = """
    I hereby grant my consent to Ross House Rentals LLC to:
    <br/><br/>
    <b>1. Property Documentation</b><br/>
    Take photographs and/or videos of the rented property for purposes of:<br/>
    • Move-in and move-out inspections<br/>
    • Maintenance and repair documentation<br/>
    • Property condition records<br/>
    • Insurance compliance<br/>
    <br/>
    <b>2. Personal Identification</b><br/>
    Take and store photographs of my official identification for:<br/>
    • Identity verification<br/>
    • Lease agreement records<br/>
    • State regulation compliance<br/>
    <br/>
    <b>3. Storage and Use</b><br/>
    I understand these images will be stored securely and used only for the mentioned purposes. 
    Images will NOT be shared publicly or used for marketing purposes without my additional written consent.
    """
    elements.append(Paragraph(consent_text_en, styles['ConsentBody']))
    
    # Revocation
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>REVOCACIÓN / REVOCATION</b>", styles['ConsentBold']))
    
    revocation_text = """
    Entiendo que puedo revocar este consentimiento en cualquier momento mediante notificación por escrito, 
    excepto por imágenes ya utilizadas para documentación de inspecciones o procesos legales.
    <br/><br/>
    I understand I may revoke this consent at any time through written notice, except for images already 
    used for inspection documentation or legal proceedings.
    """
    elements.append(Paragraph(revocation_text, styles['ConsentBody']))
    
    # Signature
    add_signature_block(elements, styles, tenant_name, signature_data, date_signed)
    
    # Footer
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "Ross House Rentals LLC • (806) 934-2018 • rosshouserentals@gmail.com",
        styles['ConsentSmall']
    ))
    
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    return base64.b64encode(pdf_bytes).decode('utf-8')


# ═══════════════════════════════════════════════════════════════════
# ACH/AUTO-DEBIT AUTHORIZATION
# ═══════════════════════════════════════════════════════════════════
def generate_ach_authorization(
    tenant_name: str,
    bank_name: str = "",
    account_type: str = "checking",
    routing_number: str = "",
    account_number_last4: str = "XXXX",
    monthly_amount: float = 0,
    property_address: str = "",
    signature_data: str = None,
    date_signed: str = None,
) -> str:
    """Generate ACH/Auto-Debit Authorization PDF"""
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = get_consent_styles()
    elements = []
    
    add_header(elements, styles,
        "AUTORIZACIÓN DE DÉBITO AUTOMÁTICO (ACH)",
        "ACH Auto-Debit Authorization"
    )
    
    # Account Info
    elements.append(Paragraph("<b>INFORMACIÓN DE LA CUENTA / ACCOUNT INFORMATION</b>", styles['ConsentBold']))
    
    account_type_display = "Cuenta de Cheques / Checking" if account_type == "checking" else "Cuenta de Ahorros / Savings"
    
    info_data = [
        ["Nombre del Inquilino / Tenant Name:", tenant_name],
        ["Propiedad / Property:", property_address or "N/A"],
        ["Nombre del Banco / Bank Name:", bank_name or "N/A"],
        ["Tipo de Cuenta / Account Type:", account_type_display],
        ["Número de Ruta / Routing Number:", routing_number or "XXXXXXXXX"],
        ["Últimos 4 de la Cuenta / Account Last 4:", f"XXXXXX{account_number_last4}"],
        ["Monto Mensual / Monthly Amount:", f"${monthly_amount:,.2f}" if monthly_amount else "N/A"],
    ]
    
    info_table = Table(info_data, colWidths=[2.8*inch, 3.7*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 5), (-1, 5), HexColor('#f0f0f0')),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 20))
    
    # Authorization
    elements.append(Paragraph("<b>AUTORIZACIÓN / AUTHORIZATION</b>", styles['ConsentBold']))
    
    auth_text = f"""
    Por la presente autorizo a Ross House Rentals LLC a iniciar débitos electrónicos (ACH) de mi cuenta 
    bancaria indicada arriba para el pago de mi renta mensual y cualquier cargo adicional acordado.
    <br/><br/>
    <b>Términos de la Autorización:</b><br/>
    • El débito se realizará el día 1 de cada mes (o el siguiente día hábil)<br/>
    • El monto debitado será la renta mensual acordada: ${monthly_amount:,.2f}<br/>
    • Cargos adicionales (late fees, utilidades) serán notificados con 3 días de anticipación<br/>
    • Esta autorización permanece vigente hasta que la cancele por escrito<br/>
    <br/>
    I hereby authorize Ross House Rentals LLC to initiate electronic debits (ACH) from my bank account 
    indicated above for payment of my monthly rent and any additional agreed charges.
    <br/><br/>
    <b>Authorization Terms:</b><br/>
    • Debit will occur on the 1st of each month (or next business day)<br/>
    • Amount debited will be the agreed monthly rent: ${monthly_amount:,.2f}<br/>
    • Additional charges (late fees, utilities) will be notified 3 days in advance<br/>
    • This authorization remains in effect until I cancel it in writing
    """
    elements.append(Paragraph(auth_text, styles['ConsentBody']))
    
    # Cancellation
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>CANCELACIÓN / CANCELLATION</b>", styles['ConsentBold']))
    
    cancel_text = """
    Para cancelar esta autorización, debo notificar a Ross House Rentals LLC por escrito con al menos 
    5 días hábiles de anticipación antes del siguiente débito programado.
    <br/><br/>
    To cancel this authorization, I must notify Ross House Rentals LLC in writing at least 5 business 
    days before the next scheduled debit.
    """
    elements.append(Paragraph(cancel_text, styles['ConsentBody']))
    
    # Insufficient Funds Notice
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>AVISO DE FONDOS INSUFICIENTES / INSUFFICIENT FUNDS NOTICE</b>", styles['ConsentBold']))
    
    nsf_text = """
    Entiendo que si el débito es rechazado por fondos insuficientes, puedo estar sujeto a un cargo 
    por cheque devuelto de $35.00 además de cualquier cargo por pago tardío aplicable.
    <br/><br/>
    I understand that if the debit is rejected for insufficient funds, I may be subject to a returned 
    check fee of $35.00 in addition to any applicable late payment charges.
    """
    elements.append(Paragraph(nsf_text, styles['ConsentBody']))
    
    # Signature
    add_signature_block(elements, styles, tenant_name, signature_data, date_signed)
    
    # Footer
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "Ross House Rentals LLC • (806) 934-2018 • rosshouserentals@gmail.com",
        styles['ConsentSmall']
    ))
    
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    return base64.b64encode(pdf_bytes).decode('utf-8')
