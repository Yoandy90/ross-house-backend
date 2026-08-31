import asyncio

import pytest
from bson import ObjectId
from fastapi import HTTPException

import rental.lease_renewal_workflow_status_router as status
from rental.lease_renewal_contract_generation_router import _renewal_contract_id


def run(coro): return asyncio.run(coro)


def value(doc, path):
    current = doc
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current: return None
        current = current[part]
    return current


def matches(doc, query):
    return all(value(doc, key) == expected for key, expected in query.items())


class Cursor:
    def __init__(self, docs): self.docs = list(docs)
    def sort(self, key, direction):
        self.docs.sort(key=lambda doc: value(doc, key) or "", reverse=direction < 0); return self
    def limit(self, n): self.docs = self.docs[:n]; return self
    async def to_list(self, n): return self.docs[:n]


class Collection:
    def __init__(self, docs=None): self.docs = list(docs or [])
    async def find_one(self, query, *args): return next((d for d in self.docs if matches(d, query)), None)
    def find(self, query): return Cursor([d for d in self.docs if matches(d, query)])


def fixture(stage="draft"):
    proposal_id, old_id, property_id, tenant_id = str(ObjectId()), ObjectId(), ObjectId(), ObjectId()
    proposal = {"_id": ObjectId(proposal_id), "status": "draft", "recommendation": "renew",
                "lease_id": str(old_id), "property_id": str(property_id), "tenant_id": str(tenant_id),
                "property_address": "123 Test St", "lease_end_date": "2026-08-31", "proposed_rent": 1200,
                "tenant_email": "must-not-leak@example.com", "tenant_phone": "8065551111"}
    old = {"_id": old_id, "status": "active", "property_id": str(property_id),
           "tenant_id": str(tenant_id), "unit_id": None}
    prop = {"_id": property_id, "address": "private"}
    tenant = {"_id": tenant_id, "email": "canonical-private@example.com", "phone": "8065552222"}
    class DB: pass
    db = DB(); db.lease_renewal_proposals = Collection([proposal]); db.rental_contracts = Collection([old])
    db.properties = Collection([prop]); db.tenants = Collection([tenant])
    db.lease_renewal_notification_outbox = Collection(); db.lease_renewal_responses = Collection()
    db.lease_renewal_rollovers = Collection()
    if stage != "draft":
        proposal["status"] = "approved"
        delivery = {"_id": ObjectId(), "proposal_id": proposal_id, "lease_id": str(old_id),
                    "property_id": str(property_id), "tenant_id": str(tenant_id), "status": "sent",
                    "attempts": 1, "provider_evidence_ref": "secret-evidence", "claim_id": "secret-claim"}
        db.lease_renewal_notification_outbox.docs.append(delivery)
    if stage in {"accepted", "contract", "completed", "recovery"}:
        response = {"_id": ObjectId(proposal_id), "proposal_id": proposal_id, "lease_id": str(old_id),
                    "property_id": str(property_id), "tenant_id": str(tenant_id), "decision": "accept",
                    "terms_digest": "d" * 64}
        db.lease_renewal_responses.docs.append(response)
    if stage in {"contract", "completed", "recovery"}:
        new_id = _renewal_contract_id(proposal_id)
        new = {"_id": new_id, "status": "pending_signatures", "property_id": str(property_id),
               "tenant_id": str(tenant_id), "unit_id": None, "start_date": "2026-09-01",
               "end_date": "2027-08-31", "renewal_source": {"proposal_id": proposal_id,
               "prior_contract_id": str(old_id), "terms_digest": "d" * 64},
               "tenant_email": "must-not-leak@example.com"}
        db.rental_contracts.docs.append(new)
    if stage in {"completed", "recovery"}:
        new = db.rental_contracts.docs[1]; new["status"] = "active" if stage == "completed" else "pending_activation"
        rollover = {"_id": new["_id"], "proposal_id": proposal_id, "prior_contract_id": str(old_id),
                    "renewal_contract_id": str(new["_id"]), "property_id": str(property_id),
                    "tenant_id": str(tenant_id), "state": "completed" if stage == "completed" else "recovery_required",
                    "stage": "complete" if stage == "completed" else "activate_renewal",
                    "claim_id": "never-return-this", "manual_recovery": {"proposed_by": "private-admin"}}
        db.lease_renewal_rollovers.docs.append(rollover)
    return db, proposal_id, proposal


def result(stage):
    db, proposal_id, _ = fixture(stage)
    flow = run(status._verified_workflow(db, proposal_id))
    return status._view(proposal_id, flow)


