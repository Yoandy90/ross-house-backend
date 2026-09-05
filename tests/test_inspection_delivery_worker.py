import asyncio
from pathlib import Path

import rental.inspection_delivery_worker as worker


def run(coro):
    return asyncio.run(coro)


def test_worker_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("INSPECTION_DELIVERY_WORKER_ENABLED", raising=False)
    assert worker.worker_enabled() is False
    monkeypatch.setenv("INSPECTION_DELIVERY_WORKER_ENABLED", "true")
    assert worker.worker_enabled() is True


def test_process_available_is_bounded_and_reports_outcomes(monkeypatch):
    intents = [{"_id": "1"}, {"_id": "2"}]

    async def fake_claim(_db, _worker_id):
        return intents.pop(0) if intents else None

    async def fake_process(_db, intent, _sender):
        return "sent" if intent["_id"] == "1" else "retryable_failure"

    monkeypatch.setattr(worker, "claim_next", fake_claim)
    monkeypatch.setattr(worker, "process_claimed", fake_process)
    outcomes = run(worker.process_available(object(), worker_id="test", batch_size=10))
    assert outcomes == {"sent": 1, "retryable_failure": 1, "idle": 1}


def test_batch_size_is_capped(monkeypatch):
    calls = 0

    async def fake_claim(_db, _worker_id):
        nonlocal calls
        calls += 1
        return {"_id": str(calls)}

    async def fake_process(_db, _intent, _sender):
        return "sent"

    monkeypatch.setattr(worker, "claim_next", fake_claim)
    monkeypatch.setattr(worker, "process_claimed", fake_process)
    outcomes = run(worker.process_available(object(), worker_id="test", batch_size=999))
    assert calls == worker.MAX_BATCH_SIZE
    assert outcomes == {"sent": worker.MAX_BATCH_SIZE}


def test_server_uses_global_policy_and_explicit_worker_opt_in():
    source = Path("server.py").read_text()
    global_gate = source.index("if should_disable_background_jobs():")
    worker_gate = source.index("if worker_enabled():")
    worker_start = source.index("asyncio.create_task(inspection_delivery_loop(db))")
    assert global_gate < worker_gate < worker_start


def test_stale_claim_recovery_never_replays_provider_started_work():
    source = Path("rental/inspection_delivery_router.py").read_text()
    assert '"provider_started_at": {"$exists": False}' in source
    assert '"claimed_at": {"$lt": stale_before}' in source
