from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from rental import deal_finder_cron
from rental.opportunity_scan_lease import (
    ScanLease, acquire_scan_lease, get_scan_lease_status,
    release_scan_lease, renew_scan_lease,
)


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def database():
    collection = SimpleNamespace(
        find_one=AsyncMock(), find_one_and_update=AsyncMock(), update_one=AsyncMock(),
    )
    return SimpleNamespace(rental_config=collection)


@pytest.mark.asyncio
async def test_acquire_uses_one_atomic_expiring_lease_claim():
    db = database()
    db.rental_config.find_one_and_update.return_value = {
        "_id": "opportunity_scan_lease:deal_finder", "lease_owner": "worker-a",
        "lease_generation": 7,
    }
    lease = await acquire_scan_lease(
        db, "deal_finder", now=NOW, owner="worker-a", ttl_seconds=600,
    )
    assert lease == ScanLease("opportunity_scan_lease:deal_finder", "worker-a", 7, 600)
    query, update = db.rental_config.find_one_and_update.await_args.args
    assert query["_id"] == lease.document_id
    assert {"lease_until": {"$lte": NOW}} in query["$or"]
    assert update["$inc"] == {"lease_generation": 1}
    assert db.rental_config.find_one_and_update.await_args.kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_locked_duplicate_claim_returns_busy_without_mutation_retry():
    db = database()
    db.rental_config.find_one_and_update.side_effect = DuplicateKeyError("locked")
    assert await acquire_scan_lease(db, "deal_finder", now=NOW, owner="worker-b") is None
    assert db.rental_config.find_one_and_update.await_count == 1


@pytest.mark.asyncio
async def test_renew_and_release_are_owner_and_generation_cas():
    db = database()
    db.rental_config.update_one.return_value = SimpleNamespace(modified_count=1)
    lease = ScanLease("opportunity_scan_lease:deal_finder", "worker-a", 4, 600)
    assert await renew_scan_lease(db, lease, now=NOW) is True
    renew_filter = db.rental_config.update_one.await_args_list[0].args[0]
    assert renew_filter["lease_owner"] == "worker-a"
    assert renew_filter["lease_generation"] == 4
    assert renew_filter["lease_until"] == {"$gt": NOW}
    assert await release_scan_lease(db, lease) is True
    release_filter = db.rental_config.update_one.await_args_list[1].args[0]
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
    db.rental_config.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_status_reports_active_lease_without_owner_secret():
    db = database()
    db.rental_config.find_one.return_value = {
        "lease_owner": "must-not-leak", "lease_generation": 8,
        "lease_until": (NOW + timedelta(minutes=5)).replace(tzinfo=None),
    }
    status = await get_scan_lease_status(db, "deal_finder", now=NOW)
    assert status == {
        "running": True,
        "lease_expires_at": "2026-09-07T12:05:00+00:00",
        "lease_generation": 8,
    }
    assert "owner" not in status


@pytest.mark.asyncio
async def test_public_status_treats_expired_or_released_lease_as_idle():
    db = database()
    db.rental_config.find_one.return_value = {
        "lease_owner": "old-worker", "lease_generation": 3,
        "lease_until": NOW - timedelta(seconds=1),
    }
    assert await get_scan_lease_status(db, "deal_finder", now=NOW) == {
        "running": False, "lease_expires_at": "", "lease_generation": 3,
    }


@pytest.mark.asyncio
async def test_status_rejects_invalid_name_before_database_access():
    db = database()
    with pytest.raises(ValueError):
        await get_scan_lease_status(db, "bad:name", now=NOW)
    db.rental_config.find_one.assert_not_awaited()


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


def test_all_manual_scan_entry_points_share_the_atomic_lease():
    source = (Path(__file__).resolve().parents[1] / "rental/deal_finder_router.py").read_text()
    start = source[source.index("async def start_scan"):source.index("async def get_scan")]
    run_now = source[source.index("async def run_cron_now"):source.index("async def analyze_lead")]
    assert 'lease = await acquire_scan_lease(db, "deal_finder")' in start
    assert 'lease = await acquire_scan_lease(db, "deal_finder")' in run_now
    assert "deal_finder_scans.find_one" not in start
    assert "deal_finder_scans.find_one" not in run_now
    assert "_run_scan_trueprodigy(" in start and "max_results, lease" in start
    assert "body.only_delinquent, lease" in start


def test_manual_workers_renew_and_release_their_exact_lease():
    source = (Path(__file__).resolve().parents[1] / "rental/deal_finder_router.py").read_text()
    engine = source[source.index("async def _require_manual_scan_lease"):source.index("async def start_scan")]
    assert "renew_scan_lease(db, lease)" in engine
    assert engine.count("await _require_manual_scan_lease(db, lease)") >= 6
    assert engine.count("await _release_manual_scan_lease(db, lease, scan_id)") == 2
    assert engine.count('RuntimeError("deal_finder_scan_lease_lost")') == 1


def test_cron_status_uses_authoritative_lease_without_exposing_legacy_flag():
    source = (Path(__file__).resolve().parents[1] / "rental/deal_finder_router.py").read_text()
    block = source[source.index("async def get_cron_config"):source.index("class CronConfigUpdate")]
    assert 'get_scan_lease_status(db, "deal_finder")' in block
    assert "**lease_state" in block
    assert "manual_running" not in block

def test_deal_finder_state_reuses_existing_rental_config_collection():
    root = Path(__file__).resolve().parents[1] / "rental"
    for name in (
        "opportunity_scan_lease.py",
        "deal_finder_cron.py",
        "deal_finder_router.py",
    ):
        source = (root / name).read_text()
        assert ".rental_config" in source
        assert ".app_settings" not in source
