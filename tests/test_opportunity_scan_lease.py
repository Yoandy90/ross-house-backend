from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from rental import deal_finder_cron
from rental.opportunity_scan_lease import (
    ScanLease, acquire_scan_lease, release_scan_lease, renew_scan_lease,
)


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def database():
    collection = SimpleNamespace(
        find_one_and_update=AsyncMock(), update_one=AsyncMock(),
    )
    return SimpleNamespace(app_settings=collection)


@pytest.mark.asyncio
async def test_acquire_uses_one_atomic_expiring_lease_claim():
    db = database()
    db.app_settings.find_one_and_update.return_value = {
        "_id": "opportunity_scan_lease:deal_finder", "lease_owner": "worker-a",
        "lease_generation": 7,
    }
    lease = await acquire_scan_lease(
        db, "deal_finder", now=NOW, owner="worker-a", ttl_seconds=600,
    )
    assert lease == ScanLease("opportunity_scan_lease:deal_finder", "worker-a", 7, 600)
    query, update = db.app_settings.find_one_and_update.await_args.args
    assert query["_id"] == lease.document_id
    assert {"lease_until": {"$lte": NOW}} in query["$or"]
    assert update["$inc"] == {"lease_generation": 1}
    assert db.app_settings.find_one_and_update.await_args.kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_locked_duplicate_claim_returns_busy_without_mutation_retry():
    db = database()
    db.app_settings.find_one_and_update.side_effect = DuplicateKeyError("locked")
    assert await acquire_scan_lease(db, "deal_finder", now=NOW, owner="worker-b") is None
    assert db.app_settings.find_one_and_update.await_count == 1


@pytest.mark.asyncio
async def test_renew_and_release_are_owner_and_generation_cas():
    db = database()
    db.app_settings.update_one.return_value = SimpleNamespace(modified_count=1)
    lease = ScanLease("opportunity_scan_lease:deal_finder", "worker-a", 4, 600)
    assert await renew_scan_lease(db, lease, now=NOW) is True
    renew_filter = db.app_settings.update_one.await_args_list[0].args[0]
    assert renew_filter["lease_owner"] == "worker-a"
    assert renew_filter["lease_generation"] == 4
    assert renew_filter["lease_until"] == {"$gt": NOW}
    assert await release_scan_lease(db, lease) is True
    release_filter = db.app_settings.update_one.await_args_list[1].args[0]
    assert release_filter == {"_id": lease.document_id, "lease_owner": "worker-a",
                              "lease_generation": 4}


@pytest.mark.asyncio
async def test_batch_returns_stable_busy_result_without_scanning(monkeypatch):
    db = database()
    core = AsyncMock()
    monkeypatch.setattr(deal_finder_cron, "acquire_scan_lease", AsyncMock(return_value=None))
    monkeypatch.setattr(deal_finder_cron, "_run_auto_scan_batch_unlocked", core)
    assert await deal_finder_cron.run_auto_scan_batch(db) == {
        "skipped": True, "reason": "deal_finder_scan_already_running",
    }
    core.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_always_releases_exact_lease_on_success_and_failure(monkeypatch):
    db = database()
    lease = ScanLease("opportunity_scan_lease:deal_finder", "worker-a", 2, 600)
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(deal_finder_cron, "release_scan_lease", release)
    monkeypatch.setattr(deal_finder_cron, "_run_auto_scan_batch_unlocked",
                        AsyncMock(return_value={"processed": 3}))
    assert await deal_finder_cron.run_auto_scan_batch(db, lease=lease) == {"processed": 3}
    release.assert_awaited_once_with(db, lease)
    release.reset_mock()
    deal_finder_cron._run_auto_scan_batch_unlocked.side_effect = RuntimeError("synthetic")
    with pytest.raises(RuntimeError, match="synthetic"):
        await deal_finder_cron.run_auto_scan_batch(db, lease=lease)
    release.assert_awaited_once_with(db, lease)


@pytest.mark.parametrize("name,ttl", [("", 600), ("bad:name", 600),
                                       ("deal_finder", True), ("deal_finder", 59),
                                       ("deal_finder", 21601)])
@pytest.mark.asyncio
async def test_invalid_lease_inputs_fail_before_database_access(name, ttl):
    db = database()
    with pytest.raises(ValueError):
        await acquire_scan_lease(db, name, ttl_seconds=ttl, now=NOW)
    db.app_settings.find_one_and_update.assert_not_awaited()


def test_manual_endpoint_uses_same_atomic_lease_without_legacy_flag():
    source = (Path(__file__).resolve().parents[1] / "rental/deal_finder_router.py").read_text()
    assert 'lease = await acquire_scan_lease(db, "deal_finder")' in source
    assert "run_auto_scan_batch(db, lease=lease)" in source
    block = source[source.index("async def run_cron_now"):source.index("async def analyze_lead")]
    assert "manual_running" not in block


def test_lease_is_renewed_before_cursor_write_and_alerts_remain_supported():
    source = (Path(__file__).resolve().parents[1] / "rental/deal_finder_cron.py").read_text()
    cursor_block = source[source.index("# avanzar cursor"):source.index("await asyncio.sleep(1.0)")]
    assert cursor_block.index("renew_scan_lease(db, lease)") < cursor_block.index(
        '"_id": "deal_finder_cron_state"')
    final_state_block = source[source.index('result["alerted"] = alerted'):]
    assert final_state_block.index("renew_scan_lease(db, lease)") < final_state_block.index(
        '"_id": "deal_finder_cron_state"')
    assert "send_alert_email(db, new_opps, became)" in source
