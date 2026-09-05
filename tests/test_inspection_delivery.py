import asyncio
from pathlib import Path

from bson import ObjectId

import rental.inspection_delivery_router as delivery


def run(coro):
    return asyncio.run(coro)


class Result:
    def __init__(self, matched_count=1, inserted_id=None):
        self.matched_count = matched_count
        self.inserted_id = inserted_id or ObjectId()


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
    def limit(self, count):
        self.rows = self.rows[:count]
        return self
    async def to_list(self, count):
        return self.rows[:count]


class Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
    async def find_one(self, query):
        for row in self.rows:
            ok = True
            for key, value in query.items():
                if isinstance(value, dict) and "$exists" in value:
                    ok = ok and ((key in row) == value["$exists"])
                else:
                    ok = ok and row.get(key) == value
            if ok:
                return row
        return None
    def find(self, query):
        return Cursor([row for row in self.rows if all(row.get(k) == v for k, v in query.items())])
    async def insert_one(self, document):
        row = {**document, "_id": ObjectId()}
        self.rows.append(row)
        return Result(inserted_id=row["_id"])
    async def update_one(self, query, update):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(update.get("$set", {}))
                return Result()
        return Result(0)


class DB:
    pass


def fixture():
    inspection_id, tenant_id, intent_id = ObjectId(), ObjectId(), ObjectId()
    signatures = {
        "admin": {"signer_name": "Inspector", "signature_sha256": "a" * 64, "signed_at": "2026-09-05"},
        "tenant": {"signer_name": "Tenant", "signature_sha256": "b" * 64, "signed_at": "2026-09-05"},
    }
    inspection = {
        "_id": inspection_id,
        "status": "completed",
        "tenant_id": str(tenant_id),
        "tenant_name": "Canonical Tenant",
        "property_name": "Safe Home",
        "type": "routine",
        "rooms": {},
        "signatures": signatures,
    }
    intent = {
        "_id": intent_id,
        "inspection_id": str(inspection_id),
        "tenant_id": str(tenant_id),
        "status": "claimed",
        "claim_id": "claim",
        "attempts": 1,
    }
    db = DB()
    db.inspections = Collection([inspection])
    db.tenants = Collection([{"_id": tenant_id, "email_normalized": "tenant@example.com"}])
    db.inspection_delivery_outbox = Collection([intent])
    return db, inspection, intent


def test_pdf_is_real_and_uses_canonical_fields():
    _, inspection, _ = fixture()
    content = delivery.build_inspection_pdf(inspection)
    assert content.startswith(b"%PDF-")
    assert len(content) > 500


def test_queue_ignores_client_recipient_and_is_bound_to_tenant():
    db, inspection, _ = fixture()
    response = run(delivery.queue_inspection_email(
        str(inspection["_id"]), admin={"email": "admin@example.com"}, db=db
    ))
    assert response["queued"] is True
    queued = db.inspection_delivery_outbox.rows[-1]
    assert queued["tenant_id"] == inspection["tenant_id"]
    assert "email" not in queued
    assert queued["dedupe_key"] == f'inspection:{inspection["_id"]}:completed'


def test_sender_re_resolves_canonical_recipient_and_marks_sent():
    db, _, intent = fixture()
    async def sender(payload):
        assert payload["email"] == "tenant@example.com"
        assert payload["pdf"].startswith(b"%PDF-")
        return {"provider": "sendgrid", "provider_message_id": "m-1"}
    assert run(delivery.process_claimed(db, intent, sender)) == "sent"
    assert intent["status"] == "sent"
    assert "email" not in intent


def test_timeout_is_ambiguous_and_never_retried():
    db, _, intent = fixture()
    async def timeout(_payload):
        raise delivery.ProviderAmbiguousResult("provider_transport_or_timeout")
    assert run(delivery.process_claimed(db, intent, timeout)) == "ambiguous_provider_result"
    assert intent["automatic_retry_allowed"] is False


def test_stale_tenant_binding_fails_before_provider():
    db, _, intent = fixture()
    intent["tenant_id"] = str(ObjectId())
    called = False
    async def sender(_payload):
        nonlocal called
        called = True
    assert run(delivery.process_claimed(db, intent, sender)) == "failed"
    assert called is False


def test_delivery_routes_are_registered_before_legacy_routes():
    source = Path("rental/auth_metrics.py").read_text()
    assert source.index("router.routes.extend(inspection_delivery_router.routes)") < source.index(
        "router.routes.extend(inspection_security_router.routes)"
    )
    delivery_source = Path("rental/inspection_delivery_router.py").read_text()
    assert "find_one_and_update" in delivery_source
    assert "ambiguous_provider_result" in delivery_source
    assert "dedupe_key" in delivery_source
