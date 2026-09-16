"""Order-scoped Helcim payments. A committed claim never expires or retries a charge.
Provider I/O stays outside the inventory CAS. No rental/autopay ledger writes.
"""
import hashlib
import os
from decimal import Decimal, InvalidOperation
from uuid import uuid4
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from rental import resident_store as s
from rental.helcim_vault_router import decrypt_provider_token

router = APIRouter()
BASE = 'https://api.helcim.com/v2'
AUTH_VERSION = 'store-purchase-v1'

async def configuration():
    from rental.payment_processors_core import get_active_processor
    processor, cfg = await get_active_processor()
    if processor != 'helcim' or not cfg.get('api_token'):
        raise HTTPException(409, 'store_payment_unavailable')
    environment = cfg.get('environment')
    if environment not in ('sandbox', 'production'):
        raise HTTPException(409, 'store_payment_unavailable')
    # An accidental production processor switch must never charge from staging.
    if os.getenv('ENVIRONMENT', '').lower() != 'production' and environment != 'sandbox':
        raise HTTPException(409, 'store_payment_sandbox_required')
    return {**cfg, 'fingerprint': hashlib.sha256(cfg['api_token'].encode()).hexdigest()}

async def method_owners(user):
    uid = s.identity(user)
    rows = await s.get_db().tenants.find({'app_user_id': uid}).limit(2).to_list(2)
    if len(rows) > 1:
        raise HTTPException(409, 'store_payment_identity_review')
    return [uid, *[str(row['_id']) for row in rows]]

async def methods_for(user):
    return await s.get_db().helcim_saved_methods.find({'tenant_id': {'$in': await method_owners(user)}}).limit(100).to_list(100)

def method_ready(m):
    if m.get('ready_for_payments') is False:
        return False
    return bool(m.get('card_token')) if m.get('type', 'card') == 'card' else bool(m.get('type') == 'ach' and m.get('customer_id') and m.get('bank_account_id'))

@router.get('/store/payment-options')
async def options(request: Request):
    user = await s.resident(request)
    state = await s.read_state()
    try:
        if not state['settings'].get('online_payments', True):
            raise HTTPException(409, 'store_payment_disabled')
        cfg = await configuration()
    except HTTPException as exc:
        return {'available': False, 'reason': exc.detail, 'methods': []}
    methods = await methods_for(user)
    return {'available': True, 'environment': cfg['environment'], 'authorization_version': AUTH_VERSION,
            'methods': [{'id': str(m['_id']), 'type': m.get('type', 'card'), 'brand': str(m.get('brand') or 'Helcim'),
                         'last4': str(m.get('last4') or '')[-4:], 'ready': method_ready(m) and (not m.get('environment') or m['environment'] == cfg['environment'])} for m in methods]}

async def prepare(user, body, state):
    if not body.payment_method_id:
        return None
    if body.payment_authorized is not True or body.payment_authorization_version != AUTH_VERSION:
        raise HTTPException(400, 'store_payment_authorization_required')
    if not state['settings'].get('online_payments', True):
        raise HTTPException(409, 'store_payment_disabled')
    cfg = await configuration()
    m = next((m for m in await methods_for(user) if str(m['_id']) == body.payment_method_id), None)
    if not m or not method_ready(m):
        raise HTTPException(409, 'store_payment_method_unavailable')
    # New vault records carry their environment; never mix sandbox/production tokens.
    if m.get('environment') and m['environment'] != cfg['environment']:
        raise HTTPException(409, 'store_payment_method_unavailable')
    return cfg, m

def attach(order, prepared):
    if not prepared:
        return
    cfg, method = prepared
    order.update(payment_method='helcim_' + method.get('type', 'card'), payment_status='processing')
    order['payment_attempt'] = {
        'id': str(uuid4()), 'status': 'processing', 'created_at': s.now(),
        'method_id': str(method['_id']), 'type': method.get('type', 'card'),
        'brand': str(method.get('brand') or 'Helcim'), 'last4': str(method.get('last4') or '')[-4:],
        'environment': cfg['environment'], 'credential_fingerprint': cfg['fingerprint'],
        'amount_cents': order['total_cents'], 'authorization_version': AUTH_VERSION,
        'authorized_by': order['user_id'], 'authorized_at': s.now(),
    }

