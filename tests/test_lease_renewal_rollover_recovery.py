import asyncio
from datetime import date

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

import rental.lease_renewal_rollover_recovery_router as recovery
from rental.lease_renewal_rollover_recovery_audit import (
    RECOVERY_AUDIT_EVENTS,
    verify_recovery_audit_event,
)
import rental.lease_renewal_tenant_response_router as tenant_response
from rental.lease_renewal_contract_generation_router import _renewal_contract_id


def run(coro): return asyncio.run(coro)


def value(doc, path):
    current = doc
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current: return None
        current = current[part]
    return current


def exists(doc, path):
    current = doc
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current: return False
        current = current[part]
    return True


def matches(doc, query):
    for key, expected in query.items():
        if key == '$or':
            if not any(matches(doc, option) for option in expected): return False
            continue
        actual = value(doc, key)
        if isinstance(expected, dict):
            if '$exists' in expected and exists(doc, key) != expected['$exists']: return False
            if '$ne' in expected and actual == expected['$ne']: return False
            if '$in' in expected and actual not in expected['$in']: return False
            if '$nin' in expected and actual in expected['$nin']: return False
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
    async def insert_one(self, doc):
        if any(existing.get("_id") == doc.get("_id") for existing in self.docs):
            raise DuplicateKeyError("duplicate _id")
        self.docs.append(doc)
    async def update_one(self, query, update):
        doc = next((d for d in self.docs if matches(d, query)), None)
        if not doc or (self.fail_on and self.fail_on(query, update)): return Result(0)
        for key, val in update.get('$set', {}).items():
            target = doc; parts = key.split('.')
            for part in parts[:-1]: target = target.setdefault(part, {})
            target[parts[-1]] = val
        for key in update.get('$unset', {}):
            target = doc; parts = key.split('.')
            for part in parts[:-1]: target = target.get(part, {})
            target.pop(parts[-1], None)
        return Result()


def fixture():
    proposal_oid = ObjectId()
    proposal_id = str(proposal_oid)
    old_id = ObjectId()
    new_id = _renewal_contract_id(proposal_id)
    pid, tid = ObjectId(), ObjectId()
    claim = 'claim-secret-never-returned'
    old_end = date.today()
    old = {
        '_id': old_id, 'status': 'expired', 'property_id': str(pid),
        'tenant_id': str(tid), 'unit_id': None, 'end_date': old_end.isoformat(),
        'rent_amount': 1200.0, 'lifecycle_claim_id': claim,
        'lifecycle_claim_target': 'renewal_rollover',
    }
    proposal = {
        '_id': proposal_oid, 'status': 'approved', 'lease_id': str(old_id),
        'property_id': str(pid), 'tenant_id': str(tid), 'unit_id': None,
        'recommendation': 'renew', 'current_rent': 1200.0,
        'proposed_rent': 1250.0, 'lease_end_date': old_end.isoformat(),
    }
    terms = {
        'proposal_id': proposal_id, 'lease_id': str(old_id),
        'property_id': str(pid), 'tenant_id': str(tid),
        'recommendation': 'renew', 'current_rent': '1200.00',
        'proposed_rent': '1250.00', 'lease_end_date': old_end.isoformat(),
    }
    digest = tenant_response._digest(terms)
    response = {
        '_id': proposal_oid, 'proposal_id': proposal_id, 'decision': 'accept',
        'lease_id': str(old_id), 'property_id': str(pid), 'tenant_id': str(tid),
        'terms': terms, 'terms_digest': digest,
    }
    new = {
        '_id': new_id, 'status': 'pending_activation',
        'property_id': str(pid), 'tenant_id': str(tid), 'unit_id': None,
        'start_date': date.today().isoformat(), 'rent_amount': 1250.0,
        'tenant_signature': 't', 'admin_signature': 'a',
        'lifecycle_claim_id': claim, 'lifecycle_claim_target': 'renewal_rollover',
        'renewal_source': {
            'proposal_id': proposal_id, 'response_id': proposal_id,
            'prior_contract_id': str(old_id), 'terms_digest': digest,
        },
    }
    prop = {
        '_id': pid, 'status': 'rented', 'status_manually_set': False,
        'current_contract_id': str(new_id), 'current_tenant_id': str(tid),
    }
    tenant = {
        '_id': tid, 'current_contract_id': str(new_id),
        'current_property_id': str(pid), 'current_unit_id': None,
    }
    record = {
        '_id': new_id, 'proposal_id': proposal_id,
        'prior_contract_id': str(old_id), 'renewal_contract_id': str(new_id),
        'property_id': str(pid), 'tenant_id': str(tid), 'claim_id': claim,
        'state': 'recovery_required', 'stage': 'activate_renewal',
        'automatic_retry_allowed': False,
    }
    class DB: pass
    db = DB()
    db.rental_contracts = Collection([old, new])
    db.properties = Collection([prop])
    db.property_units = Collection()
    db.tenants = Collection([tenant])
    db.lease_renewal_proposals = Collection([proposal])
    db.lease_renewal_responses = Collection([response])
    db.lease_renewal_rollovers = Collection([record])
    db.lease_renewal_rollover_recovery_audit = Collection()
    return db, proposal_id, record, old, new, prop, tenant


