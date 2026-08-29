import asyncio
from datetime import datetime, timedelta

from bson import ObjectId

import rental.property_sync_cron as sync


def run(coro):
    return asyncio.run(coro)


class Cursor:
    def __init__(self, docs):
        self.docs = list(docs)
    def __aiter__(self):
        self._iter = iter(self.docs)
        return self
    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration
    def limit(self, n):
        self.docs = self.docs[:n]
        return self
    async def to_list(self, n):
        return self.docs[:n]


class Properties:
    def __init__(self, docs):
        self.docs = list(docs)
        self.updates = []
    def find(self, _query):
        return Cursor(self.docs)
    async def update_one(self, query, update):
        self.updates.append((query, update))
        class Result:
            matched_count = 1
        return Result()


class Contracts:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, query, *args, **kwargs):
        property_id = str(query.get("property_id") or "")
        if "lifecycle_claim_id" in query:
            for doc in self.docs:
                claim = doc.get("lifecycle_claim_id")
                if str(doc.get("property_id")) == property_id and claim not in (None, ""):
                    return doc
            return None
        for doc in self.docs:
            if str(doc.get("property_id")) == property_id:
                return doc
        return None

    def find(self, query):
        matches = [d for d in self.docs
                   if str(d.get("property_id")) == str(query.get("property_id"))
                   and d.get("status") == query.get("status")]
        return Cursor(matches)


class DB:
    def __init__(self, props, contracts):
        self.properties = Properties(props)
        self.rental_contracts = Contracts(contracts)


def test_sync_does_not_choose_between_multiple_active_contracts():
    pid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "available"}],
        [
            {"_id": ObjectId(), "property_id": str(pid), "tenant_id": str(ObjectId()), "status": "active"},
            {"_id": ObjectId(), "property_id": str(pid), "tenant_id": str(ObjectId()), "status": "active"},
        ],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["ambiguous"] == 1
    assert db.properties.updates == []


def test_sync_never_overwrites_other_contract_projection():
    pid = ObjectId()
    existing_contract = ObjectId()
    canonical_contract = ObjectId()
    db = DB(
        [{"_id": pid, "status": "rented", "current_contract_id": str(existing_contract)}],
        [{"_id": canonical_contract, "property_id": str(pid), "tenant_id": str(ObjectId()), "status": "active"}],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["conflicts"] == 1
    assert db.properties.updates == []


def test_sync_repairs_empty_projection_with_cas():
    pid = ObjectId()
    cid = ObjectId()
    tid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "available", "current_contract_id": None}],
        [{"_id": cid, "property_id": str(pid), "tenant_id": str(tid), "status": "active"}],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["fixed"] == 1
    query, update = db.properties.updates[0]
    assert query["_id"] == pid
    assert "$or" in query
    assert update["$set"]["current_contract_id"] == str(cid)
    assert update["$set"]["current_tenant_id"] == str(tid)


def test_sync_does_not_clear_claim_when_no_active_contract_but_projection_exists():
    pid = ObjectId()
    cid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "rented", "current_contract_id": str(cid)}],
        [],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["conflicts"] == 1
    assert db.properties.updates == []


def test_sync_skips_property_with_live_mutation_claim():
    pid = ObjectId()
    db = DB(
        [{
            "_id": pid,
            "status": "maintenance",
            "mutation_lock": {
                "token": "owned",
                "operation": "unit_topology_create",
                "expires_at": datetime.utcnow() + timedelta(minutes=1),
            },
        }],
        [],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["skipped_mutation"] == 1
    assert db.properties.updates == []


def test_sync_fails_closed_on_malformed_mutation_claim():
    pid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "maintenance", "mutation_lock": {"token": "owned"}}],
        [],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["skipped_mutation"] == 1
    assert db.properties.updates == []


def test_sync_skips_property_with_lifecycle_recovery_claim():
    pid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "maintenance"}],
        [{
            "_id": ObjectId(),
            "property_id": str(pid),
            "status": "pending_activation",
            "lifecycle_claim_id": "retained-claim",
        }],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["skipped_recovery"] == 1
    assert db.properties.updates == []