def public_payment(order):
    a = order.get('payment_attempt') or {}
    r = order.get('refund_attempt') or {}
    return {'type': a.get('type'), 'brand': a.get('brand'), 'last4': a.get('last4'),
            'status': order.get('payment_status'), 'transaction_id': a.get('transaction_id'),
            'refund_status': r.get('status'), 'refund_cents': order.get('refunded_cents', 0)} if a else None

async def provider_request(cfg, method, path, *, body=None, key=None):
    headers = {'api-token': cfg['api_token'], 'accept': 'application/json'}
    if key:
        headers['idempotency-key'] = key
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, BASE + path, headers=headers, json=body)
    # Error bodies can contain financial secrets. Never return or log them.
    if response.status_code >= 400:
        raise RuntimeError('provider_response_unconfirmed')
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError('provider_response_unconfirmed')
    return value.get('transaction') if isinstance(value.get('transaction'), dict) else value

def transaction_id(tx):
    value = str(tx.get('transactionId') or tx.get('id') or '')
    return value if value.isascii() and value.isdigit() else ''

def outcome(tx, attempt, *, refresh=False, refund=False):
    try:
        amount = Decimal(str(tx['amount'])) * 100
        if not amount.is_finite() or amount != attempt['amount_cents']:
            return 'review_required'
    except (KeyError, InvalidOperation, TypeError):
        return 'review_required'
    if str(tx.get('currency') or '').upper() != 'USD' or not transaction_id(tx):
        return 'review_required'
    if refund and (transaction_id(tx) == attempt.get('original_transaction_id') or
                   (tx.get('originalTransactionId') is not None and str(tx['originalTransactionId']) != attempt.get('original_transaction_id'))):
        return 'review_required'
    if tx.get('test') is True and attempt['environment'] == 'production':
        return 'review_required'
    if attempt['type'] == 'ach':
        clearing = str(tx.get('statusClearing') or '').upper()
        auth = str(tx.get('statusAuth') or '').upper()
        if clearing in ('RETURNED', 'REJECTED', 'CONTESTED'):
            return 'failed'
        if auth in ('DECLINED', 'CANCELLED'):
            return 'failed'
        # Initial ACH acceptance is never a settled payment.
        if refresh and clearing == 'CLEARED' and auth == 'APPROVED':
            return 'refunded' if refund else 'paid'
        return 'refund_pending' if refund else 'ach_pending'
    status = str(tx.get('status') or '').upper()
    if status in ('APPROVED', 'APPROVAL'):
        return 'refunded' if refund else 'paid'
    return 'failed' if status in ('DECLINED', 'DECLINE') else 'review_required'

async def apply_result(oid, aid, tx, status, *, refund=False):
    # Persist only a safe provider response summary, recoverable after a local CAS failure.
    rid = aid + (':refund' if refund else ':payment')
    tid = transaction_id(tx)
    result = {'order_id': oid, 'attempt_id': aid, 'transaction_id': tid, 'status': status, 'refund': refund, 'at': s.now()}
    await s.get_db().store_payment_results.update_one({'_id': rid}, {'$set': result}, upsert=True)
    def operation(state):
        o = state['orders'][oid]
        a = o.get('refund_attempt' if refund else 'payment_attempt', {})
        if a.get('id') != aid:
            raise HTTPException(409, 'store_payment_review_required')
        if a.get('status') == 'failed' and status != 'failed':
            if not o.get('payment_review_required'):
                o['payment_review_required'] = True
                s.audit(state, 'helcim', 'payment_conflicting_result', oid)
            return s.order_public(o)
        if a.get('status') in ('paid', 'refunded') and status != a['status']:
            # ACH returns after settlement need an explicit accounting review.
            if status == 'failed' and not o.get('payment_review_required'):
                o['payment_review_required'] = True
                s.audit(state, 'helcim', 'payment_return_review', oid)
            return s.order_public(o)
        if a.get('status') == status and a.get('transaction_id') == tid:
            return s.order_public(o)
        a.update(status=status, transaction_id=tid or a.get('transaction_id', ''), updated_at=s.now())
        o['updated_at'] = s.now()
        if refund:
            if status == 'refunded':
                o['payment_status'] = 'refunded'
                o['refunded_cents'] = a['amount_cents']
                o['refunded_at'] = s.now()
            s.audit(state, 'helcim', 'store_refund_' + status, oid, {'order_id':oid,'user_id':o['user_id'],'status':'refunded'} if status == 'refunded' else None)
        else:
            o['payment_status'] = status
            if status == 'paid':
                o.update(payment_reference=tid, payment_actor='helcim', paid_at=s.now())
                from rental.store_receipts import issue_receipt
                issue_receipt(state, o)
                s.audit(state, 'helcim', 'payment_recorded', oid, {'order_id':oid,'user_id':o['user_id'],'status':'paid','email_notice':True})
            else:
                s.audit(state, 'helcim', 'store_payment_' + status, oid)
        return s.order_public(o)
    return await s.mutate(operation)