def propose(db, proposal_id, record, admin='admin-a'):
    observed = run(recovery._observation(db, record, db.rental_contracts.docs[0], db.rental_contracts.docs[1]))
    digest = recovery._digest(observed)
    result = run(recovery.propose_recovery(proposal_id, str(record['_id']),
                                           {'action': 'complete', 'observed_digest': digest}, db, {'_id': admin}))
    return result, digest


def test_observation_is_stable_sanitized_and_read_only():
    db, proposal_id, record, old, new, *_ = fixture()
    first = run(recovery._observation(db, record, old, new)); second = run(recovery._observation(db, record, old, new))
    assert recovery._digest(first) == recovery._digest(second)
    assert record['claim_id'] not in repr(first)
    assert first['prior_claim'] == 'exact' and first['renewal_claim'] == 'exact'


def test_observation_exposes_only_bounded_pending_confirmation_handoff():
    db, proposal_id, record, *_ = fixture()
    before = run(recovery.observe_recovery(
        proposal_id, str(record['_id']), db, {'_id': 'admin-a'}))
    assert before['pending_confirmation'] is None
    proposed, digest = propose(db, proposal_id, record)
    view = run(recovery.observe_recovery(
        proposal_id, str(record['_id']), db, {'_id': 'admin-b'}))
    pending = view['pending_confirmation']
    assert pending == {
        'status': 'pending_confirmation',
        'action': 'complete',
        'recovery_id': proposed['recovery_id'],
        'observed_digest': digest,
        'observation_matches': True,
        'confirmable': True,
        'requires_second_admin': True,
    }
    assert record['claim_id'] not in repr(view)
    assert 'proposed_by' not in repr(view)
    audit = view["audit"]
    assert audit["status"] == "partial"
    assert audit["valid"] is True and audit["complete"] is False
    assert audit["recorded_events"] == 1
    assert "actor" not in repr(audit)
    assert "digest" not in repr(audit)
    assert "claim_id" not in repr(audit)


def test_corrupt_pending_confirmation_handoff_fails_closed():
    db, proposal_id, record, *_ = fixture()
    propose(db, proposal_id, record)
    record['manual_recovery']['recovery_id'] = 'not-valid'
    with pytest.raises(HTTPException) as exc:
        run(recovery.observe_recovery(
            proposal_id, str(record['_id']), db, {'_id': 'admin-b'}))
    assert exc.value.detail == 'renewal_rollover_recovery_proposal_invalid'


def test_two_admin_confirmation_completes_only_forward():
    db, proposal_id, record, old, new, prop, tenant = fixture()
    proposed, digest = propose(db, proposal_id, record)
    result = run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'], 'id:admin-b'))
    assert result['state'] == 'completed' and record['state'] == 'completed'
    assert old['status'] == 'expired' and new['status'] == 'active'
    assert prop['status'] == 'rented' and prop['current_contract_id'] == str(new['_id'])
    assert tenant['current_contract_id'] == str(new['_id'])
    assert not old.get('lifecycle_claim_id') and not new.get('lifecycle_claim_id')
    assert record['manual_recovery']['observed_digest'] == digest
    events = db.lease_renewal_rollover_recovery_audit.docs
    assert [event["event"] for event in events] == list(RECOVERY_AUDIT_EVENTS)
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert all(verify_recovery_audit_event(event) for event in events)
    assert events[0]["previous_digest"] == ""
    assert all(
        events[index]["previous_digest"] == events[index - 1]["integrity_digest"]
        for index in range(1, len(events))
    )
    assert record["claim_id"] not in repr(events)
    assert "claim_id" not in repr(events)
    view = run(recovery.observe_recovery(
        proposal_id, str(record["_id"]), db, {"_id": "admin-b"},
    ))
    audit = view["audit"]
    assert audit["status"] == "complete"
    assert audit["valid"] is True and audit["complete"] is True
    assert audit["recorded_events"] == audit["expected_events"] == 4
    assert "actor" not in repr(audit)
    assert "digest" not in repr(audit)
    assert "claim_id" not in repr(audit)


