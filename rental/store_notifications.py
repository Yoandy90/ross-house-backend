"""Store audit outbox: durable inbox notices, separately claimed push attempts.

Only audit entries explicitly marked by new store transactions are processed.
Request handlers materialize the inbox only; outbound sends belong to the worker
and always honor the environment's background-job policy.
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from rental.background_job_policy import should_disable_background_jobs
from rental.notification_identity import push_recipient

log = logging.getLogger(__name__)
STORE_KEY = 'resident-store-v1'
CURSOR_KEY = 'source:' + STORE_KEY
INACTIVE = {'deleted', 'inactive', 'disabled', 'suspended'}
COPY = {
    'out_for_delivery': ('Tu pedido va en camino', 'Consulta la hora estimada de llegada en Pedidos.', 'Your order is on its way', 'Check the estimated arrival time in Orders.'),
    'handoff': ('Entrega confirmada', 'El repartidor confirmó la entrega de tu pedido.', 'Delivery confirmed', 'Your courier confirmed your order was delivered.'),
    'paid': ('Pago recibido', 'Tu pago de la tienda fue registrado. Tu recibo está disponible en Pedidos.',
             'Payment received', 'Your store payment was recorded. Your receipt is available in Orders.'),
    'received': ('Pedido recibido', 'Recibimos tu pedido. Te avisaremos cuando esté listo.',
                 'Order received', 'We received your order. We will let you know when it is ready.'),
    'preparing': ('Estamos preparando tu pedido', 'Tu pedido de la tienda está en preparación.',
                  'Preparing your order', 'Your store order is being prepared.'),
    'ready': ('Tu pedido está listo', 'Consulta los detalles y el seguimiento en Pedidos.',
              'Your order is ready', 'Check details and tracking in Orders.'),
    'delivered': ('Pedido entregado', 'Tu pedido fue entregado. Puedes consultar tu recibo en Pedidos.',
                  'Order delivered', 'Your order was delivered. You can view your receipt in Orders.'),
    'cancelled': ('Pedido cancelado', 'Tu pedido fue cancelado. No hay ningún pago pendiente por este pedido.',
                  'Order cancelled', 'Your order was cancelled. No payment is due for this order.'),
}


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def insert_once(collection, key, values):
    try:
        await collection.update_one({'_id': key}, {'$setOnInsert': values}, upsert=True)
    except DuplicateKeyError:
        # Concurrent workers can race on an upsert; the winner owns the same ID.
        pass


async def ingest(db):
    checkpoint = await db.store_notification_events.find_one({'_id': CURSOR_KEY}) or {}
    start = checkpoint.get('next', 0)
    state = await db.resident_store.find_one(
        {'_id': STORE_KEY}, {'audit.store_notice': 1, 'audit.at': 1}) or {}
    entries = state.get('audit', [])
    count = 0
    end = start
    for index in range(start, len(entries)):
        entry = entries[index]
        notice = entry.get('store_notice')
        if notice:
            event_id = digest('store:' + notice['order_id'] + ':' + notice['status'])
            await insert_once(db.store_notification_events, event_id, {
                **notice, 'created_at': datetime.fromisoformat(entry['at']),
                'status_code': notice['status'], 'status': 'pending',
            })
            count += 1
        end = index + 1
        if count >= 50:
            break
    # Advance only after all preceding events are durable; $max cannot rewind.
    if end > start:
        await db.store_notification_events.update_one(
            {'_id': CURSOR_KEY}, {'$max': {'next': end}}, upsert=True)


async def materialize(db, event, admin=False):
    audience = 'admin' if admin else 'user:' + event['user_id']
    nid = ObjectId(digest(event['_id'] + ':' + audience)[:24])
    if admin:
        copy = ('Nuevo pedido en la tienda', 'Revisa Pedidos en el panel de administración.',
                'New store order', 'Review Orders in the administration panel.')
    else:
        copy = COPY[event['status_code']]
    data = {'type': 'store_order_new' if admin else 'store_order_status',
            'order_id': event['order_id'], 'status': event['status_code'],
            'notification_id': str(nid)}
    await insert_once(db.rental_notifications, nid, {
        'title': copy[0], 'body': copy[1], 'title_en': copy[2], 'body_en': copy[3],
        'type': data['type'], 'data': data, 'read_by': [],
        'created_at': event['created_at'],
        **({'target': 'admin'} if admin else {'user_id': event['user_id']}),
    })
    if admin:
        recipients = [str(u['_id']) async for u in db.app_users.find(
            {'role': 'admin', 'status': {'$nin': list(INACTIVE)}, 'is_active': {'$ne': False}},
            {'_id': 1})]
    else:
        recipients = [event['user_id']]
    for uid in recipients:
        # Namespace campaign_id so the existing receipt worker/index can be used.
        campaign_id = 'store:' + str(nid)
        await insert_once(db.push_deliveries, campaign_id + ':' + uid, {
            'campaign_id': campaign_id, 'user_id': uid, 'store_event_id': event['_id'],
            'store_admin': admin, 'data': data, 'copy': list(copy),
            'status': 'suppressed_environment' if should_disable_background_jobs() else 'store_pending',
            'created_at': event['created_at'], 'updated_at': now(),
        })


async def sync_inbox(db):
    await ingest(db)
    async for event in db.store_notification_events.find({'status': 'pending'}).sort('created_at', 1).limit(50):
        await materialize(db, event)
        if event['status_code'] == 'received':
            await materialize(db, event, admin=True)
        from rental.store_email import enqueue
        await enqueue(db, event)
        await db.store_notification_events.update_one(
            {'_id': event['_id'], 'status': 'pending'}, {'$set': {'status': 'materialized'}})


async def sync_inbox_safely(db):
    try:
        await asyncio.wait_for(sync_inbox(db), timeout=2)
    except Exception as exc:
        log.warning('Store inbox deferred (%s)', type(exc).__name__)


async def deliver(db, delivery):
    # Recheck the kill switch immediately before any external operation.
    if should_disable_background_jobs():
        await db.push_deliveries.update_one(
            {'_id': delivery['_id'], 'status': 'store_pending'},
            {'$set': {'status': 'suppressed_environment', 'updated_at': now()}})
        return
    collection, user = await push_recipient(db, delivery['user_id'])
    eligible = user and user.get('status') not in INACTIVE and user.get('is_active') is not False
    role = (user or {}).get('role', 'tenant' if collection == 'tenants' else '')
    eligible = eligible and (role == 'admin' if delivery['store_admin'] else role in ('tenant', 'admin'))
    token = (user or {}).get('push_token') or (user or {}).get('expo_push_token') or ''
    from push_notification_service import is_expo_token
    outcome = None
    if not eligible:
        outcome = {'status': 'ineligible'}
    elif not is_expo_token(token):
        outcome = {'status': 'no_device'}
    else:
        oid = delivery['data']['order_id']
        field = 'payment_status' if delivery['data']['status'] == 'paid' else 'status'
        state = await db.resident_store.find_one({'_id': STORE_KEY}, {f'orders.{oid}.{field}': 1, f'orders.{oid}.tracking.handoff_at': 1}) or {}
        order = state.get('orders', {}).get(oid, {})
        current = 'handoff' if delivery['data']['status'] == 'handoff' and order.get('tracking', {}).get('handoff_at') else order.get(field)
        created = delivery['created_at'].replace(tzinfo=timezone.utc)
        if current != delivery['data']['status'] or created < now() - timedelta(days=1):
            outcome = {'status': 'superseded'}
    if outcome:
        await db.push_deliveries.update_one(
            {'_id': delivery['_id'], 'status': 'store_pending'},
            {'$set': {**outcome, 'updated_at': now()}})
        return
    claimed = await db.push_deliveries.update_one(
        {'_id': delivery['_id'], 'status': 'store_pending'},
        {'$set': {'status': 'sending', 'started_at': now(),
                  'token_hash': digest(token), 'updated_at': now()}})
    if not claimed.modified_count:
        return
    copy = delivery['copy']
    english = str(user.get('language') or user.get('preferred_language') or '').startswith('en')
    from rental.notification_center import expo_send
    try:
        outcome = await expo_send(token, copy[2] if english else copy[0],
                                  copy[3] if english else copy[1], delivery['data'])
    except Exception:
        # The provider may have accepted the request. Never blindly resend.
        outcome = {'status': 'uncertain', 'error': 'provider_result_unknown'}
    if outcome.get('error') == 'DeviceNotRegistered':
        await db[collection].update_one({'_id': user['_id'], 'push_token': token}, {'$unset': {'push_token': ''}})
        await db[collection].update_one({'_id': user['_id'], 'expo_push_token': token}, {'$unset': {'expo_push_token': ''}})
    await db.push_deliveries.update_one(
        {'_id': delivery['_id'], 'status': 'sending'}, {'$set': {**outcome, 'updated_at': now()}})


async def drain(db):
    await sync_inbox(db)
    await db.push_deliveries.update_many(
        {'store_event_id': {'$exists': True}, 'status': 'sending',
         'started_at': {'$lt': now() - timedelta(hours=1)}},
        {'$set': {'status': 'uncertain', 'error': 'worker_interrupted', 'updated_at': now()}})
    async for delivery in db.push_deliveries.find({'status': 'store_pending'}).sort('created_at', 1).limit(50):
        await deliver(db, delivery)
    from rental.store_email import drain as drain_email
    await drain_email(db)


async def ensure_indexes(db):
    await db.store_notification_events.create_index([('status', 1), ('created_at', 1)])
    await db.push_deliveries.create_index([('status', 1), ('created_at', 1)])
    await db.store_email_deliveries.create_index([('status', 1), ('created_at', 1)])
