"""Private purchase receipts, independent of rental invoices and ledgers."""
import base64
import hashlib
import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Embed fonts so receipt spacing does not depend on the viewer's substitutions.
_fonts = Path(reportlab.__file__).parent / 'fonts'
pdfmetrics.registerFont(TTFont('StoreSans', str(_fonts / 'Vera.ttf')))
pdfmetrics.registerFont(TTFont('StoreSansBold', str(_fonts / 'VeraBd.ttf')))


def issue_receipt(state, order):
    if order.get('receipt'):
        return
    state['receipt_sequence'] = state.get('receipt_sequence', 0) + 1
    order['receipt'] = {
        'number': f"RHT-{order['paid_at'][:4]}-{state['receipt_sequence']:06d}",
        'issued_at': order['paid_at'], 'version': 1,
        'merchant': 'Ross House Rentals LLC',
    }


def render_receipt(order, language='es'):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    es = language == 'es'
    tr = lambda a, b: a if es else b
    money = lambda cents: f'${cents / 100:,.2f}'
    ink, muted, red = colors.HexColor('#16202B'), colors.HexColor('#586575'), colors.HexColor('#C8102E')
    body = ParagraphStyle('body', fontName='StoreSans', fontSize=10, leading=15, textColor=ink)
    small = ParagraphStyle('small', parent=body, fontSize=9, leading=13, textColor=muted)
    heading = ParagraphStyle('heading', parent=body, fontName='StoreSansBold', fontSize=25, leading=31)
    right = ParagraphStyle('right', parent=body, alignment=TA_RIGHT)
    p = lambda value, style=body: Paragraph(escape(str(value)), style)
    meta = order['receipt']
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=44, rightMargin=44, topMargin=42, bottomMargin=46,
                            title=meta['number'], author=meta['merchant'])
    flow = [p(meta['merchant'], small), Spacer(1, 18), p(tr('Recibo de compra', 'Purchase receipt'), heading),
            Spacer(1, 10), p(meta['number']), Spacer(1, 8),
            p(tr('PAGADO', 'PAID'), ParagraphStyle('paid', parent=body, textColor=red, fontName='StoreSansBold')),
            Spacer(1, 22)]
    info = [
        [p(tr('Cliente', 'Customer'), small), p(order.get('customer_name') or tr('Cliente', 'Customer'))],
        [p(tr('Fecha de pago (UTC)', 'Payment date (UTC)'), small), p(meta['issued_at'].replace('T', ' ')[:19])],
        [p(tr('Pedido', 'Order'), small), p(order['id'])],
        [p(tr('Referencia', 'Reference'), small), p(order.get('payment_reference', ''))],
    ]
    table = Table(info, colWidths=[145, 379], hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    flow += [table, Spacer(1, 20)]
    headers = [tr('Producto', 'Product'), tr('Cant.', 'Qty.'), tr('Precio', 'Price'), tr('Importe', 'Amount')]
    rows = [[p(h, small) for h in headers]]
    for line in order['items']:
        name = (line.get('name') if es else line.get('name_en')) or line['name']
        name = ' · '.join(str(v) for v in (name, line.get('size'), line.get('color'), line.get('sku')) if v)
        name = re.sub(r'^DEMO\s*[·:—-]\s*', '', name, flags=re.I)
        rows.append([p(name), p(line['quantity'], right), p(money(line['price_cents']), right), p(money(line['subtotal_cents']), right)])
    table = Table(rows, colWidths=[286, 44, 94, 100], repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F2F4F7')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 11), ('BOTTOMPADDING', (0, 0), (-1, -1), 11),
        ('LINEBELOW', (0, 0), (-1, -1), .4, colors.HexColor('#E3E7EC')),
    ]))
    flow += [table, Spacer(1, 18)]
    totals = [[p(label, small), p(money(value), right)] for label, value in [
        (tr('Subtotal', 'Subtotal'), order['subtotal_cents']),
        (tr('Entrega', 'Delivery'), order['delivery_fee_cents']),
        (tr('Impuestos', 'Tax'), order['tax_cents']),
        (tr('Total pagado', 'Total paid'), order['total_cents']),
    ]]
    total = Table(totals, colWidths=[380, 144], hAlign='LEFT')
    total.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                               ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F2F4F7'))]))
    flow.append(KeepTogether([total, Spacer(1, 18), p(tr('Gracias por tu compra.', 'Thank you for your purchase.'), small)]))
    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(red)
        canvas.setLineWidth(2)
        canvas.line(44, 32, 568, 32)
        canvas.setFont('StoreSans', 8)
        canvas.setFillColor(muted)
        canvas.drawString(44, 20, meta['number'])
        canvas.drawRightString(568, 20, str(document.page))
        canvas.restoreState()
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


async def receipt_payload(db, order, language):
    """Persist once; subsequent downloads return the same bytes, not a new receipt."""
    key = order['id'] + ':' + language + ':v1'
    stored = await db.store_receipt_files.find_one({'_id': key})
    if not stored:
        data = await run_in_threadpool(render_receipt, order, language)
        filename = order['receipt']['number'] + '-' + language + '.pdf'
        try:
            await db.store_receipt_files.update_one({'_id': key}, {'$setOnInsert': {
                'order_id': order['id'], 'user_id': order['user_id'], 'filename': filename,
                'pdf': data, 'sha256': hashlib.sha256(data).hexdigest(),
                'issued_at': order['receipt']['issued_at'],
            }}, upsert=True)
        except DuplicateKeyError:
            pass
        stored = await db.store_receipt_files.find_one({'_id': key})
    return {'success': True, 'filename': stored['filename'],
            'pdf_base64': base64.b64encode(stored['pdf']).decode(), 'receipt_number': order['receipt']['number']}
