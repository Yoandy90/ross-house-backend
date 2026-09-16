"""Brand-owned receipt layout. Local image bytes only; rendering never fetches URLs."""
import io
import os
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Image
from reportlab.graphics.barcode import createBarcodeDrawing

LOGO = Path(__file__).resolve().parent.parent / 'assets' / 'ross_house_logo.png'
APP_SEAL = LOGO.with_name('ross_house_app_seal.png')
STAGING_ORIGIN = 'https://ross-house-rentals-git-staging-yoandyross-2350s-projects.vercel.app'
PRODUCTION_ORIGIN = 'https://www.rosshouserentals.com'


def order_lookup_url(order_id):
    if not re.fullmatch(r'[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}', str(order_id)):
        return None
    # Never derive printed URLs from Host or other request-controlled headers.
    origin = PRODUCTION_ORIGIN if os.environ.get('ENVIRONMENT', '').lower() == 'production' else STAGING_ORIGIN
    return origin + '/admin/tienda?order_id=' + order_id


def payment_date(value, es):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        from datetime import timezone
        if dt.tzinfo: dt = dt.astimezone(timezone.utc)
        months = ('ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic') if es else ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
        return f'{dt.day:02d} {months[dt.month-1]} {dt.year} · {dt:%H:%M} UTC'
    except (ValueError, TypeError):
        return str(value)[:40]


