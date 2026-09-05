import asyncio

import pytest
from fastapi import HTTPException

import rental.staging_renewal_fixture_router as fixtures


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, deleted_count=0, matched_count=0):
        self.deleted_count = deleted_count
        self.matched_count = matched_count


def value(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def matches(doc, query):
    for key, expected in query.items():
        actual = value(doc, key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class Collection:
    def __init__(self):
        self.docs = []
        self.delete_queries = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def find_one(self, query):
        return next((doc for doc in self.docs if matches(doc, query)), None)

    async def update_one(self, query, update):
        for doc in self.docs:
            if matches(doc, query):
                doc.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    doc.pop(key, None)
                return Result(matched_count=1)
        return Result(matched_count=0)

    async def delete_many(self, query):
        self.delete_queries.append(query)
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not matches(doc, query)]
        return Result(before - len(self.docs))


class DB:
    def __init__(self):
        self.properties = Collection()
        self.tenants = Collection()
        self.rental_contracts = Collection()
        self.lease_renewal_proposals = Collection()
        self.lease_renewal_responses = Collection()
        self.lease_renewal_notification_outbox = Collection()
        self.lease_renewal_rollovers = Collection()
        self.app_users = Collection()
        self.auth_sessions = Collection()


def allow(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DB_NAME", "ross_house_staging")
    monkeypatch.setenv("DISABLE_BACKGROUND_JOBS", "true")
    monkeypatch.setenv("STAGING_FIXTURES_ENABLED", "true")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)


def test_create_inspect_and_exact_cleanup(monkeypatch):
    allow(monkeypatch)
    db = DB()
    created = run(
        fixtures.create_renewal_source(
            {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {"role": "admin"}
        )
    )
    assert created["ok"] is True and created["synthetic"] is True
    assert created["marker"].startswith("staging-renewal-")
    assert len(db.properties.docs) == len(db.tenants.docs) == len(db.rental_contracts.docs) == 1

    inspected = run(
        fixtures.inspect_renewal_source(created["marker"], db, {"role": "admin"})
    )
    assert inspected["present"] == {
        "property": True,
        "tenant": True,
        "contract": True,
    }
    assert inspected["consistent"] is True
    assert "email" not in inspected and "phone" not in inspected

    deleted = run(
        fixtures.delete_renewal_source(
            created["marker"], "DELETE_SYNTHETIC_RENEWAL", db, {"role": "admin"}
        )
    )
    assert deleted["clean"] is True
    assert deleted["deleted"] == {
        "rental_contracts": 1,
        "tenants": 1,
        "properties": 1,
    }
    for collection in (db.properties, db.tenants, db.rental_contracts):
        assert collection.delete_queries == [
            {"staging_fixture_marker": created["marker"], "synthetic": True}
        ]


def test_confirmations_fail_before_writes(monkeypatch):
    allow(monkeypatch)
    db = DB()
    with pytest.raises(HTTPException) as create_error:
        run(fixtures.create_renewal_source({"confirmation": "wrong"}, db, {}))
    assert create_error.value.detail == "fixture_create_confirmation_required"
    assert not db.properties.docs

    marker = "staging-renewal-" + ("a" * 32)
    with pytest.raises(HTTPException) as delete_error:
        run(fixtures.delete_renewal_source(marker, "wrong", db, {}))
    assert delete_error.value.detail == "fixture_delete_confirmation_required"


def test_policy_blocks_route_when_flag_is_off(monkeypatch):
    allow(monkeypatch)
    monkeypatch.setenv("STAGING_FIXTURES_ENABLED", "false")
    with pytest.raises(HTTPException) as exc:
        run(fixtures.create_renewal_source(
            {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, DB(), {}
        ))
    assert exc.value.status_code == 404
    assert exc.value.detail == "staging_fixtures_not_enabled"


def test_cleanup_refuses_when_proposal_exists(monkeypatch):
    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))
    db.lease_renewal_proposals.docs.append(
        {"lease_id": created["contract_id"], "status": "draft"}
    )
    with pytest.raises(HTTPException) as exc:
        run(fixtures.delete_renewal_source(
            created["marker"], "DELETE_SYNTHETIC_RENEWAL", db, {}
        ))
    assert exc.value.status_code == 409
    assert exc.value.detail == "fixture_has_derived_lifecycle_data"
    assert db.properties.docs


def test_server_registers_fixture_router_only_for_staging():
    source = open("server.py", encoding="utf-8").read()
    assert 'if _ENV == "staging":' in source
    assert "staging_renewal_fixture_router" in source


def test_full_lifecycle_cleanup_verifies_and_deletes_exact_graph(monkeypatch):
    from bson import ObjectId
    from rental.lease_renewal_contract_generation_router import _renewal_contract_id

    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))
    proposal_id = ObjectId()
    proposal_text = str(proposal_id)
    old_id = created["contract_id"]
    property_id = created["property_id"]
    tenant_id = created["tenant_id"]
    renewal_id = _renewal_contract_id(proposal_text)

    db.lease_renewal_proposals.docs.append({
        "_id": proposal_id, "lease_id": old_id,
        "property_id": property_id, "tenant_id": tenant_id,
    })
    db.lease_renewal_responses.docs.append({
        "proposal_id": proposal_text, "lease_id": old_id,
        "property_id": property_id, "tenant_id": tenant_id,
    })
    db.lease_renewal_notification_outbox.docs.append({
        "proposal_id": proposal_text, "tenant_id": tenant_id,
    })
    db.rental_contracts.docs.append({
        "_id": renewal_id, "property_id": property_id, "tenant_id": tenant_id,
        "renewal_source": {
            "proposal_id": proposal_text, "prior_contract_id": old_id,
        },
    })
    db.lease_renewal_rollovers.docs.append({
        "_id": renewal_id, "proposal_id": proposal_text,
        "prior_contract_id": old_id, "renewal_contract_id": str(renewal_id),
        "property_id": property_id, "tenant_id": tenant_id,
    })

    result = run(fixtures.delete_renewal_lifecycle(
        created["marker"], "DELETE_SYNTHETIC_RENEWAL", db, {}
    ))
    assert result["clean"] is True
    assert all(not collection.docs for collection in (
        db.properties, db.tenants, db.rental_contracts,
        db.lease_renewal_proposals, db.lease_renewal_responses,
        db.lease_renewal_notification_outbox, db.lease_renewal_rollovers,
    ))