async def charge(order, prepared, ip):
    cfg, m = prepared
    a = order['payment_attempt']
    try:
        if a['type'] == 'card':
            body = {'ipAddress': ip, 'ecommerce': True, 'currency': 'USD', 'amount': order['total_cents']/100,
                    'cardData': {'cardToken': decrypt_provider_token(m['card_token'])}}
            if m.get('customer_code'):
                body['customerCode'] = m['customer_code']
            tx = await provider_request(cfg, 'POST', '/payment/purchase', body=body, key=a['id'])
        else:
            tx = await provider_request(cfg, 'PUT', '/ach/withdraw', key=a['id'], body={
                'customerId': int(m['customer_id']), 'bankAccountId': int(m['bank_account_id']),
                'currencyId': 2, 'amount': order['total_cents']/100})
        return await apply_result(order['id'], a['id'], tx, outcome(tx, a))
    except Exception:
        # A timeout or persistence error never releases the claim or sends again.
        # Preserve a successful saved result for later recovery.
        saved = await s.get_db().store_payment_results.find_one({'_id': a['id'] + ':payment'})
        if not saved:
            return await apply_result(order['id'], a['id'], {}, 'review_required')
        return s.order_public((await s.read_state())['orders'][order['id']])

async def refresh_order(oid, *, refund=False):
    state = await s.read_state(); o = state['orders'][oid]
    a = o.get('refund_attempt' if refund else 'payment_attempt')
    if not a:
        return s.order_public(o)
    suffix = ':refund' if refund else ':payment'
    saved = await s.get_db().store_payment_results.find_one({'_id': a['id'] + suffix})
    if saved and (saved['status'] != a['status'] or saved['transaction_id'] != a.get('transaction_id', '')):
        await apply_result(oid, a['id'], {'transactionId': saved['transaction_id']}, saved['status'], refund=refund)
        o = (await s.read_state())['orders'][oid]; a = o['refund_attempt' if refund else 'payment_attempt']
    tid = a.get('transaction_id')
    if not tid or a['status'] in ('refunded', 'failed') or (a['type'] == 'card' and a['status'] == 'paid'):
        return s.order_public(o)
    cfg = await configuration()
    if cfg['fingerprint'] != a['credential_fingerprint'] or cfg['environment'] != a['environment']:
        raise HTTPException(409, 'store_payment_environment_changed')
    path = '/ach/transactions/' if a['type'] == 'ach' else '/card-transactions/'
    try:
        tx = await provider_request(cfg, 'GET', path + tid)
        if transaction_id(tx) != tid:
            raise ValueError('wrong_transaction')
        return await apply_result(oid, a['id'], tx, outcome(tx, a, refresh=True, refund=refund), refund=refund)
    except (RuntimeError, ValueError, httpx.HTTPError):
        raise HTTPException(503, 'store_payment_status_unavailable')