def test_completed_recovery_inspection_rejects_changed_authority():
    db, proposal_id, record, _old, _new, prop, _tenant = fixture()
    proposed, _ = propose(db, proposal_id, record)
    run(recovery._complete(
        db, proposal_id, str(record["_id"]), proposed["recovery_id"],
        "id:admin-b", {"id:admin-b"},
    ))
    prop["current_contract_id"] = str(ObjectId())
    with pytest.raises(HTTPException) as exc:
        run(recovery.observe_recovery(
            proposal_id, str(record["_id"]), db, {"_id": "admin-b"},
        ))
    assert exc.value.detail == "renewal_rollover_recovery_completed_state_invalid"


def test_same_admin_and_stale_digest_fail_before_mutation():
    db, proposal_id, record, old, new, *_ = fixture(); proposed, _ = propose(db, proposal_id, record)
    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'], 'id:admin-a'))
    assert exc.value.detail == 'renewal_rollover_recovery_second_admin_required'
    old['status'] = 'active'
    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'], 'id:admin-b'))
    assert exc.value.detail == 'renewal_rollover_recovery_observation_changed'
    assert record['manual_recovery']['status'] == 'proposed'


def test_confirmation_fails_closed_when_recovery_audit_was_tampered():
    db, proposal_id, record, *_ = fixture()
    proposed, _ = propose(db, proposal_id, record)
    db.lease_renewal_rollover_recovery_audit.docs[0]["actor"] = "tampered"
    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(
            db, proposal_id, str(record["_id"]), proposed["recovery_id"],
            "id:admin-b", {"id:admin-b"},
        ))
    assert exc.value.detail == "renewal_recovery_audit_chain_invalid"
    assert record["state"] == "recovery_required"
    assert record["manual_recovery"]["status"] == "failed"


def test_same_admin_cannot_switch_from_id_to_email_identity():
    db, proposal_id, record, *_ = fixture()
    observed = run(recovery._observation(db, record, db.rental_contracts.docs[0], db.rental_contracts.docs[1]))
    digest = recovery._digest(observed)
    proposed = run(recovery.propose_recovery(
        proposal_id, str(record['_id']), {'action': 'complete', 'observed_digest': digest},
        db, {'_id': 'admin-a', 'email': 'ADMIN@example.com'}))
    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'],
                               'email:admin@example.com', {'email:admin@example.com'}))
    assert exc.value.detail == 'renewal_rollover_recovery_second_admin_required'


def test_foreign_projection_and_foreign_property_claim_fail_closed():
    db, proposal_id, record, *_rest = fixture(); db.properties.docs[0]['current_contract_id'] = str(ObjectId())
    observed = run(recovery._observation(db, record, db.rental_contracts.docs[0], db.rental_contracts.docs[1]))
    with pytest.raises(HTTPException) as exc: recovery._assert_recoverable(observed)
    assert exc.value.detail == 'renewal_rollover_recovery_projection_foreign'
    db, proposal_id, record, *_rest = fixture()
    db.rental_contracts.docs.append({'_id': ObjectId(), 'property_id': record['property_id'], 'lifecycle_claim_id': 'foreign'})
    proposed, _ = propose(db, proposal_id, record)
    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'], 'id:admin-b'))
    assert exc.value.detail == 'renewal_rollover_recovery_property_foreign_claim'


def test_unclaimed_partial_authority_and_extra_actions_are_rejected():
    db, proposal_id, record, old, new, *_ = fixture()
    old.pop('lifecycle_claim_id'); new.pop('lifecycle_claim_id')
    observed = run(recovery._observation(db, record, old, new))
    with pytest.raises(HTTPException) as exc: recovery._assert_recoverable(observed)
    assert exc.value.detail == 'renewal_rollover_recovery_authority_unclaimed'
    with pytest.raises(HTTPException) as exc:
        run(recovery.propose_recovery(proposal_id, str(record['_id']),
            {'action': 'rollback', 'observed_digest': '0' * 64}, db, {'_id': 'admin-a'}))
    assert exc.value.detail == 'renewal_rollover_recovery_payload_invalid'