def test_progression_actions_are_server_derived():
    assert result("draft")["next_action"] == "review_or_approve_proposal"
    assert result("sent")["next_action"] == "await_tenant_response"
    assert result("accepted")["next_action"] == "generate_contract"
    assert result("contract")["next_action"] == "collect_contract_signatures"
    assert result("completed")["next_action"] == "completed"
    assert result("recovery")["next_action"] == "inspect_or_recover_rollover"


def test_view_is_read_only_and_never_leaks_sensitive_fields():
    view = result("recovery")
    encoded = repr(view).lower()
    assert view["read_only"] is True and view["integrity"] == "verified"
    assert view["rollover"]["automatic_retry_allowed"] is False
    for forbidden in ("email", "phone", "address", "claim", "provider", "evidence", "proposed_by"):
        assert forbidden not in encoded


def test_ambiguous_delivery_requires_inspection_not_retry():
    db, proposal_id, _ = fixture("sent")
    db.lease_renewal_notification_outbox.docs[0]["status"] = "ambiguous_provider_result"
    flow = run(status._verified_workflow(db, proposal_id)); view = status._view(proposal_id, flow)
    assert view["next_action"] == "inspect_delivery"
    assert view["delivery"]["manual_review_required"] is True


def test_cross_tenant_delivery_and_response_before_send_fail_closed():
    db, proposal_id, _ = fixture("sent")
    db.lease_renewal_notification_outbox.docs[0]["tenant_id"] = str(ObjectId())
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_delivery_binding_changed"
    db, proposal_id, _ = fixture("accepted")
    db.lease_renewal_notification_outbox.docs[0]["status"] = "failed"
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_response_without_delivery"


def test_contract_requires_acceptance_and_exact_source():
    db, proposal_id, _ = fixture("contract")
    db.lease_renewal_responses.docs.clear()
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_contract_without_acceptance"
    db, proposal_id, _ = fixture("contract")
    db.rental_contracts.docs[1]["renewal_source"]["prior_contract_id"] = str(ObjectId())
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_contract_source_changed"


def test_delivery_requires_approval_and_contract_terms_require_exact_digest():
    db, proposal_id, proposal = fixture("sent"); proposal["status"] = "draft"
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_delivery_before_approval"
    db, proposal_id, _ = fixture("contract")
    db.rental_contracts.docs[1]["renewal_source"]["terms_digest"] = "e" * 64
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_contract_terms_changed"


def test_rollover_stage_is_bounded_and_terminal_contract_needs_completion():
    db, proposal_id, _ = fixture("recovery")
    db.lease_renewal_rollovers.docs[0]["stage"] = "claim=secret-value"
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_rollover_stage_invalid"
    db, proposal_id, _ = fixture("contract"); db.rental_contracts.docs[1]["status"] = "terminated"
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_terminal_contract_without_completed_rollover"


def test_duplicate_stage_records_and_unknown_states_fail_closed():
    db, proposal_id, _ = fixture("sent")
    db.lease_renewal_notification_outbox.docs.append(dict(db.lease_renewal_notification_outbox.docs[0], _id=ObjectId()))
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_multiple_delivery_records"
    db, proposal_id, proposal = fixture("draft"); proposal["status"] = "mystery"
    with pytest.raises(HTTPException) as exc: run(status._verified_workflow(db, proposal_id))
    assert exc.value.detail == "renewal_workflow_proposal_state_invalid"


def test_module_contains_no_mutation_or_provider_calls():
    source = open("rental/lease_renewal_workflow_status_router.py", encoding="utf-8").read()
    for forbidden in ("update_one(", "insert_one(", "delete_one(", "sendgrid", "twilio", "requests."):
        assert forbidden not in source.lower()


def test_read_only_list_uses_existing_proposals_and_isolates_corrupt_rows():
    db, proposal_id, _ = fixture("draft")
    listed = run(status._list_workflows(db, 50))
    assert listed["read_only"] is True and listed["total"] == 1
    assert listed["items"][0]["integrity"] == "verified"
    assert listed["items"][0]["summary"]["proposed_rent"] >= 0
    db.lease_renewal_proposals.docs[0]["status"] = "corrupt"
    listed = run(status._list_workflows(db, 50))
    assert listed["items"] == [{
        "ok": False, "read_only": True, "integrity": "unavailable",
        "proposal_id": proposal_id, "summary": None, "next_action": None,
    }]
