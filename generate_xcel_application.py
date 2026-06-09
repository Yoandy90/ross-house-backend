"""
Generate a filled Green Button Program Service Application for Ross House Rentals LLC
"""
import io
import base64
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, Image as RLImage
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

BRAND = HexColor('#0072CE')  # Xcel Energy blue
DARK = HexColor('#1F2937')
GRAY = HexColor('#6B7280')
LIGHT_BG = HexColor('#F0F4F8')
BORDER = HexColor('#D1D5DB')
GREEN_CHECK = HexColor('#16A34A')
FIELD_BG = HexColor('#EFF6FF')


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('XcelTitle', fontName='Helvetica-Bold', fontSize=16, textColor=BRAND, spaceAfter=4))
    styles.add(ParagraphStyle('XcelSub', fontName='Helvetica-Bold', fontSize=12, textColor=DARK, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle('XcelBody', fontName='Helvetica', fontSize=10, textColor=DARK, spaceAfter=3, leading=13))
    styles.add(ParagraphStyle('XcelSmall', fontName='Helvetica', fontSize=8.5, textColor=GRAY, spaceAfter=2, leading=11))
    styles.add(ParagraphStyle('XcelBold', fontName='Helvetica-Bold', fontSize=10, textColor=DARK, spaceAfter=3, leading=13))
    styles.add(ParagraphStyle('FieldLabel', fontName='Helvetica-Bold', fontSize=8, textColor=GRAY, spaceAfter=1))
    styles.add(ParagraphStyle('FieldValue', fontName='Helvetica-Bold', fontSize=10, textColor=BRAND, spaceAfter=2, leading=13))
    styles.add(ParagraphStyle('Legal', fontName='Helvetica', fontSize=7.5, textColor=DARK, spaceAfter=2, leading=9.5))
    styles.add(ParagraphStyle('Footer', fontName='Helvetica', fontSize=7, textColor=GRAY, alignment=TA_CENTER))
    styles.add(ParagraphStyle('CheckMark', fontName='Helvetica-Bold', fontSize=10, textColor=GREEN_CHECK))
    return styles


def field_row(label, value, width1=140, width2=330):
    """Create a label-value field row"""
    return Table(
        [[Paragraph(f"<font size='8' color='#6B7280'><b>{label}</b></font>", getSampleStyleSheet()['Normal']),
          Paragraph(f"<font size='10' color='#0072CE'><b>{value}</b></font>", getSampleStyleSheet()['Normal'])]],
        colWidths=[width1, width2]
    )


def generate_filled_application():
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=0.5*inch, bottomMargin=0.5*inch,
                            leftMargin=0.6*inch, rightMargin=0.6*inch)
    S = build_styles()
    E = []

    # ═══ HEADER ═══
    E.append(Paragraph("Xcel Energy® — Service Application", S['XcelTitle']))
    E.append(HRFlowable(width="100%", thickness=2, color=BRAND))
    E.append(Spacer(1, 6))
    E.append(Paragraph("<b>Green Button Program</b>", S['XcelSub']))
    E.append(Paragraph(
        "Xcel Energy is looking forward to growing our list of Green Button service providers. "
        "To become a Green Button service provider, your company must provide one or more services "
        "that utilize Connect My Data, and be in Green Button compliance.", S['XcelBody']))
    E.append(Paragraph(
        "<i>Submit this application to: <b>greenbuttonsupport@xcelenergy.com</b></i>", S['XcelSmall']))
    E.append(Spacer(1, 12))

    # ═══ COMPANY INFORMATION ═══
    E.append(Paragraph("Company Information", S['XcelSub']))
    E.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    E.append(Spacer(1, 6))

    company_fields = [
        ['Company name (legal/registered)', 'Ross House Rentals LLC'],
        ['Tax Identification Number', '[TIN a completar por Yoandy]'],
        ['Street address', '600 Bliss Ave'],
        ['City / State / ZIP', 'Dumas, TX 79029'],
        ['Mailing address', 'Same as above'],
        ['Website', 'https://rosshouserentals.com'],
        ['Application date', datetime.now().strftime('%m/%d/%Y')],
    ]

    for label, value in company_fields:
        data = [[
            Paragraph(f"<font size='8' color='#6B7280'><b>{label}</b></font>", getSampleStyleSheet()['Normal']),
            Paragraph(f"<font size='10' color='#0072CE'><b>{value}</b></font>", getSampleStyleSheet()['Normal']),
        ]]
        t = Table(data, colWidths=[180, 300])
        t.setStyle(TableStyle([
            ('BACKGROUND', (1, 0), (1, 0), FIELD_BG),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        E.append(t)
    E.append(Spacer(1, 12))

    # ═══ CONTACT INFORMATION ═══
    E.append(Paragraph("Contact Information", S['XcelSub']))
    E.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    E.append(Spacer(1, 6))
    E.append(Paragraph("Xcel Energy requires contact information for each service area your company plans to offer services in.", S['XcelSmall']))
    E.append(Spacer(1, 4))

    contact_header = ['Area', 'First Name', 'Last Name', 'Phone', 'Email']
    contact_rows = [
        ['✅ Primary', 'Yoandy', 'Ross', '[TEL]', 'yoandyross@gmail.com'],
        ['✅ Texas', 'Yoandy', 'Ross', '[TEL]', 'yoandyross@gmail.com'],
        ['Colorado', '', '', '', ''],
        ['Michigan', '', '', '', ''],
        ['Minnesota', '', '', '', ''],
        ['Wisconsin', '', '', '', ''],
        ['New Mexico', '', '', '', ''],
        ['North Dakota', '', '', '', ''],
        ['South Dakota', '', '', '', ''],
    ]

    all_rows = [contact_header] + contact_rows
    ct = Table(all_rows, colWidths=[80, 75, 75, 75, 170])
    ct.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), BRAND),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, 1), HexColor('#D1FAE5')),
        ('BACKGROUND', (0, 2), (-1, 2), HexColor('#D1FAE5')),
        ('BACKGROUND', (0, 3), (-1, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    E.append(ct)
    E.append(Spacer(1, 4))
    E.append(Paragraph("<i>Nota: [TEL] = Completar con número de teléfono de la oficina</i>", S['XcelSmall']))
    E.append(PageBreak())

    # ═══ GREEN BUTTON SERVICE INFORMATION ═══
    E.append(Paragraph("Xcel Energy® — Green Button Service Information", S['XcelTitle']))
    E.append(HRFlowable(width="100%", thickness=2, color=BRAND))
    E.append(Spacer(1, 10))

    E.append(Paragraph("Green Button Service Information", S['XcelSub']))
    E.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    E.append(Spacer(1, 6))

    svc_fields = [
        ['Company name (for display)', 'Ross House Rentals'],
        ['Service description\n(150 characters max)',
         'Property management platform providing tenants with energy usage tracking, cost insights and efficiency alerts.'],
    ]
    for label, value in svc_fields:
        data = [[
            Paragraph(f"<font size='8' color='#6B7280'><b>{label}</b></font>", getSampleStyleSheet()['Normal']),
            Paragraph(f"<font size='10' color='#0072CE'><b>{value}</b></font>", getSampleStyleSheet()['Normal']),
        ]]
        t = Table(data, colWidths=[180, 300])
        t.setStyle(TableStyle([
            ('BACKGROUND', (1, 0), (1, 0), FIELD_BG),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        E.append(t)
    E.append(Spacer(1, 10))

    # Type of service
    E.append(Paragraph("<b>Type of service</b> (select all that apply):", S['XcelBold']))
    type_options = [
        ('Financing', False), ('Products', False), ('Research', False),
        ('Consultation', False), ('Services', True),
    ]
    type_data = []
    for name, checked in type_options:
        mark = "✅" if checked else "☐"
        type_data.append(f"{mark} {name}")
    E.append(Paragraph("    ".join(type_data), S['XcelBody']))
    E.append(Spacer(1, 8))

    # Service segment
    E.append(Paragraph("<b>Service segment</b> (select all that apply):", S['XcelBold']))
    seg_options = [('Residential', True), ('Commercial', True), ('Industrial', False)]
    seg_data = []
    for name, checked in seg_options:
        mark = "✅" if checked else "☐"
        seg_data.append(f"{mark} {name}")
    E.append(Paragraph("    ".join(seg_data), S['XcelBody']))
    E.append(Spacer(1, 8))

    # Service category
    E.append(Paragraph("<b>Service category</b> (select all that apply):", S['XcelBold']))
    cat_options = [
        ('Residential solar', False), ('Energy management', True),
        ('Gas products/service', False), ('Community solar', False),
        ('Energy Efficiency', True), ('Electric vehicles', False),
        ('Research', False), ('Survey', False),
        ('Offers and Rewards', False), ('Energy supply company (ESCO)', False),
    ]
    cat_lines = []
    for name, checked in cat_options:
        mark = "✅" if checked else "☐"
        cat_lines.append(f"{mark} {name}")
    # Display in two columns
    E.append(Paragraph("    ".join(cat_lines[:5]), S['XcelBody']))
    E.append(Paragraph("    ".join(cat_lines[5:]), S['XcelBody']))
    E.append(Spacer(1, 16))

    # ═══ SUMMARY BOX ═══
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND))
    E.append(Spacer(1, 8))
    E.append(Paragraph("<b>Resumen de la Aplicación / Application Summary:</b>", S['XcelBold']))
    summary = [
        ['Campo', 'Valor'],
        ['Compañía', 'Ross House Rentals LLC'],
        ['Dirección', '600 Bliss Ave, Dumas, TX 79029'],
        ['Contacto', 'Yoandy Ross — yoandyross@gmail.com'],
        ['Áreas de Servicio', 'Texas (Southwestern Public Service / SPS)'],
        ['Tipo de Servicio', 'Services'],
        ['Segmento', 'Residential + Commercial'],
        ['Categorías', 'Energy Management + Energy Efficiency'],
        ['Descripción', 'Property management platform providing tenants\nwith energy usage tracking, cost insights\nand efficiency alerts.'],
    ]
    st = Table(summary, colWidths=[130, 340])
    st.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), BRAND),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('BACKGROUND', (0, 1), (-1, -1), FIELD_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    E.append(st)
    E.append(PageBreak())

    # ═══ PAGE 3: TERMS OF USE ═══
    E.append(Paragraph("Xcel Energy® — Green Button Program Terms of Use", S['XcelTitle']))
    E.append(HRFlowable(width="100%", thickness=2, color=BRAND))
    E.append(Spacer(1, 8))
    E.append(Paragraph("PLEASE READ THE FOLLOWING TERMS AND CONDITIONS CAREFULLY", S['XcelBold']))
    E.append(Spacer(1, 4))

    terms = [
        "1. Xcel Energy reserves the right to change, implement, modify, or remove restrictions and limits to these Terms of Use at any time.",
        "2. Through the GBC program, Xcel Energy provides its customers with the ability to consent to transfer of such customer's energy usage data to third parties. You shall not be eligible to receive Data without such customer's prior authorization.",
        "3. XCEL ENERGY MAKES NO REPRESENTATIONS ABOUT THE DATA FOR ANY PURPOSE. ALL DATA IS PROVIDED \"AS IS\" WITHOUT WARRANTY OF ANY KIND.",
        "4. The Data may include technical inaccuracies. Changes are periodically added to the Data. Xcel Energy may make improvements at any time.",
        "5. You shall not use the Data for any purpose that is unlawful or prohibited by these terms.",
        "6. Xcel Energy will make reasonable commercial efforts to provide limited technical support during business hours.",
        "7. Xcel Energy reserves the right to terminate or suspend Your participation at any time, without notice.",
        "8. You agree to indemnify, defend and hold harmless Xcel Energy from any claims arising from Your use of the Data.",
        "9. You represent that you have reasonable technical ability to communicate with Xcel Energy's GBC services.",
        "10. The laws of the State of Minnesota govern these Terms of Use. Disputes shall be resolved in Minneapolis, MN.",
        "11. These Terms constitute the entire agreement between You and Xcel Energy regarding the GBC program.",
    ]
    for t_text in terms:
        E.append(Paragraph(t_text, S['Legal']))
    E.append(Spacer(1, 16))

    # Agreement
    E.append(HRFlowable(width="100%", thickness=1, color=BRAND))
    E.append(Spacer(1, 8))
    E.append(Paragraph("✅  <b>I agree</b> — Acepto los términos y condiciones del programa Green Button.", S['XcelBold']))
    E.append(Spacer(1, 12))

    sig_data = [
        ['Representative signature:', ''],
        ['', ''],
        ['Printed name:', 'Yoandy Ross'],
        ['Title:', 'Owner / Managing Member'],
        ['Company:', 'Ross House Rentals LLC'],
        ['Date:', datetime.now().strftime('%m/%d/%Y')],
    ]
    sigt = Table(sig_data, colWidths=[150, 320])
    sigt.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (1, 0), (1, -1), BRAND),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (1, 0), (1, 0), 1, DARK),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    E.append(sigt)
    E.append(Spacer(1, 16))

    # Instructions for Yoandy
    E.append(HRFlowable(width="100%", thickness=2, color=HexColor('#C8102E')))
    E.append(Spacer(1, 8))
    E.append(Paragraph("⚠️ INSTRUCCIONES PARA COMPLETAR Y ENVIAR", S['XcelBold']))
    E.append(Spacer(1, 4))
    instructions = [
        "<b>1.</b> Completa los campos marcados con <b>[TEL]</b> con el número de teléfono de tu oficina.",
        "<b>2.</b> Completa el <b>Tax Identification Number (TIN/EIN)</b> de Ross House Rentals LLC.",
        "<b>3.</b> Firma donde dice <b>'Representative signature'</b>.",
        "<b>4.</b> Descarga el formulario ORIGINAL de Xcel Energy desde: <b>xcelenergy.com/staticfiles/xe-responsive/Partners/Green_Button_Program_Service_Application.pdf</b>",
        "<b>5.</b> Llénalo con los datos de este documento (copia y pega los valores).",
        "<b>6.</b> Envía el formulario completado por email a: <b>greenbuttonsupport@xcelenergy.com</b>",
        "<b>7.</b> Asunto del email: <b>'Green Button Service Provider Application — Ross House Rentals LLC'</b>",
        "<b>8.</b> El procesamiento puede tomar hasta <b>10 días hábiles</b>.",
    ]
    for inst in instructions:
        E.append(Paragraph(inst, S['XcelBody']))
    E.append(Spacer(1, 12))

    E.append(Paragraph("Este documento fue preparado como referencia. El formulario oficial debe descargarse de xcelenergy.com", S['Footer']))

    doc.build(E)
    return buffer.getvalue()


if __name__ == '__main__':
    pdf_bytes = generate_filled_application()
    with open('/tmp/xcel_green_button_application_filled.pdf', 'wb') as f:
        f.write(pdf_bytes)
    print(f"✅ PDF generated: {len(pdf_bytes)} bytes ({len(pdf_bytes)//1024}KB)")
