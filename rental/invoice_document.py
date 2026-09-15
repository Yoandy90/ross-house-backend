"""Printable invoice references contain no tenant information or credentials."""
import base64
import io
import re
from pathlib import Path
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.graphics.barcode import createBarcodeDrawing


def invoice_reference(payment):
    identity = str(payment.get('_id') or '')
    if not re.fullmatch(r'[0-9a-fA-F]{24}', identity):
        return None
    return 'RHR:' + identity.lower()


def parse_invoice_reference(value):
    match = re.fullmatch(r'RHR:([0-9a-fA-F]{24})', str(value).strip(), re.I)
    return match.group(1).lower() if match else None


def render_invoice_document(payment, *, company, tenant_name, tenant_email,
                            property_address, contract_number, period, amount,
                            late_fee, total, paid, date, method=''):
    fonts = Path(reportlab.__file__).parent / 'fonts'
    for name, file in [('InvoiceRegular', 'Vera.ttf'), ('InvoiceBold', 'VeraBd.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=(612, 792), leftMargin=44,
                            rightMargin=44, topMargin=38, bottomMargin=38,
                            title='Recibo de renta' if paid else 'Factura de renta',
                            author=company['name'])
    ink, muted, red = '#17212B', '#66717E', '#C8102E'
    def p(text, size=10, color=ink, bold=False, align=0):
        return Paragraph(escape(str(text)), ParagraphStyle(
            'text', fontName='InvoiceBold' if bold else 'InvoiceRegular',
            fontSize=size, leading=size*1.4, textColor=colors.HexColor(color),
            alignment=align, spaceAfter=3))
    def table(rows, widths, background=None, padding=12):
        t = Table(rows, colWidths=widths, hAlign='LEFT')
        style = [('VALIGN',(0,0),(-1,-1),'TOP'),
                 ('LEFTPADDING',(0,0),(-1,-1),padding),
                 ('RIGHTPADDING',(0,0),(-1,-1),padding),
                 ('TOPPADDING',(0,0),(-1,-1),padding),
                 ('BOTTOMPADDING',(0,0),(-1,-1),padding)]
        if background: style.append(('BACKGROUND',(0,0),(-1,-1),colors.HexColor(background)))
        t.setStyle(TableStyle(style))
        return t
    number = payment.get('receipt_number') if paid else invoice_reference(payment)
    elements = [table([[[p(company['name'],17,bold=True),p('RENTAL SERVICES',8,muted)],
                       [p('RECIBO DE PAGO' if paid else 'FACTURA DE RENTA',15,bold=True,align=TA_RIGHT),
                        p(number or 'Referencia no disponible',9,muted,align=TA_RIGHT)]]], [262,262],padding=0), Spacer(1,24)]
    elements += [table([[[p('TOTAL PAGADO · USD' if paid else 'TOTAL A PAGAR · USD',9,muted),p(f'${total:,.2f}',34,bold=True)],
                         [p('PAGADO' if paid else 'PENDIENTE',12,'#087F5B' if paid else red,True,TA_RIGHT),
                          p(period,11,ink,False,TA_RIGHT)]]],[310,214],'#F2F5F7',18), Spacer(1,20)]
    elements += [table([[[p('INQUILINO',8,muted,True),p(tenant_name,12,bold=True),p(tenant_email,9,muted)],
                        [p('PROPIEDAD',8,muted,True),p(property_address,11,bold=True),p('Contrato: '+str(contract_number),9,muted)]]],[262,262],padding=0),Spacer(1,24)]
    rows = [[p('CONCEPTO',8,'#FFFFFF',True),p('IMPORTE',8,'#FFFFFF',True,TA_RIGHT)],
            [p('Renta mensual / Monthly rent'),p(f'${amount:,.2f}',11,bold=True,align=TA_RIGHT)]]
    if late_fee: rows.append([p('Recargo por mora / Late fee'),p(f'${late_fee:,.2f}',11,bold=True,align=TA_RIGHT)])
    rows.append([p('TOTAL PAGADO' if paid else 'TOTAL A PAGAR',10,bold=True),p(f'${total:,.2f}',16,red,True,TA_RIGHT)])
    details=table(rows,[364,160])
    details.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(ink)),('LINEBELOW',(0,1),(-1,-2),.5,colors.HexColor('#DCE2E7')),('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#F2F5F7'))]))
    elements += [details,Spacer(1,20)]
    elements.append(table([[[p('FECHA DE PAGO' if paid else 'FECHA DE VENCIMIENTO',8,muted,True),p(date or 'No registrada',10)],
                           [p('MÉTODO DE PAGO' if paid else 'ESTADO DEL DOCUMENTO',8,muted,True),p(method if paid else 'Pendiente de pago',10)]]],[262,262],padding=0))
    note = ('Pago confirmado manualmente por la administración. Payment manually confirmed by management.'
            if paid and payment.get('confirmation_source') == 'admin_manual'
            else 'Este recibo acredita el pago registrado.' if paid
            else 'Este documento es una solicitud de pago. No acredita un pago recibido.')
    elements += [Spacer(1,16),p(note,9,muted),Spacer(1,18)]
    reference=invoice_reference(payment)
    if reference:
        qr=createBarcodeDrawing('QR',value=reference,width=86,height=86,barBorder=4)
        elements.append(table([[qr,[p('CONSULTA EN OFICINA',9,bold=True),
            p('Escanea el QR en el buscador de pagos del panel administrativo.',9,muted),
            p(reference,9,bold=True),
            p('La consulta no registra ni repite un cobro.',8,muted)]]],[110,414],'#F7F8FA',10))
    elements += [Spacer(1,20),p(company.get('address',''),8,muted),
                 p(' · '.join(str(company.get(k,'')) for k in ('phone','email')),8,muted)]
    doc.build(elements)
    return base64.b64encode(out.getvalue()).decode()
