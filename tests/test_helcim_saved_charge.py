"""No external database/provider: exercise charge behavior with atomic fake writes."""
import asyncio
import copy
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from rental import helcim_vault_router as vault
from rental.rent_charge_claim import claim_rent_charge

MID = ObjectId('507f1f77bcf86cd799439011')
MISSING = object()


def lookup(doc, key):
    for part in key.split('.'):
        if not isinstance(doc, dict) or part not in doc:
            return MISSING
        doc = doc[part]
    return doc


def matches(doc, query):
    for key, expected in query.items():
        if key == '$or':
            if not any(matches(doc, q) for q in expected):
                return False
            continue
        actual = lookup(doc, key)
        if isinstance(expected, dict):
            for op, value in expected.items():
                if op == '$exists' and (actual is not MISSING) != value:
                    return False
                if op == '$in' and actual not in value:
                    return False
                if op == '$ne' and actual == value:
                    return False
                if op == '$gte' and (actual is MISSING or actual < value):
                    return False
                if op == '$lt' and (actual is MISSING or actual >= value):
                    return False
                if op == '$regex' and (actual is MISSING or not re.search(value, actual, re.I)):
                    return False
        elif actual != expected:
            return False
    return True


class Collection:
    def __init__(self, docs=()):
        self.docs = copy.deepcopy(list(docs))

    async def find_one(self, query, **kwargs):
        await asyncio.sleep(0)
        return next((copy.deepcopy(d) for d in self.docs if matches(d, query)), None)

    async def update_one(self, query, update):
        for doc in self.docs:
            if matches(doc, query):
                for key, value in update.get('$set', {}).items():
                    target = doc
                    parts = key.split('.')
                    for part in parts[:-1]:
                        target = target.setdefault(part, {})
                    target[parts[-1]] = copy.deepcopy(value)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def database(status='partial'):
    now = datetime.now(timezone.utc)
    invoice = {'_id': 'invoice-1', 'contract_id': 'contract-1', 'status': status,
               'period': now.strftime('%Y-%m'), 'amount': 1000, 'late_fee': 50,
               'total_due': 1050, 'total_paid': 400}
    return SimpleNamespace(
        rental_payments=Collection([invoice]), autopay_config=Collection(),
        rental_contracts=Collection([{'_id': 'contract-1', 'tenant_id': 'tenant-1', 'status': 'active'}]),
        helcim_saved_methods=Collection([{'_id': MID, 'tenant_id': 'tenant-1', 'type': 'card',
                                         'card_token': 'fake-token'}]))


class Request:
    client = SimpleNamespace(host='192.0.2.1')
    def __init__(self, method_id=str(MID)):
        self.method_id = method_id
    async def json(self):
        return {'method_id': self.method_id, 'amount': 1, 'tenant_id': 'other'}


def install(monkeypatch, db, provider):
    async def auth(_):
        return {'_id': 'tenant-1'}
    async def cfg():
        return {'api_token': 'fake-api-token'}
    monkeypatch.setattr(vault, 'auth_tenant_flex', auth)
    monkeypatch.setattr(vault, 'get_db', lambda: db)
    monkeypatch.setattr(vault, '_helcim_cfg', cfg)
    monkeypatch.setattr(vault, 'helcim_purchase_with_token', provider)


@pytest.mark.asyncio
async def test_concurrent_saved_requests_charge_only_outstanding_once(monkeypatch):
    db, calls = database(), []
    async def provider(*args):
        calls.append(args)
        await asyncio.sleep(0)
        return {'status': 'APPROVED', 'transactionId': 'tx-1'}
    install(monkeypatch, db, provider)
    results = await asyncio.gather(*(vault.pay_with_method(Request()) for _ in range(5)), return_exceptions=True)
    assert len(calls) == 1
    assert calls[0][1] == 65000
    assert sum(isinstance(r, dict) and r.get('success') for r in results) == 1
    invoice = db.rental_payments.docs[0]
    assert invoice['total_paid'] == 1050
    assert invoice['paid'] is True
    assert invoice['charge_attempt']['amount'] == 650
    assert invoice['reference_number'] == 'tx-1'


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [None, {'status': 'DECLINED'}, {'status': 'PENDING'}, {'status': 'APPROVED'}])
async def test_uncertain_or_unconfirmed_result_never_recharges(monkeypatch, response):
    db, calls = database(), []
    async def provider(*args):
        calls.append(args)
        if response is None:
            raise TimeoutError()
        return response
    install(monkeypatch, db, provider)
    for _ in range(2):
        with pytest.raises(HTTPException):
            await vault.pay_with_method(Request())
    assert len(calls) == 1
    assert db.rental_payments.docs[0]['status'] == 'partial'
    assert db.rental_payments.docs[0]['total_paid'] == 400
    assert 'charge_attempt' in db.rental_payments.docs[0]