@router.post('/store/orders/{oid}/payment-status')
async def customer_status(oid: str, request: Request):
    user = await s.resident(request)
    o = (await s.read_state())['orders'].get(oid)
    if not o or o['user_id'] != s.identity(user):
        raise HTTPException(404, 'store_order_not_found')
    return await refresh_order(oid, refund=bool(o.get('refund_attempt')))

@router.post('/admin/store/orders/{oid}/payment-status')
async def admin_status(oid: str, request: Request):
    await s.auth_admin(request)
    o = (await s.read_state())['orders'].get(oid)
    if not o:
        raise HTTPException(404, 'store_order_not_found')
    return await refresh_order(oid, refund=bool(o.get('refund_attempt')))

class Refund(s.Strict):
    reason: str = Field(min_length=5, max_length=250)

@router.post('/admin/store/orders/{oid}/refund')
async def refund_order(oid: str, body: Refund, request: Request):
    actor = s.identity(await s.auth_admin(request))
    cfg = await configuration()
    aid = str(uuid4())
    def claim(state):
        o = state['orders'].get(oid)
        if not o:
            raise HTTPException(404, 'store_order_not_found')
        if o.get('refund_attempt'):
            return None
        a = o.get('payment_attempt') or {}
        if o['status'] == 'out_for_delivery':
            raise HTTPException(409, 'store_refund_delivery_in_progress')
        if o['payment_status'] != 'paid' or a.get('status') != 'paid' or not a.get('transaction_id') or o.get('payment_review_required'):
            raise HTTPException(409, 'store_refund_unavailable')
        if a['credential_fingerprint'] != cfg['fingerprint'] or a['environment'] != cfg['environment']:
            raise HTTPException(409, 'store_payment_environment_changed')
        o['refund_attempt'] = {**a, 'id': aid, 'status': 'processing', 'transaction_id': '',
            'original_transaction_id': a['transaction_id'], 'reason': body.reason, 'actor': actor, 'created_at': s.now()}
        s.audit(state, actor, 'store_refund_requested', oid)
        return o.copy()
    o = await s.mutate(claim)
    if not o:
        return s.order_public((await s.read_state())['orders'][oid])
    a = o['refund_attempt']
    try:
        if a['type'] == 'ach':
            tx = await provider_request(cfg, 'PUT', '/ach/transactions/' + a['original_transaction_id'] + '/refund', key=aid, body={'amount': a['amount_cents']/100})
        else:
            tx = await provider_request(cfg, 'POST', '/payment/refund', key=aid, body={
                'originalTransactionId': int(a['original_transaction_id']), 'amount': a['amount_cents']/100,
                'ipAddress': request.client.host if request.client else '127.0.0.1', 'ecommerce': True})
        return await apply_result(oid, aid, tx, outcome(tx, a, refund=True), refund=True)
    except Exception:
        saved = await s.get_db().store_payment_results.find_one({'_id': aid + ':refund'})
        if not saved:
            return await apply_result(oid, aid, {}, 'review_required', refund=True)
        return s.order_public((await s.read_state())['orders'][oid])

async def reconcile_pending(db):
    from rental.background_job_policy import should_disable_background_jobs
    if should_disable_background_jobs():
        return
    state = await db.resident_store.find_one({'_id': s.KEY}) or {}
    pending = []
    for o in state.get('orders', {}).values():
        refund = bool(o.get('refund_attempt'))
        a = o.get('refund_attempt' if refund else 'payment_attempt') or {}
        if a.get('status') in ('processing', 'ach_pending', 'refund_pending', 'review_required'):
            pending.append((o, a, refund))
    for o, a, refund in sorted(pending, key=lambda entry: entry[1].get('last_checked_at', ''))[:10]:
        if should_disable_background_jobs():
            return
        def mark(state):
            current = state['orders'][o['id']].get('refund_attempt' if refund else 'payment_attempt', {})
            if current.get('id') == a['id']:
                current['last_checked_at'] = s.now()
        await s.mutate(mark)
        try:
            await refresh_order(o['id'], refund=refund)
        except Exception:
            # Keep the claim. An operator can inspect the unresolved attempt.
            pass