def render(order, language='es', assets=None):
    es = language == 'es'; tr = lambda a,b: a if es else b
    assets = assets or {}
    ink, muted, red, soft, border = map(colors.HexColor, ('#17212B','#65717F','#C8102E','#F5F6F8','#E2E6EB'))
    money = lambda n: f'${n / 100:,.2f}'
    def p(value, size=10, bold=False, color=ink, align=0):
        return Paragraph(escape(str(value)), ParagraphStyle('receipt', fontName='StoreSansBold' if bold else 'StoreSans', fontSize=size, leading=size*1.4, textColor=color, alignment=align, spaceAfter=3))
    def table(rows, widths, background=None, padding=0):
        t = Table(rows, colWidths=widths, hAlign='LEFT')
        st = [('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),padding),('RIGHTPADDING',(0,0),(-1,-1),padding),('TOPPADDING',(0,0),(-1,-1),padding),('BOTTOMPADDING',(0,0),(-1,-1),padding)]
        if background: st.append(('BACKGROUND',(0,0),(-1,-1),background))
        t.setStyle(TableStyle(st)); return t
    def picture(data, width, height):
        source = io.BytesIO(data) if isinstance(data, bytes) else str(data)
        return Image(source, width=width, height=height, kind='proportional', hAlign='LEFT')
    meta = order['receipt']; out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=(612,792), leftMargin=40, rightMargin=40, topMargin=32, bottomMargin=48, title=meta['number'], author=meta['merchant'])
    logo = picture(LOGO, 205, 88)
    flow = [table([[logo, [p(tr('Recibo de compra','Purchase receipt'),18,True,align=TA_RIGHT),p(meta['number'],9,color=muted,align=TA_RIGHT),Spacer(1,8),p(tr('PAGADO','PAID'),10,True,red,TA_RIGHT)]]],[252,280]),Spacer(1,10)]
    greeting = [p(tr('Gracias por tu compra.','Thank you for your purchase.'),19,True),p(tr('Lo esencial, entregado en casa.','Everyday essentials, delivered to your door.'),10,color=muted)]
    flow += [table([[greeting]],[532],soft,12),Spacer(1,12)]
    left = [p(tr('CLIENTE','CUSTOMER'),8,True,muted),p(order.get('customer_name') or tr('Cliente','Customer'),11,True),p(tr('Pedido: ','Order: ') + order['id'],8,color=muted)]
    right = [p(tr('PAGO RECIBIDO','PAYMENT RECEIVED'),8,True,muted),p(payment_date(meta['issued_at'],es),10),p(tr('Referencia: ','Reference: ') + str(order.get('payment_reference','')),9,color=muted)]
    flow += [table([[left,right]],[292,240]),Spacer(1,12)]
    if order.get('address'):
        flow += [p(tr('DIRECCIÓN DE ENTREGA','DELIVERY ADDRESS') if order.get('fulfillment')=='delivery' else tr('PUNTO DE RECOGIDA','PICKUP LOCATION'),8,True,muted),p(' '.join(str(order.get(k) or '') for k in ('address','zip')).strip(),10),Spacer(1,12)]
    headers = ['',tr('PRODUCTO','PRODUCT'),tr('CANT.','QTY.'),tr('PRECIO UNIT.','UNIT PRICE'),tr('IMPORTE','AMOUNT')]
    rows = [[p(label,7.5,True,muted,TA_RIGHT if i>1 else 0) for i,label in enumerate(headers)]]
    for line in order['items']:
        name = (line.get('name') if es else line.get('name_en')) or line['name']
        name = re.sub(r'^DEMO\s*[·:—-]\s*','',name,flags=re.I)
        details = [p(name,10,True)]
        variant = ' · '.join(str(line.get(k) or '') for k in ('size','color') if line.get(k))
        if variant: details.append(p(variant,8,color=muted))
        if line.get('sku'): details.append(p('SKU: '+line['sku'],7,color=muted))
        unit = line.get('sale_unit')
        if unit and unit not in ('unit','pack'):
            details.append(p(str(line.get('pack_size') or '1')+' '+unit,8,color=muted))
        image = assets.get(line['product_id'])
        thumb = picture(image,52,52) if image else table([[p(tr('Sin foto','No photo'),7,color=muted)]],[58],soft,8)
        rows.append([thumb,details,p(line['quantity'],10,align=TA_RIGHT),p(money(line['price_cents']),10,align=TA_RIGHT),p(money(line['subtotal_cents']),10,True,align=TA_RIGHT)])
    products = Table(rows,colWidths=[76,214,42,92,108],repeatRows=1,hAlign='LEFT')
    products.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),soft),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,0),1,red),('LINEBELOW',(0,1),(-1,-1),.4,border)]))
    flow += [products,Spacer(1,12)]
    totals = [[p(label,10,color=muted),p(money(value),11,align=TA_RIGHT)] for label,value in [(tr('Subtotal','Subtotal'),order['subtotal_cents']),(tr('Entrega','Delivery'),order['delivery_fee_cents']),(tr('Impuestos','Tax'),order['tax_cents'])]]
    totals += [[p(tr('TOTAL PAGADO','TOTAL PAID'),11,True,color=colors.white),p(money(order['total_cents']),17,True,colors.white,TA_RIGHT)]]
    total = table(totals,[162,154],padding=6)
    total.setStyle(TableStyle([('BACKGROUND',(0,-1),(-1,-1),red),('VALIGN',(0,-1),(-1,-1),'MIDDLE')]))
    link = order_lookup_url(order['id'])
    lookup = [createBarcodeDrawing('QR',value=link,width=106,height=106,barBorder=4),p(tr('Consulta administrativa','Staff order lookup'),8,True),p(tr('Escanear requiere iniciar sesión.','Scanning requires sign-in.'),7,color=muted)] if link else []
    closing = table([[picture(APP_SEAL,40,40),[
        p(tr('Gracias por ser parte de Ross House.','Thank you for being part of Ross House.'),10,True),
        p(tr('Conserva este recibo como comprobante de tu compra.','Keep this receipt as proof of your purchase.'),8,color=muted),
    ]]],[54,478])
    closing.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    flow += [KeepTogether([table([[lookup,total]],[216,316]),Spacer(1,14),closing])]
    def footer(canvas, document):
        canvas.saveState(); canvas.setStrokeColor(red); canvas.setLineWidth(2); canvas.line(40,34,572,34)
        canvas.setFont('StoreSans',7.5); canvas.setFillColor(muted)
        canvas.drawString(40,21,meta['merchant']+' · '+meta['number']+' · USD')
        canvas.drawRightString(572,21,tr('Página ','Page ')+str(document.page))
        if document.page>1:
            canvas.setFont('StoreSansBold',8);canvas.drawString(40,776,tr('Detalle de compra','Purchase details')+' · '+meta['number'])
        canvas.restoreState()
    doc.build(flow,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
