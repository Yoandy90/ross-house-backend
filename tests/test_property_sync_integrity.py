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
        self.docs = [dict(d) for d in docs]
        self.updates = []

    def find(self, _query):
        # Mongo cursors return snapshots of documents, not references that our
        # fake lock mutation should retroactively alter.
        return Cursor([dict(d) for d in self.docs])

    async def find_one(self, query, *args, **kwargs):
        oid = query.get("_id")
        for doc in self.docs:
            if doc.get("_id") == oid:
                return dict(doc)
        return None

    async def update_one(self, query, update):
        self.updates.append((query, update))
        oid = query.get("_id")
        for doc in self.docs:
            if doc.get("_id") != oid:
                continue

            token_required = query.get("mutation_lock.token")
            if token_required is not None:
                if (doc.get("mutation_lock") or {}).get("token") != token_required:
                    break

            lock_or = query.get("$or")
            if lock_or and any("mutation_lock" in clause or "mutation_lock.expires_at" in clause for clause in lock_or):
                lock = doc.get("mutation_lock")
                now = datetime.utcnow()
                can_lock = not lock or (
                    isinstance(lock, dict)
                    and isinstance(lock.get("expires_at"), datetime)
                    and lock["expires_at"] < now
                )
                if not can_lock:
                    break

            if "$set" in update:
                for key, value in update["$set"].items():
                    doc[key] = value
            if "$unset" in update:
                for key in update["$unset"]:
                    doc.pop(key, None)

            class Result:
                matched_count = 1
            return Result()

        class Result:
            matched_count = 0
        return Result()


class Contracts:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]
        self.before_active_recheck = None

    async def find_one(self, query, *args, **kwargs):
        property_id = str(query.get("property_id") or "")
        if "lifecycle_claim_id" in query:
            for doc in self.docs:
                claim = doc.get("lifecycle_claim_id")
                if str(doc.get("property_id")) == property_id and claim not in (None, ""):
                    return dict(doc)
            return None

        if query.get("status") == "active" and query.get("_id") is not None:
            if self.before_active_recheck:
                hook = self.before_active_recheck
                self.before_active_recheck = None
                hook(self)
            for doc in self.docs:
                if (doc.get("_id") == query.get("_id")
                        and str(doc.get("property_id")) == property_id
                        and doc.get("status") == "active"):
                    return dict(doc)
            return None

        for doc in self.docs:
            if str(doc.get("property_id")) == property_id:
                return dict(doc)
        return None

    def find(self, query):
        matches = [dict(d) for d in self.docs
                   if str(d.get("property_id")) == str(query.get("property_id"))
                   and d.get("status") == query.get("status")]
        return Cursor(matches)


class DB:
    def __init__(self, props, contracts):
        self.properties = Properties(props)
        self.rental_contracts = Contracts(contracts)


def projection_updates(db):
    return [entry for entry in db.properties.updates
            if entry[1].get("$set", {}).get("status") in {"available", "rented"}]


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
    assert projection_updates(db) == []
    assert "mutation_lock" not in db.properties.docs[0]


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
    assert projection_updates(db) == []


def test_sync_repairs_empty_projection_under_exact_lock_token():
    pid = ObjectId()
    cid = ObjectId()
    tid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "available", "current_contract_id": None}],
        [{"_id": cid, "property_id": str(pid), "tenant_id": str(tid), "status": "active"}],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["fixed"] == 1
    updates = projection_updates(db)
    assert len(updates) == 1
    query, update = updates[0]
    assert query["_id"] == pid
    assert query["mutation_lock.token"]
    assert "$or" in query
    assert update["$set"]["current_contract_id"] == str(cid)
    assert update["$set"]["current_tenant_id"] == str(tid)
    assert "mutation_lock" not in db.properties.docs[0]


def test_sync_rechecks_active_contract_before_projection_write():
    pid = ObjectId()
    cid = ObjectId()
    tid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "available", "current_contract_id": None}],
        [{"_id": cid, "property_id": str(pid), "tenant_id": str(tid), "status": "active"}],
    )

    def terminate_before_recheck(contracts):
        contracts.docs[0]["status"] = "terminated"

    db.rental_contracts.before_active_recheck = terminate_before_recheck
    report = run(sync.reconcile_property_statuses(db))
    assert report["conflicts"] == 1
    assert projection_updates(db) == []
    assert "mutation_lock" not in db.properties.docs[0]


def test_sync_does_not_clear_claim_when_no_active_contract_but_projection_exists():
    pid = ObjectId()
    cid = ObjectId()
    db = DB(
        [{"_id": pid, "status": "rented", "current_contract_id": str(cid)}],
        [],
    )
    report = run(sync.reconcile_property_statuses(db))
    assert report["conflicts"] == 1
    assert projection_updates(db) == []


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


def test_sync_skips_property_with_lifecycle_recovery_claim_and_releases_lock():
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
    assert projection_updates(db) == []
    assert "mutation_lock" not in db.properties.docs[0]
