"""Private purchase receipts, independent of rental invoices and ledgers."""
import base64
import hashlib
import io
import re
from pathlib import Path

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


def render_receipt(order, language='es', assets=None):
    from rental.store_receipt_design import render
    return render(order, language, assets)


async def receipt_photos(db, order):
    """Use immutable uploaded images, never fetch remote or customer URLs."""
    from urllib.parse import urlsplit
    from PIL import Image, ImageOps
    legacy = any('image_url' not in item for item in order['items'])
    catalog = await db.resident_store.find_one({'_id': 'resident-store-v1'}, {'products': 1}) if legacy else None
    assets = {}
    for item in order['items']:
        url = item.get('image_url') if 'image_url' in item else (catalog or {}).get('products', {}).get(item['product_id'], {}).get('image_url', '')
        try:
            path = urlsplit(url or '').path
        except ValueError:
            continue
        match = re.fullmatch(r'/api/public/store-images/([a-f0-9]{64})', path)
        if not match: continue
        stored = await db.resident_store_images.find_one({'_id': match[1]})
        if not stored or not isinstance(stored.get('data'), str) or len(stored['data']) > 4_000_000: continue
        try:
            raw = base64.b64decode(stored['data'], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 4_000_000: continue
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((240,240))
                out = io.BytesIO(); image.save(out,format='JPEG',quality=88)
                assets[item['product_id']] = out.getvalue()
        except (ValueError, OSError, Image.DecompressionBombError):
            continue
    return assets


async def receipt_payload(db, order, language):
    """Persist once; subsequent downloads return the same bytes, not a new receipt."""
    key = order['id'] + ':' + language + ':v2'
    stored = await db.store_receipt_files.find_one({'_id': key})
    if not stored:
        assets = await receipt_photos(db, order)
        data = await run_in_threadpool(render_receipt, order, language, assets)
        filename = order['receipt']['number'] + '-' + language + '.pdf'
        try:
            await db.store_receipt_files.update_one({'_id': key}, {'$setOnInsert': {
                'order_id': order['id'], 'user_id': order['user_id'], 'filename': filename,
                'pdf': data, 'sha256': hashlib.sha256(data).hexdigest(),
                'issued_at': order['receipt']['issued_at'], 'design_version': 2, 'language': language,
            }}, upsert=True)
        except DuplicateKeyError:
            pass
        stored = await db.store_receipt_files.find_one({'_id': key})
    return {'success': True, 'filename': stored['filename'],
            'pdf_base64': base64.b64encode(stored['pdf']).decode(), 'receipt_number': order['receipt']['number']}