@pytest.mark.asyncio
async def test_accounting_change_after_charge_requires_reconciliation(monkeypatch):
    db = database()
    async def provider(*args):
        db.rental_payments.docs[0]['total_paid'] = 500
        return {'status': 'APPROVED', 'transactionId': 'tx-2'}
    install(monkeypatch, db, provider)
    with pytest.raises(HTTPException) as exc:
        await vault.pay_with_method(Request())
    assert exc.value.status_code == 409
    invoice = db.rental_payments.docs[0]
    assert invoice['total_paid'] == 500
    assert invoice['charge_attempt']['transaction_id'] == 'tx-2'
    assert invoice['charge_attempt']['status'] == 'reconciliation_required'


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['foreign_method', 'invalid_id', 'no_contract', 'settled'])
async def test_invalid_request_never_contacts_provider(monkeypatch, problem):
    db = database('completed' if problem == 'settled' else 'partial')
    if problem == 'foreign_method':
        db.helcim_saved_methods.docs[0]['tenant_id'] = 'another-tenant'
    if problem == 'no_contract':
        db.rental_contracts.docs.clear()
    async def provider(*args):
        pytest.fail('provider must not be called')
    install(monkeypatch, db, provider)
    with pytest.raises(HTTPException):
        await vault.pay_with_method(Request('invalid' if problem == 'invalid_id' else str(MID)))


@pytest.mark.asyncio
async def test_shared_claim_has_one_winner_across_payment_channels():
    db = database()
    results = await asyncio.gather(*(claim_rent_charge(
        db, 'invoice-1', source=source, amount=650, contract_id='contract-1'
    ) for source in ['helcim_saved', 'helcim_autopay', 'stripe_autopay', 'helcim_checkout']))
    assert sum(r is not None for r in results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('blocker', ['legacy_checkout', 'legacy_autopay', 'changed_balance'])
async def test_shared_claim_blocks_existing_attempts_and_changed_balance(blocker):
    db = database()
    now = datetime.now(timezone.utc)
    if blocker == 'legacy_checkout':
        db.rental_payments.docs.append({'_id': 'old-checkout', 'contract_id': 'contract-1',
            'period_year': now.year, 'period_month': now.strftime('%B').lower(), 'status': 'pending_checkout'})
    elif blocker == 'legacy_autopay':
        db.autopay_config.docs.append({'_id': 'ap', 'last_attempt_invoice_id': 'invoice-1', 'last_attempt_date': now})
    else:
        db.rental_payments.docs[0]['total_paid'] = 500
    assert await claim_rent_charge(db, 'invoice-1', source='helcim_saved', amount=650, contract_id='contract-1') is None
    assert 'charge_attempt' not in db.rental_payments.docs[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('competitor', ['autopay', 'checkout'])
@pytest.mark.parametrize('concurrent', [True, False])
async def test_saved_charge_and_other_real_entrypoint_never_both_call_provider(monkeypatch, competitor, concurrent):
    from rental import autopay_cron, payment_processors_router as processors
    from pymongo.errors import DuplicateKeyError
    db, calls = database(), []

    async def insert_one(doc):
        if any(d['_id'] == doc['_id'] for d in db.rental_payments.docs):
            raise DuplicateKeyError('duplicate')
        db.rental_payments.docs.append(copy.deepcopy(doc))
    db.rental_payments.insert_one = insert_one

    async def provider(*args):
        calls.append('saved_or_autopay')
        await asyncio.sleep(0)
        return {'status': 'APPROVED', 'transactionId': 'tx-race'}
    install(monkeypatch, db, provider)
    if competitor == 'autopay':
        ap = {'_id': 'ap-1', 'user_id': 'tenant-1', 'enabled': True,
              'processor': 'helcim', 'helcim_card_token': 'fake-token', 'day_of_month': 1}
        db.autopay_config.docs.append(ap)
        async def config():
            return {'processors': {'helcim': {'api_token': 'fake-api-token'}}}
        monkeypatch.setattr(processors, '_get_doc', config, raising=False)
        monkeypatch.setattr(processors, '_active_creds', lambda c: c, raising=False)
        other = autopay_cron._process_autopay_for_config(db, copy.deepcopy(ap))
    else:
        async def auth(_):
            return {'_id': 'tenant-1'}
        async def active():
            return 'helcim', {}
        async def checkout(**kwargs):
            calls.append('checkout')
            await asyncio.sleep(0)
            return {'url': 'https://example.invalid/checkout', 'external_id': 'fake-session'}
        monkeypatch.setattr(processors.core, 'auth_tenant_flex', auth)
        monkeypatch.setattr(processors.core, 'get_db', lambda: db)
        monkeypatch.setattr(processors.core, 'get_active_processor', active)
        monkeypatch.setattr(processors.core, 'create_hosted_checkout', checkout)
        other = processors.tenant_create_checkout_payment(Request())
    tasks = [vault.pay_with_method(Request()), other] if concurrent else [other]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(calls) <= 1
    assert all(isinstance(r, (dict, HTTPException)) for r in results)
    if not concurrent:
        assert len(calls) == 1
        assert results[0]['success'] is True


@pytest.mark.asyncio
async def test_saved_charge_keeps_claim_when_local_confirmation_write_fails(monkeypatch):
    db, calls = database(), []
    original = db.rental_payments.update_one
    async def write(query, update):
        if 'charge_attempt.provider_status' in update.get('$set', {}):
            raise RuntimeError('database unavailable')
        return await original(query, update)
    db.rental_payments.update_one = write
    async def provider(*args):
        calls.append(args)
        return {'status': 'APPROVED', 'transactionId': 'tx-lost'}
    install(monkeypatch, db, provider)
    for _ in range(2):
        with pytest.raises(HTTPException):
            await vault.pay_with_method(Request())
    assert len(calls) == 1
    assert 'charge_attempt' in db.rental_payments.docs[0]
