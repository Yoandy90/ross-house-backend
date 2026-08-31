import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.lease_renewal_contract_generation_router as generation
import rental.lease_renewal_tenant_response_router as response


def run(coro): return asyncio.run(coro)


class InsertResult:
    def __init__(self, inserted_id): self.inserted_id = inserted_id


class Cursor:
    def __init__(self, docs): self.docs = list(docs)
    def limit(self, n): self.docs = self.docs[:n]; return self
    async def to_list(self, n): return self.docs[:n]


class Collection:
    def __init__(self, docs=None): self.docs = list(docs or [])
    async def find_one(self, query):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in query.items())), None)
    def find(self, query): return Cursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])
    async def insert_one(self, doc): self.docs.append(doc); return InsertResult(doc['_id'])


def fixture(decision='accept', with_unit=False):
    old_id, pid, tid, qid = [ObjectId() for _ in range(4)]
    unit_id = ObjectId() if with_unit else None
    end = datetime.now(timezone.utc) + timedelta(days=30)
    old = {'_id': old_id, 'status': 'active', 'property_id': str(pid), 'tenant_id': str(tid), 'unit_id': str(unit_id) if unit_id else None, 'rent_amount': 1200, 'deposit_amount': 500, 'end_date': end.isoformat(), 'payment_due_day': 1}
    proposal = {'_id': qid, 'status': 'approved', 'lease_id': str(old_id), 'property_id': str(pid), 'tenant_id': str(tid), 'current_rent': 1200, 'proposed_rent': 1250, 'lease_end_date': end.isoformat(), 'recommendation': 'raise'}
    prop = {'_id': pid, 'address': '121 Oak Ave', 'current_contract_id': str(old_id), 'current_tenant_id': str(tid)}
    canonical = {**old, '_canonical_end': end, '_canonical_rent': 1200.0, '_property': prop}
    terms = response._terms(proposal, canonical); digest = response._digest(terms)
    accepted = {'_id': qid, 'proposal_id': str(qid), 'lease_id': str(old_id), 'property_id': str(pid), 'tenant_id': str(tid), 'decision': decision, 'terms_digest': digest, 'terms': terms}
    class DB: pass
    db = DB(); db.rental_contracts = Collection([old]); db.properties = Collection([prop]); db.tenants = Collection([{'_id': tid}]); db.lease_renewal_proposals = Collection([proposal]); db.lease_renewal_responses = Collection([accepted]); db.lease_renewal_notification_outbox = Collection([{'proposal_id': str(qid), 'tenant_id': str(tid), 'status': 'sent'}]); db.property_units = Collection([{'_id': unit_id, 'property_id': str(pid), 'current_contract_id': str(old_id), 'current_tenant_id': str(tid)}] if unit_id else [])
    return db, proposal, old


def test_accepted_response_generates_pending_signatures_without_occupancy_mutation():
    db, proposal, old = fixture()
    result = run(generation._generate_under_lock(db, str(proposal['_id']), 'admin-1'))
    assert result['status'] == 'pending_signatures' and result['idempotent'] is False
    created = db.rental_contracts.docs[-1]
    assert created['rent_amount'] == 1250
    assert created['start_date'] == (datetime.fromisoformat(old['end_date']).date() + timedelta(days=1)).isoformat()
    assert created['activation_authority'] == 'lease_lifecycle_only'
    assert old['status'] == 'active'
    assert db.properties.docs[0]['current_contract_id'] == str(old['_id'])


def test_generation_is_deterministically_idempotent():
    db, proposal, _old = fixture()
    first = run(generation._generate_under_lock(db, str(proposal['_id']), 'admin-1'))
    second = run(generation._generate_under_lock(db, str(proposal['_id']), 'admin-2'))
    assert first['contract_id'] == second['contract_id']
    assert second['idempotent'] is True
    assert len(db.rental_contracts.docs) == 2


def test_decline_and_tampered_response_terms_fail_closed():
    db, proposal, _old = fixture('decline')
    with pytest.raises(HTTPException) as exc:
        run(generation._generate_under_lock(db, str(proposal['_id']), 'admin'))
    assert exc.value.detail == 'renewal_response_not_accepted'
    db, proposal, _old = fixture(); db.lease_renewal_responses.docs[0]['terms']['proposed_rent'] = '1.00'
    with pytest.raises(HTTPException) as exc:
        run(generation._generate_under_lock(db, str(proposal['_id']), 'admin'))
    assert exc.value.detail == 'renewal_response_terms_invalid'


def test_stale_occupancy_and_client_contract_terms_fail_closed():
    db, proposal, old = fixture(); db.properties.docs[0]['current_contract_id'] = str(ObjectId())
    with pytest.raises(HTTPException) as exc:
        run(generation._generate_under_lock(db, str(proposal['_id']), 'admin'))
    assert exc.value.detail == 'renewal_source_property_occupancy_changed'
    with pytest.raises(HTTPException) as exc:
        run(generation.generate_renewal_contract(str(proposal['_id']), {'rent_amount': 1}, db, {'_id': 'admin'}))
    assert exc.value.detail == 'renewal_generation_payload_must_be_empty'


def test_exact_old_unit_occupancy_can_coexist_but_other_claim_cannot():
    db, proposal, _old = fixture(with_unit=True)
    assert run(generation._generate_under_lock(db, str(proposal['_id']), 'admin'))['status'] == 'pending_signatures'
    db, proposal, _old = fixture(with_unit=True); db.property_units.docs[0]['current_contract_id'] = str(ObjectId())
    with pytest.raises(HTTPException) as exc:
        run(generation._generate_under_lock(db, str(proposal['_id']), 'admin'))
    assert exc.value.detail == 'renewal_source_unit_occupancy_changed'


def test_effective_dates_handle_month_end_exactly(monkeypatch):
    monkeypatch.setenv('RENEWAL_TERM_MONTHS', '12')
    assert generation._add_months(datetime(2027, 1, 31).date(), 1).isoformat() == '2027-02-28'
    assert generation._add_months(datetime(2028, 2, 29).date(), 12).isoformat() == '2029-02-28'


def test_source_has_no_activation_occupancy_or_payment_write():
    source = open('rental/lease_renewal_contract_generation_router.py', encoding='utf-8').read()
    assert '"status": "active"' not in source
    for forbidden in ('properties.update', 'property_units.update', 'tenants.update', 'rent_payments', 'force_activate'):
        assert forbidden not in source

