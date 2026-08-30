import asyncio
from datetime import datetime, timezone, timedelta

from bson import ObjectId

import rental.lease_renewal_notification_sender as sender


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, matched_count=1): self.matched_count = matched_count


class Cursor:
    def __init__(self, docs): self.docs = docs
    def limit(self, n): self.docs = self.docs[:n]; return self
    async def to_list(self, n): return self.docs[:n]


class Collection:
    def __init__(self, docs): self.docs = docs
    async def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()): return d
    def find(self, query): return Cursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])
    async def update_one(self, query, update):
        for d in self.docs:
            if all((k.startswith('$') or d.get(k) == v or (isinstance(v, dict) and '$exists' in v and (k in d) == v['$exists'])) for k, v in query.items()):
                d.update(update.get('$set', {})); return Result()
        return Result(0)


def fixture():
    cid, pid, tid, qid, oid = [ObjectId() for _ in range(5)]
    end = datetime.now(timezone.utc) + timedelta(days=30)
    contract = {'_id': cid, 'status': 'active', 'property_id': str(pid), 'tenant_id': str(tid), 'rent_amount': 1200, 'end_date': end.isoformat()}
    proposal = {'_id': qid, 'status': 'approved', 'lease_id': str(cid), 'property_id': str(pid), 'tenant_id': str(tid), 'current_rent': 1200, 'lease_end_date': end.isoformat()}
    intent = {'_id': oid, 'proposal_id': str(qid), 'lease_id': str(cid), 'property_id': str(pid), 'tenant_id': str(tid), 'channel': 'email', 'subject': 'Renewal', 'message': 'Message', 'status': 'claimed', 'claim_id': 'claim', 'attempts': 1}
    class DB: pass
    db = DB(); db.rental_contracts = Collection([contract]); db.properties = Collection([{'_id': pid}]); db.lease_renewal_proposals = Collection([proposal]); db.tenants = Collection([{'_id': tid, 'email_normalized': 'tenant@example.com'}]); db.lease_renewal_notification_outbox = Collection([intent])
    return db, intent


def test_provider_confirmation_marks_sent_without_storing_recipient():
    db, intent = fixture()
    async def ok(delivery):
        assert delivery['email'] == 'tenant@example.com'
        return {'provider': 'sendgrid', 'provider_message_id': 'm-1'}
    assert run(sender.process_claimed(db, intent, ok)) == 'sent'
    assert intent['status'] == 'sent'
    assert intent['provider_message_id'] == 'm-1'
    assert 'email' not in intent


def test_timeout_is_ambiguous_and_never_auto_retried():
    db, intent = fixture()
    async def timeout(_delivery): raise sender.ProviderAmbiguousResult('provider_transport_or_timeout')
    assert run(sender.process_claimed(db, intent, timeout)) == 'ambiguous_provider_result'
    assert intent['automatic_retry_allowed'] is False


def test_definite_retryable_rejection_is_bounded():
    db, intent = fixture()
    async def reject(_delivery): raise sender.ProviderRetryableFailure('provider_http_503')
    assert run(sender.process_claimed(db, intent, reject)) == 'retryable_failure'
    intent['status'] = 'claimed'; intent['claim_id'] = 'second-claim'; intent['attempts'] = sender.MAX_ATTEMPTS
    assert run(sender.process_claimed(db, intent, reject)) == 'failed'
    assert intent['automatic_retry_allowed'] is False


def test_stale_binding_fails_before_provider_call():
    db, intent = fixture(); intent['property_id'] = str(ObjectId())
    called = False
    async def should_not_send(_delivery):
        nonlocal called; called = True
    assert run(sender.process_claimed(db, intent, should_not_send)) == 'failed'
    assert called is False


def test_source_has_atomic_claim_and_no_recipient_logging():
    source = open('rental/lease_renewal_notification_sender.py', encoding='utf-8').read()
    assert 'find_one_and_update' in source
    assert 'claim_id' in source
    assert 'ambiguous_provider_result' in source
    assert 'automatic_retry_allowed=False' in source
    assert 'logger.info' not in source
