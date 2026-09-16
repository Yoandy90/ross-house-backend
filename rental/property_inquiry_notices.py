"""Durable inquiry inbox and one-attempt outbound delivery, respecting staging policy."""
import hashlib
from datetime import datetime, timedelta, timezone
from html import escape
from bson import ObjectId
from rental.background_job_policy import should_disable_background_jobs
from rental.store_notifications import insert_once
from rental.notification_identity import push_recipient

COPY = {
 'new': ('Solicitud recibida', 'Recibimos tu solicitud. Puedes consultar su estado en Mis solicitudes.', 'Request received', 'We received your request. Check its status in My requests.'),
 'following_up': ('Solicitud en seguimiento', 'Estamos revisando tu solicitud. Consulta los detalles en Mis solicitudes.', 'Request in progress', 'We are reviewing your request. Check My requests for details.'),
 'visit_confirmed': ('Visita confirmada', 'Tu visita tiene fecha y hora confirmadas. Revisa los detalles en Mis solicitudes.', 'Visit confirmed', 'Your visit date and time are confirmed. Check My requests for details.'),
 'resolved': ('Solicitud resuelta', 'Tu solicitud fue resuelta. Consulta la respuesta en Mis solicitudes.', 'Request resolved', 'Your request was resolved. Check the response in My requests.'),
 'cancelled': ('Solicitud cancelada', 'Tu solicitud fue cancelada. Consulta los detalles en Mis solicitudes.', 'Request cancelled', 'Your request was cancelled. Check My requests for details.'),
}
def now(): return datetime.now(timezone.utc)

async def materialize(db, doc, event):
    key = f"inquiry:{doc['_id']}:{event['version']}"
    copy = COPY[event['status']]
    for audience in (['admin', 'user'] if doc.get('user_id') else ['admin']):
        if audience == 'admin' and event['status'] != 'new': continue
        nid = ObjectId(hashlib.sha256((key + ':' + audience).encode()).hexdigest()[:24])
        text = ('Nueva solicitud de propiedad', 'Revisa las consultas de propiedades.', 'New property request', 'Review property inquiries.') if audience == 'admin' else copy
        await insert_once(db.rental_notifications, nid, {
            'title': text[0], 'body': text[1], 'title_en': text[2], 'body_en': text[3],
            'type': 'property_inquiry', 'data': {'type': 'property_inquiry', 'inquiry_id': str(doc['_id']), 'notification_id': str(nid)},
            'created_at': event['at'], 'read_by': [],
            **({'target': 'admin'} if audience == 'admin' else {'user_id': doc['user_id']}),
        })
    # Send only to the verified account, never to an anonymous form's email.
    if doc.get('user_id'):
        for channel in ('email', 'push'):
            await insert_once(db.property_inquiry_deliveries, key + ':' + channel, {
                'inquiry_id': doc['_id'], 'user_id': doc['user_id'], 'version': event['version'],
                'copy': copy, 'channel': channel, 'language': doc.get('language', 'es'),
                'created_at': event['at'], 'status': 'suppressed_environment' if should_disable_background_jobs() else 'pending',
            })

async def deliver(db, item):
    col = db.property_inquiry_deliveries
    if should_disable_background_jobs():
        await col.update_one({'_id': item['_id'], 'status': 'pending'}, {'$set': {'status': 'suppressed_environment'}})
        return
    doc = await db.property_inquiries.find_one({'_id': item['inquiry_id'], 'user_id': item['user_id']})
    _, user = await push_recipient(db, item['user_id'])
    if not doc or not user or user.get('status') in ('deleted', 'inactive', 'disabled', 'suspended') or user.get('active') is False or user.get('is_active') is False or doc.get('version') != item['version']:
        await col.update_one({'_id': item['_id'], 'status': 'pending'}, {'$set': {'status': 'superseded'}})
        return
    token = user.get('push_token') or user.get('expo_push_token')
    if item['channel'] == 'email':
        from rental.security_email import _transactional_sendgrid_config
        api_key, sender = await _transactional_sendgrid_config(db)
        if not api_key:
            await col.update_one({'_id': item['_id'], 'status': 'pending'}, {'$set': {'retry_at': now() + timedelta(minutes=15)}})
            return
        import re
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', user.get('email') or ''):
            await col.update_one({'_id': item['_id'], 'status': 'pending'}, {'$set': {'status': 'no_email'}})
            return
    elif not token:
        await col.update_one({'_id': item['_id'], 'status': 'pending'}, {'$set': {'status': 'no_device'}})
        return
    if should_disable_background_jobs(): return
    claim = await col.update_one({'_id': item['_id'], 'status': 'pending'}, {'$set': {'status': 'sending', 'started_at': now()}})
    if not claim.modified_count: return
    en = item.get('language') == 'en'; copy = item['copy']; title, body = copy[2:4] if en else copy[:2]
    try:
        if item['channel'] == 'push':
            from rental.notification_center import expo_send
            outcome = await expo_send(token, title, body, {'type': 'property_inquiry', 'inquiry_id': str(doc['_id'])})
            status = outcome.get('status', 'uncertain')
        else:
            from rental.store_email import send_message
            content = {'subject': title, 'text': body + '\nRoss House Rentals LLC', 'attachments': [],
                       'html': f'<div style="font-family:Arial;color:#17212b;max-width:600px;padding:28px;border-top:4px solid #c8102e"><h2>Ross House Rentals</h2><h1>{escape(title)}</h1><p>{escape(body)}</p></div>'}
            result = await send_message(api_key, sender, user['email'], content)
            status = 'accepted' if 200 <= result < 300 else 'failed'
    except Exception:
        status = 'uncertain'  # Do not retry an ambiguous provider acceptance.
    await col.update_one({'_id': item['_id'], 'status': 'sending'}, {'$set': {'status': status, 'updated_at': now()}})

async def drain(db):
    if should_disable_background_jobs(): return
    query = {'events.0': {'$exists': True}, '$expr': {'$lt': [{'$ifNull': ['$notice_version', 0]}, '$version']}}
    async for doc in db.property_inquiries.find(query).limit(50):
        for event in doc.get('events', []): await materialize(db, doc, event)
        await db.property_inquiries.update_one({'_id': doc['_id'], 'version': doc['version']}, {'$set': {'notice_version': doc['version']}})
    await db.property_inquiry_deliveries.update_many({'status': 'sending', 'started_at': {'$lt': now() - timedelta(hours=1)}}, {'$set': {'status': 'uncertain'}})
    async for item in db.property_inquiry_deliveries.find({'status': 'pending', '$or': [{'retry_at': {'$exists': False}}, {'retry_at': {'$lte': now()}}]}).limit(50): await deliver(db, item)
