"""Transactional purchase email outbox. No network from checkout requests."""
import asyncio
import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from html import escape

from rental.background_job_policy import should_disable_background_jobs
from rental.notification_identity import push_recipient
from rental.security_email import _transactional_sendgrid_config
from rental.store_receipts import receipt_photos, receipt_payload
from rental.store_receipt_design import LOGO, APP_SEAL

KEY = 'resident-store-v1'
INACTIVE = {'deleted', 'inactive', 'disabled', 'suspended'}


def now():
    return datetime.now(timezone.utc)


async def enqueue(db, event):
    # Explicit marker prevents retroactive mail from existing audit/events.
    if not event.get('email_notice') or event['status_code'] not in ('received', 'paid'):
        return
    from rental.store_notifications import insert_once
    await insert_once(db.store_email_deliveries, event['_id'], {
        'order_id': event['order_id'], 'user_id': event['user_id'],
        'kind': event['status_code'], 'created_at': event['created_at'],
        'status': 'suppressed_environment' if should_disable_background_jobs() else 'pending',
        'updated_at': now(),
    })


def build_message(order, kind, language, photos=None):
    es = language == 'es'
    tr = lambda a, b: a if es else b
    e = lambda value: escape(str(value or ''), quote=True)
    money = lambda n: f"${n / 100:,.2f}"
    paid = kind == 'paid'
    number = order['receipt']['number'] if paid else '#' + order['id'][:8]
    title = tr('Tu recibo de compra', 'Your purchase receipt') if paid else tr('Recibimos tu pedido', 'We received your order')
    intro = tr('Tu pago está registrado. Adjuntamos tu recibo de compra.', 'Your payment is recorded. Your purchase receipt is attached.') if paid else tr('Estamos coordinando tu entrega a domicilio. El pago se realiza al recibir.', 'We are arranging your home delivery. Payment is due upon receipt.')
    attachments = [
        {'content': base64.b64encode(LOGO.read_bytes()).decode(), 'type': 'image/png', 'filename': 'ross-house.png', 'disposition': 'inline', 'content_id': 'ross-brand'},
        {'content': base64.b64encode(APP_SEAL.read_bytes()).decode(), 'type': 'image/png', 'filename': 'ross-seal.png', 'disposition': 'inline', 'content_id': 'ross-seal'},
    ]
    rows, plain = [], []
    attached = set()
    for item in order['items']:
        name = re.sub(r'^DEMO\s*[·:—-]\s*', '', (item.get('name') if es else item.get('name_en')) or item['name'], flags=re.I)
        name = ' · '.join(str(x) for x in (name, item.get('size'), item.get('color')) if x)
        image = (photos or {}).get(item['product_id'])
        cid = 'product-' + hashlib.sha256(item['product_id'].encode()).hexdigest()[:20]
        if image and cid not in attached:
            attachments.append({'content': base64.b64encode(image).decode(), 'type': 'image/jpeg', 'filename': cid + '.jpg', 'disposition': 'inline', 'content_id': cid})
            attached.add(cid)
        thumb = f'<img src="cid:{cid}" width="64" height="64" style="object-fit:contain;border-radius:8px" alt="{e(name)}">' if image else ''
        detail = tr('Cantidad', 'Quantity') + ': ' + str(item['quantity']) + ' · ' + money(item['price_cents'])
        rows.append(f'<tr><td width="76" style="padding:14px 0;border-bottom:1px solid #e2e8f0">{thumb}</td><td style="padding:14px 8px;border-bottom:1px solid #e2e8f0"><strong>{e(name)}</strong><br><span style="color:#526173;font-size:13px">{e(detail)}<br>{e(item.get("sku", ""))}</span></td><td align="right" style="padding:14px 0;border-bottom:1px solid #e2e8f0;white-space:nowrap">{money(item["subtotal_cents"])}</td></tr>')
        plain.append(f'{item["quantity"]} × {name} — {money(item["subtotal_cents"])}')
    total_label = tr('Total pagado', 'Total paid') if paid else tr('Total al recibir', 'Total due upon receipt')
    totals = [(tr('Subtotal','Subtotal'),order['subtotal_cents']), (tr('Entrega','Delivery'),order['delivery_fee_cents']), (tr('Impuestos','Tax'),order['tax_cents'])]
    summary = ''.join(f'<tr><td style="padding:5px 0">{label}</td><td align="right">{money(value)}</td></tr>' for label,value in totals)
    address = ' '.join(str(order.get(k) or '') for k in ('address','zip')).strip()
    thanks = tr('Gracias por ser parte de Ross House.', 'Thank you for being part of Ross House.')
    tracking = tr('Consulta el seguimiento en la app: Tienda → Pedidos.', 'Track your order in the app: Store → Orders.')
    html = f'''<!doctype html><html lang="{language}"><body style="margin:0;background:#f4f6f8;font-family:Arial,sans-serif;color:#17212b">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:620px;background:#fff;border-radius:18px"><tr><td style="padding:28px">
<img src="cid:ross-brand" alt="Ross House Rentals LLC" width="205" style="display:block;margin-bottom:24px">
<p style="color:#c8102e;font-size:12px;font-weight:bold;letter-spacing:1px">{e(number)}</p><h1 style="font-size:26px;margin:10px 0">{title}</h1>
<p>{tr('Hola','Hello')} {e(order.get('customer_name') or tr('cliente','there'))},</p><p style="color:#526173;line-height:1.6">{intro}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(rows)}</table>
<table role="presentation" width="100%" style="margin-top:20px">{summary}<tr style="background:#c8102e;color:#fff"><td style="padding:14px"><strong>{total_label}</strong></td><td align="right" style="padding:14px;font-size:21px"><strong>{money(order['total_cents'])}</strong></td></tr></table>
<p style="font-weight:bold;margin-top:24px">{tr('Entrega a domicilio','Home delivery')}</p><p style="color:#526173">{e(address)}</p><p style="color:#526173">{tracking}</p>
<table role="presentation" style="margin-top:24px"><tr><td><img src="cid:ross-seal" alt="" width="42" height="42"></td><td style="padding-left:12px;font-weight:bold">{thanks}</td></tr></table>
<p style="font-size:12px;color:#65717f;border-top:2px solid #c8102e;padding-top:16px;margin-top:24px">Ross House Rentals LLC · USD</p>
</td></tr></table></td></tr></table></body></html>'''
    text = '\n'.join([title + ' · ' + number, intro, *plain, *[f'{label}: {money(value)}' for label,value in totals], total_label + ': ' + money(order['total_cents']), address, tracking, thanks])
    return {'subject': title + ' · ' + number, 'html': html, 'text': text, 'attachments': attachments}


