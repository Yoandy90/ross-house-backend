import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.lease_renewal_delivery_recovery_router as recovery


def run(coro): return asyncio.run(coro)


class Result:
    def __init__(self, matched_count=1): self.matched_count = matched_count


def nested(doc, key):
    value = doc
    for part in key.split('.'):
        if not isinstance(value, dict) or part not in value: return None
        value = value[part]
    return value


def matches(doc, query):
    for key, value in query.items():
        if key == '$or':
            if not any(matches(doc, item) for item in value): return False
            continue
        actual = nested(doc, key)
        if isinstance(value, dict):
            if '$exists' in value and (actual is not None) != value['$exists']: return False
            if '$ne' in value and actual == value['$ne']: return False
        elif actual != value: return False
    return True


class Collection:
    def __init__(self, docs): self.docs = docs
    async def find_one(self, query):
        return next((d for d in self.docs if matches(d, query)), None)
    async def update_one(self, query, update):
        doc = next((d for d in self.docs if matches(d, query)), None)
        if not doc: return Result(0)
        for key, value in update.get('$set', {}).items():
            target = doc; parts = key.split('.')
            for part in parts[:-1]: target = target.setdefault(part, {})
            target[parts[-1]] = value
        return Result()


class DB:
    def __init__(self, doc): self.lease_renewal_notification_outbox = Collection([doc])


def fixture(status='ambiguous_provider_result'):
    return {
        '_id': ObjectId(), 'status': status, 'attempts': 1, 'claim_id': 'claim-1',
        'claimed_at': datetime.now(timezone.utc) - timedelta(hours=1),
        'provider_started_at': datetime.now(timezone.utc) - timedelta(hours=1),
        'proposal_id': str(ObjectId()), 'lease_id': str(ObjectId()),
        'property_id': str(ObjectId()), 'tenant_id': str(ObjectId()), 'channel': 'email',
    }


def proposal_body(outcome='sent'):
    reason = 'provider_dashboard_confirmed_delivered' if outcome == 'sent' else 'provider_dashboard_confirmed_not_accepted'
    return {'outcome': outcome, 'reason': reason, 'provider_evidence_reference': 'sg:event/123', 'expected_status': 'ambiguous_provider_result', 'expected_attempts': 1}


def test_two_distinct_admins_close_ambiguous_delivery_as_sent():
    doc = fixture(); db = DB(doc)
    proposed = run(recovery.propose_resolution(str(doc['_id']), proposal_body(), db, {'_id': 'admin-a', 'email': 'a@example.com'}))
    result = run(recovery.confirm_resolution(str(doc['_id']), proposed['resolution_id'], db, {'_id': 'admin-b', 'email': 'b@example.com'}))
    assert result == {'ok': True, 'status': 'sent', 'automatic_retry_allowed': False}
    assert doc['status'] == 'sent'
    assert doc['sent_resolution'] == 'manual_provider_evidence'


def test_same_admin_cannot_confirm_own_resolution():
    doc = fixture(); db = DB(doc)
    proposed = run(recovery.propose_resolution(str(doc['_id']), proposal_body(), db, {'_id': 'admin-a', 'email': 'a@example.com'}))
    with pytest.raises(HTTPException) as exc:
        run(recovery.confirm_resolution(str(doc['_id']), proposed['resolution_id'], db, {'_id': 'admin-a', 'email': 'a@example.com'}))
    assert exc.value.detail == 'renewal_recovery_distinct_confirmer_required'
    assert doc['status'] == 'ambiguous_provider_result'


def test_fresh_claim_cannot_be_manually_resolved_while_worker_may_run():
    doc = fixture('claimed'); doc['claimed_at'] = datetime.now(timezone.utc); doc['provider_started_at'] = datetime.now(timezone.utc)
    body = proposal_body(); body['expected_status'] = 'claimed'
    with pytest.raises(HTTPException) as exc:
        run(recovery.propose_resolution(str(doc['_id']), body, DB(doc), {'_id': 'admin-a'}))
    assert exc.value.detail == 'renewal_delivery_claim_still_fresh'


def test_stale_snapshot_and_arbitrary_evidence_fail_closed():
    doc = fixture(); db = DB(doc)
    body = proposal_body(); body['expected_attempts'] = 0
    with pytest.raises(HTTPException) as exc:
        run(recovery.propose_resolution(str(doc['_id']), body, db, {'_id': 'admin-a'}))
    assert exc.value.detail == 'renewal_recovery_snapshot_stale'
    body = proposal_body(); body['provider_evidence_reference'] = 'tenant@example.com evidence'
    with pytest.raises(HTTPException) as exc:
        run(recovery.propose_resolution(str(doc['_id']), body, db, {'_id': 'admin-a'}))
    assert exc.value.detail == 'renewal_recovery_evidence_reference_invalid'


def test_boolean_attempt_snapshot_is_not_accepted_as_integer_one():
    doc = fixture(); body = proposal_body(); body['expected_attempts'] = True
    with pytest.raises(HTTPException) as exc:
        run(recovery.propose_resolution(str(doc['_id']), body, DB(doc), {'_id': 'admin-a'}))
    assert exc.value.detail == 'renewal_recovery_expected_attempts_invalid'


def test_recovery_source_has_no_provider_call_or_retry_transition():
    source = open('rental/lease_renewal_delivery_recovery_router.py', encoding='utf-8').read().lower()
    assert 'sendgrid' not in source
    assert 'twilio' not in source
    assert 'retryable_failure' not in source
    assert 'automatic_retry_allowed": false' in source
