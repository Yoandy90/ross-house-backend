import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.lease_renewal_rollover_router as rollover
from rental.lease_renewal_contract_generation_router import _renewal_contract_id


def run(coro): return asyncio.run(coro)


def value(doc, path):
    current = doc
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current: return None
        current = current[part]
    return current


def matches(doc, query):
    for key, expected in query.items():
        if key == '$or':
            if not any(matches(doc, option) for option in expected): return False
            continue
        actual = value(doc, key)
        if isinstance(expected, dict):
            if '$exists' in expected and (actual is not None) != expected['$exists']: return False
            if '$ne' in expected and actual == expected['$ne']: return False
            if '$in' in expected and actual not in expected['$in']: return False
        elif actual != expected: return False
    return True


class Result:
    def __init__(self, matched_count=1): self.matched_count = matched_count


class Cursor:
    def __init__(self, docs): self.docs = list(docs)
    def limit(self, n): self.docs = self.docs[:n]; return self
    async def to_list(self, n): return self.docs[:n]


class Collection:
    def __init__(self, docs=None): self.docs = list(docs or []); self.fail_on = None
    async def find_one(self, query, *args): return next((d for d in self.docs if matches(d, query)), None)
    def find(self, query): return Cursor([d for d in self.docs if matches(d, query)])
    async def insert_one(self, doc): self.docs.append(doc)
    async def update_one(self, query, update):
        doc = next((d for d in self.docs if matches(d, query)), None)
        if not doc: return Result(0)
        if self.fail_on and self.fail_on(query, update): return Result(0)
        for key, val in update.get('$set', {}).items():
            target = doc; parts = key.split('.')
            for part in parts[:-1]: target = target.setdefault(part, {})
            target[parts[-1]] = val
        for key in update.get('$unset', {}): doc.pop(key, None)
        return Result()


def fixture():
    proposal_id = str(ObjectId()); old_id = ObjectId(); new_id = _renewal_contract_id(proposal_id)
    pid, tid = ObjectId(), ObjectId(); start = date.today(); old_end = start - timedelta(days=1)
    old = {'_id': old_id, 'status': 'active', 'property_id': str(pid), 'tenant_id': str(tid), 'unit_id': None, 'end_date': old_end.isoformat()}
    new = {'_id': new_id, 'status': 'pending_activation', 'property_id': str(pid), 'tenant_id': str(tid), 'unit_id': None, 'start_date': start.isoformat(), 'tenant_signature': 'tenant-sig', 'admin_signature': 'admin-sig', 'renewal_source': {'proposal_id': proposal_id, 'prior_contract_id': str(old_id)}}
    prop = {'_id': pid, 'status': 'rented', 'status_manually_set': False, 'current_contract_id': str(old_id), 'current_tenant_id': str(tid)}
    tenant = {'_id': tid, 'current_contract_id': str(old_id), 'current_property_id': str(pid), 'current_unit_id': None}
    class DB: pass
    db = DB(); db.rental_contracts = Collection([old, new]); db.properties = Collection([prop]); db.property_units = Collection(); db.tenants = Collection([tenant]); db.lease_renewal_rollovers = Collection()
    return db, proposal_id, old, new, prop, tenant


def test_rollover_transfers_exact_authority_without_available_window():
    db, proposal_id, old, new, prop, tenant = fixture()
    result = run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today()))
    assert result['state'] == 'completed'
    assert old['status'] == 'expired' and new['status'] == 'active'
    assert prop['status'] == 'rented' and prop['current_contract_id'] == str(new['_id'])
    assert tenant['current_contract_id'] == str(new['_id'])
    assert 'lifecycle_claim_id' not in old and 'lifecycle_claim_id' not in new


def test_rollover_is_idempotent_only_after_completed_record():
    db, proposal_id, _old, _new, _prop, _tenant = fixture()
    first = run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today()))
    second = run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today()))
    record = db.lease_renewal_rollovers.docs[0]
    assert record['state'] == 'completed' and first['idempotent'] is False
    assert second['idempotent'] is True and second['contract_id'] == first['contract_id']


def test_too_early_or_noncontiguous_dates_fail_before_claim():
    db, proposal_id, old, new, *_ = fixture()
    with pytest.raises(HTTPException) as exc:
        run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today() - timedelta(days=1)))
    assert exc.value.detail == 'renewal_rollover_too_early'
    new['start_date'] = (date.fromisoformat(old['end_date']) + timedelta(days=2)).isoformat()
    with pytest.raises(HTTPException) as exc:
        run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today() + timedelta(days=2)))
    assert exc.value.detail == 'renewal_rollover_dates_not_contiguous'


def test_partial_failure_retains_claim_and_requires_recovery():
    db, proposal_id, old, new, prop, tenant = fixture()
    db.rental_contracts.fail_on = lambda query, update: query.get('_id') == new['_id'] and query.get('status') == 'pending_activation' and '$set' in update and update['$set'].get('status') == 'active'
    with pytest.raises(HTTPException) as exc:
        run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today()))
    assert exc.value.detail == 'renewal_rollover_new_status_changed'
    assert db.lease_renewal_rollovers.docs[0]['state'] == 'recovery_required'
    assert old.get('lifecycle_claim_id') and new.get('lifecycle_claim_id')
    assert prop['current_contract_id'] == str(new['_id'])
    assert tenant['current_contract_id'] == str(new['_id'])


def test_changed_projection_and_extra_payload_fail_closed():
    db, proposal_id, _old, _new, prop, _tenant = fixture(); prop['current_contract_id'] = str(ObjectId())
    with pytest.raises(HTTPException) as exc:
        run(rollover._rollover_under_lock(db, proposal_id, 'admin', date.today()))
    assert exc.value.detail == 'renewal_rollover_property_projection_changed'
    with pytest.raises(HTTPException) as exc:
        run(rollover.rollover_renewal(proposal_id, {'force_activate': True}, db, {'_id': 'admin'}))
    assert exc.value.detail == 'renewal_rollover_payload_must_be_empty'


def test_inspection_is_read_only_and_hides_claim_value():
    source = open('rental/lease_renewal_rollover_router.py', encoding='utf-8').read()
    assert 'automatic_retry_allowed": False' in source
    assert 'force_activate' not in source
    assert 'mark_unit_rented' not in source
    assert '"status": "available"' not in source