def test_full_cleanup_fails_closed_on_foreign_binding(monkeypatch):
    from bson import ObjectId

    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))
    db.lease_renewal_proposals.docs.append({
        "_id": ObjectId(), "lease_id": created["contract_id"],
        "property_id": str(ObjectId()), "tenant_id": created["tenant_id"],
    })
    with pytest.raises(HTTPException) as exc:
        run(fixtures.delete_renewal_lifecycle(
            created["marker"], "DELETE_SYNTHETIC_RENEWAL", db, {}
        ))
    assert exc.value.detail == "fixture_proposal_binding_changed"
    assert db.properties.docs and db.rental_contracts.docs


def test_simulated_delivery_marks_only_exact_synthetic_intent(monkeypatch):
    from bson import ObjectId

    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))
    proposal_id = ObjectId()
    db.lease_renewal_proposals.docs.append({
        "_id": proposal_id,
        "lease_id": created["contract_id"],
        "property_id": created["property_id"],
        "tenant_id": created["tenant_id"],
        "status": "approved",
    })
    db.lease_renewal_notification_outbox.docs.append({
        "_id": ObjectId(),
        "proposal_id": str(proposal_id),
        "lease_id": created["contract_id"],
        "property_id": created["property_id"],
        "tenant_id": created["tenant_id"],
        "status": "pending",
        "attempts": 0,
    })

    result = run(fixtures.simulate_renewal_delivery(
        created["marker"],
        {"confirmation": "SIMULATE_SYNTHETIC_DELIVERY"},
        db,
        {},
    ))
    assert result["status"] == "sent"
    assert result["attempts"] == 1
    intent = db.lease_renewal_notification_outbox.docs[0]
    assert intent["status"] == "sent"
    assert intent["provider"] == "staging-simulator"
    assert intent["staging_simulated"] is True
    assert intent["automatic_retry_allowed"] is False


def test_simulated_delivery_fails_closed_on_foreign_binding(monkeypatch):
    from bson import ObjectId

    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))
    proposal_id = ObjectId()
    db.lease_renewal_proposals.docs.append({
        "_id": proposal_id,
        "lease_id": created["contract_id"],
        "property_id": created["property_id"],
        "tenant_id": created["tenant_id"],
        "status": "approved",
    })
    db.lease_renewal_notification_outbox.docs.append({
        "_id": ObjectId(),
        "proposal_id": str(proposal_id),
        "lease_id": created["contract_id"],
        "property_id": created["property_id"],
        "tenant_id": str(ObjectId()),
        "status": "pending",
        "attempts": 0,
    })

    with pytest.raises(HTTPException) as exc:
        run(fixtures.simulate_renewal_delivery(
            created["marker"],
            {"confirmation": "SIMULATE_SYNTHETIC_DELIVERY"},
            db,
            {},
        ))
    assert exc.value.detail == "fixture_outbox_binding_changed"
    assert db.lease_renewal_notification_outbox.docs[0]["status"] == "pending"


def test_synthetic_tenant_session_is_linked_and_session_bound(monkeypatch):
    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))

    async def issue_token(user_id, email, role, request):
        assert role == "tenant"
        assert email.endswith("@invalid.example")
        db.auth_sessions.docs.append({"user_id": user_id, "sid": "a" * 32})
        return "synthetic-session-token"

    monkeypatch.setattr(fixtures, "create_session_token", issue_token)
    result = run(fixtures.create_renewal_tenant_session(
        created["marker"],
        None,
        {"confirmation": "CREATE_SYNTHETIC_TENANT_SESSION"},
        db,
        {},
    ))
    assert result["token"] == "synthetic-session-token"
    assert result["session_bound"] is True
    assert len(db.app_users.docs) == len(db.auth_sessions.docs) == 1
    user = db.app_users.docs[0]
    tenant = db.tenants.docs[0]
    assert user["role"] == "tenant"
    assert user["tenant_id"] == created["tenant_id"]
    assert tenant["app_user_id"] == str(user["_id"])
    assert "password_hash" not in user


def test_tenant_session_failure_rolls_back_identity(monkeypatch):
    allow(monkeypatch)
    db = DB()
    created = run(fixtures.create_renewal_source(
        {"confirmation": "CREATE_SYNTHETIC_RENEWAL"}, db, {}
    ))

    async def fail_token(user_id, email, role, request):
        db.auth_sessions.docs.append({"user_id": user_id, "sid": "b" * 32})
        raise RuntimeError("session failed")

    monkeypatch.setattr(fixtures, "create_session_token", fail_token)
    with pytest.raises(HTTPException) as exc:
        run(fixtures.create_renewal_tenant_session(
            created["marker"],
            None,
            {"confirmation": "CREATE_SYNTHETIC_TENANT_SESSION"},
            db,
            {},
        ))
    assert exc.value.detail == "fixture_tenant_session_rolled_back"
    assert db.app_users.docs == []
    assert db.auth_sessions.docs == []
    assert "app_user_id" not in db.tenants.docs[0]
