import asyncio
from threading import Lock

import pytest
from fastapi import HTTPException

from rental import security
from rental.runtime_secrets import resolve_runtime_secret


def test_production_secret_is_required():
    with pytest.raises(RuntimeError, match="Missing required production secret"):
        resolve_runtime_secret(
            "TENANT_JWT_SECRET",
            purpose="test signing",
            environ={"ENVIRONMENT": "production"},
        )


def test_railway_production_name_is_recognized():
    with pytest.raises(RuntimeError):
        resolve_runtime_secret(
            "JWT_SECRET_KEY",
            "JWT_SECRET",
            purpose="test signing",
            environ={"RAILWAY_ENVIRONMENT_NAME": "production"},
        )


def test_alias_and_nonproduction_fallback():
    assert resolve_runtime_secret(
        "JWT_SECRET_KEY",
        "JWT_SECRET",
        purpose="test signing",
        environ={"ENVIRONMENT": "production", "JWT_SECRET": "configured"},
    ) == "configured"
    generated = resolve_runtime_secret(
        "TENANT_JWT_SECRET",
        purpose="test signing",
        environ={"ENVIRONMENT": "test"},
    )
    assert len(generated) >= 64


class AtomicCounterCollection:
    def __init__(self):
        self.docs = {}
        self.lock = Lock()
        self.calls = []

    async def find_one_and_update(self, query, update, **options):
        await asyncio.sleep(0)
        with self.lock:
            self.calls.append((query, update, options))
            doc = self.docs.get(query["_id"])
            if doc is None:
                doc = {"_id": query["_id"], **update["$setOnInsert"], "count": 0}
                self.docs[query["_id"]] = doc
            doc["count"] += update["$inc"]["count"]
            return dict(doc)


class FakeDB:
    def __init__(self):
        self.rate_limit_windows = AtomicCounterCollection()


def test_concurrent_requests_reserve_exactly_the_limit(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(security, "get_db", lambda: db)

    async def attempt():
        try:
            await security.check_rate_limit_persistent(
                "login", "person@example.test", max_requests=5, window_seconds=300
            )
            return "accepted"
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.detail == security.RATE_LIMIT_429
            return "blocked"

    async def run_attempts():
        return await asyncio.gather(*(attempt() for _ in range(50)))

    results = asyncio.run(run_attempts())
    assert results.count("accepted") == 5
    assert results.count("blocked") == 45
    assert len(db.rate_limit_windows.calls) == 50
    assert "person@example.test" not in repr(db.rate_limit_windows.calls)
    assert all(call[2]["upsert"] is True for call in db.rate_limit_windows.calls)