async def send_message(api_key, sender, recipient, content):
    def send():
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        message = Mail(from_email=(sender, 'Ross House Rentals'), to_emails=recipient,
                       subject=content['subject'], plain_text_content=content['text'], html_content=content['html']).get()
        message['attachments'] = content['attachments']
        return SendGridAPIClient(api_key).client.mail.send.post(request_body=message).status_code
    return await asyncio.to_thread(send)


async def deliver(db, delivery):
    async def finish(status, **extra):
        await db.store_email_deliveries.update_one({'_id': delivery['_id'], 'status': 'pending'}, {'$set': {'status': status, 'updated_at': now(), **extra}})
    if should_disable_background_jobs():
        await finish('suppressed_environment')
        return
    state = await db.resident_store.find_one({'_id': KEY}) or {}
    order = state.get('orders', {}).get(delivery['order_id'])
    if not state.get('settings', {}).get('email_notifications', True):
        await finish('disabled')
        return
    if not order or order['user_id'] != delivery['user_id']:
        await finish('ineligible')
        return
    collection, user = await push_recipient(db, delivery['user_id'])
    role = (user or {}).get('role', 'tenant' if collection == 'tenants' else '')
    if not user or role not in ('tenant', 'admin') or user.get('status') in INACTIVE or user.get('active') is False or user.get('is_active') is False:
        await finish('ineligible')
        return
    recipient = str(user.get('email') or '').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', recipient):
        await finish('no_email')
        return
    kind = delivery['kind']
    if kind == 'received' and (order['status'] in ('cancelled','delivered') or delivery['created_at'].replace(tzinfo=timezone.utc) < now() - timedelta(days=1)):
        await finish('superseded')
        return
    if kind == 'paid' and (order['payment_status'] != 'paid' or not order.get('receipt')):
        await finish('ineligible')
        return
    api_key, sender = await _transactional_sendgrid_config(db)
    if not api_key:
        await finish('pending', retry_at=now() + timedelta(minutes=15), last_error='configuration_missing')
        return
    language = order.get('language') or ('en' if str(user.get('language') or user.get('preferred_language') or '').startswith('en') else 'es')
    try:
        content = build_message(order, kind, language, await receipt_photos(db, order))
        if kind == 'paid':
            pdf = await receipt_payload(db, order, language)
            content['attachments'].append({'content': pdf['pdf_base64'], 'type': 'application/pdf', 'filename': pdf['filename'], 'disposition': 'attachment'})
    except Exception:
        await finish('pending', retry_at=now() + timedelta(minutes=5), last_error='document_unavailable')
        return
    # Recheck just before claim/send; requests and staging never bypass policy.
    if should_disable_background_jobs():
        await finish('suppressed_environment')
        return
    claimed = await db.store_email_deliveries.update_one({'_id': delivery['_id'], 'status': 'pending'}, {'$set': {'status': 'sending', 'started_at': now(), 'language': language, 'updated_at': now()}})
    if not claimed.modified_count:
        return
    try:
        code = await send_message(api_key, sender, recipient, content)
        outcome = {'status': 'accepted' if 200 <= code < 300 else 'failed', 'provider_status': int(code)}
    except Exception:
        # SMTP/API acceptance can be ambiguous; do not duplicate a receipt.
        outcome = {'status': 'uncertain', 'last_error': 'provider_result_unknown'}
    await db.store_email_deliveries.update_one({'_id': delivery['_id'], 'status': 'sending'}, {'$set': {**outcome, 'updated_at': now()}})


async def drain(db):
    await db.store_email_deliveries.update_many({'status': 'sending', 'started_at': {'$lt': now() - timedelta(hours=1)}}, {'$set': {'status': 'uncertain', 'last_error': 'worker_interrupted', 'updated_at': now()}})
    async for delivery in db.store_email_deliveries.find({'status': 'pending', '$or': [{'retry_at': {'$exists': False}}, {'retry_at': {'$lte': now()}}]}).sort('created_at', 1).limit(25):
        await deliver(db, delivery)