def test_recovery_requires_disabled_retry_and_valid_state_stage_pair():
    db, _proposal_id, record, old, new, *_ = fixture()
    record["automatic_retry_allowed"] = True
    observed = run(recovery._observation(db, record, old, new))
    with pytest.raises(HTTPException) as exc:
        recovery._assert_recoverable(observed)
    assert exc.value.detail == "renewal_rollover_recovery_fence_invalid"

    db, _proposal_id, record, old, new, *_ = fixture()
    record.pop("automatic_retry_allowed")
    observed = run(recovery._observation(db, record, old, new))
    with pytest.raises(HTTPException) as exc:
        recovery._assert_recoverable(observed)
    assert exc.value.detail == "renewal_rollover_recovery_fence_invalid"

    db, _proposal_id, record, old, new, *_ = fixture()
    record["stage"] = "unknown_stage"
    observed = run(recovery._observation(db, record, old, new))
    with pytest.raises(HTTPException) as exc:
        recovery._assert_recoverable(observed)
    assert exc.value.detail == "renewal_rollover_recovery_state_stage_invalid"

    record["state"] = "committed"
    record["stage"] = "activate_renewal"
    observed = run(recovery._observation(db, record, old, new))
    with pytest.raises(HTTPException) as exc:
        recovery._assert_recoverable(observed)
    assert exc.value.detail == "renewal_rollover_recovery_state_stage_invalid"


def test_interrupted_confirmation_resumes_only_for_same_confirmer():
    db, proposal_id, record, old, new, *_ = fixture()
    proposed, _ = propose(db, proposal_id, record)
    recovery_record = record["manual_recovery"]
    recovery_record["status"] = "confirming"
    recovery_record["confirmed_by"] = "id:admin-b"
    recovery_record["confirmed_by_keys"] = ["id:admin-b"]
    record["stage"] = "manual_recovery_confirmed"

    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(
            db, proposal_id, str(record["_id"]), proposed["recovery_id"],
            "id:admin-c", {"id:admin-c"},
        ))
    assert exc.value.detail == "renewal_rollover_recovery_confirmer_changed"
    assert record["state"] == "recovery_required"
    assert new["status"] == "pending_activation"

    result = run(recovery._complete(
        db, proposal_id, str(record["_id"]), proposed["recovery_id"],
        "id:admin-b", {"id:admin-b"},
    ))
    assert result["state"] == "completed"
    assert record["manual_recovery"]["status"] == "confirmed"
    assert old["status"] == "expired" and new["status"] == "active"


def test_interrupted_confirmation_finalizes_already_committed_authority():
    db, proposal_id, record, old, new, _prop, _tenant = fixture()
    proposed, _ = propose(db, proposal_id, record)
    record["state"] = "committed"
    record["stage"] = "manual_recovery_clear_claims"
    record["manual_recovery"].update({
        "status": "confirming",
        "confirmed_by": "id:admin-b",
        "confirmed_by_keys": ["id:admin-b"],
    })
    new["status"] = "active"
    old.pop("lifecycle_claim_id")
    old.pop("lifecycle_claim_target")
    new.pop("lifecycle_claim_id")
    new.pop("lifecycle_claim_target")

    result = run(recovery._complete(
        db, proposal_id, str(record["_id"]), proposed["recovery_id"],
        "id:admin-b", {"id:admin-b"},
    ))
    assert result["state"] == "completed"
    assert record["state"] == "completed"
    assert record["manual_recovery"]["status"] == "confirmed"


def test_failure_retains_recovery_fence_and_never_enables_automatic_retry():
    db, proposal_id, record, old, new, *_ = fixture(); proposed, _ = propose(db, proposal_id, record)
    db.rental_contracts.fail_on = lambda query, update: query.get('_id') == new['_id'] and update.get('$set', {}).get('status') == 'active'
    with pytest.raises(HTTPException):
        run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'], 'id:admin-b'))
    assert record['state'] == 'recovery_required'
    assert record['automatic_retry_allowed'] is False
    assert old.get('lifecycle_claim_id') or new.get('lifecycle_claim_id')
    source = open('rental/lease_renewal_rollover_recovery_router.py', encoding='utf-8').read()
    assert 'force_activate' not in source and 'automatic_retry_allowed": False' in source


def test_expected_claim_on_third_contract_is_also_foreign():
    db, proposal_id, record, *_ = fixture()
    db.rental_contracts.docs.append({'_id': ObjectId(), 'property_id': record['property_id'],
                                     'lifecycle_claim_id': record['claim_id']})
    proposed, _ = propose(db, proposal_id, record)
    with pytest.raises(HTTPException) as exc:
        run(recovery._complete(db, proposal_id, str(record['_id']), proposed['recovery_id'], 'id:admin-b'))
    assert exc.value.detail == 'renewal_rollover_recovery_property_foreign_claim'
