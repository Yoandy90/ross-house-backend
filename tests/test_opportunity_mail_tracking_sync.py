import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from rental import deal_finder_router as router


def run(coro):
    return asyncio.run(coro)


def test_snapshot_bounds_and_sanitizes_provider_events():
    raw = [{"name": "In Transit", "time": "x" * 100, "location": "y" * 300}] * 60
    snapshot = router._lob_tracking_snapshot({
        "tracking_events": raw + [{"name": "Delivered"}],
        "expected_delivery_date": "2026-09-10-extra", "send_date": "2026-09-08-extra",
    }, {})
    assert snapshot["status"] == "delivered"
    assert len(snapshot["events"]) == 50
    assert len(snapshot["events"][0]["time"]) <= 64
    assert len(snapshot["events"][0]["location"]) <= 160
    assert len(snapshot["expected_delivery"]) <= 32


def test_invalid_lob_id_is_rejected_before_provider_call():
    client = SimpleNamespace(get=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        run(router._fetch_lob_tracking(client, "test_key", "../accounts", {}))
    assert exc.value.status_code == 422
    client.get.assert_not_awaited()


def test_mismatched_provider_letter_is_rejected():
    response = SimpleNamespace(status_code=200, json=lambda: {"id": "ltr_other"})
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    with pytest.raises(HTTPException) as exc:
        run(router._fetch_lob_tracking(client, "test_key", "ltr_expected", {}))
    assert exc.value.status_code == 502


class Cursor:
    def __init__(self, docs):
        self.docs = docs
        self.sort_args = None
        self.limit_value = None

    def sort(self, *args):
        self.sort_args = args
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    async def to_list(self, value):
        return self.docs[:value]


def document(suffix):
    return {"_id": router.ObjectId(f"64b64c2f6f43d82e7f4c10{suffix}"),
            "mail": {"lob_id": f"ltr_{suffix}", "status": "mailed"}}


def setup(monkeypatch, docs, updates):
    cursor = Cursor(docs)
    leads = SimpleNamespace(
        find=lambda *_args, **_kwargs: cursor,
        update_one=AsyncMock(side_effect=[SimpleNamespace(modified_count=n) for n in updates]),
    )
    monkeypatch.setattr(router, "auth_admin", AsyncMock())
    monkeypatch.setattr(router, "_lob_key", lambda: "test_key")
    monkeypatch.setattr(router, "get_db", lambda: SimpleNamespace(deal_finder_leads=leads))
    class ClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(router.httpx, "AsyncClient", lambda **_kwargs: ClientContext())
    return leads, cursor


def test_batch_claims_updates_and_skips_concurrent_letter(monkeypatch):
    leads, cursor = setup(monkeypatch, [document("01"), document("02")], [])
    async def update(query, _change):
        if "mail_tracking_claim.token" in query:
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=1 if query["mail.lob_id"] == "ltr_01" else 0)
    leads.update_one.side_effect = update
    provider = AsyncMock(return_value={
        "status": "delivered", "expected_delivery": "2026-09-10",
        "send_date": "2026-09-08", "events": [{"name": "Delivered"}],
    })
    monkeypatch.setattr(router, "_fetch_lob_tracking", provider)
    result = run(router.sync_mail_tracking(
        object(), router.MailTrackingSyncBody(limit=2, stale_hours=24)))
    assert result["requested"] == 2
    assert result["updated"] == 1 and result["skipped"] == 1 and result["failed"] == 0
    assert provider.await_count == 1
    assert cursor.limit_value == 2
    saves = [call for call in leads.update_one.await_args_list
             if "mail_tracking_claim.token" in call.args[0]]
    assert len(saves) == 1
    assert saves[0].args[0]["mail_tracking_claim.token"]
    assert saves[0].args[1]["$unset"] == {"mail_tracking_claim": ""}


def test_batch_provider_failure_releases_claim_without_exposing_error(monkeypatch):
    leads, _ = setup(monkeypatch, [document("01")], [1, 1])
    monkeypatch.setattr(router, "_fetch_lob_tracking", AsyncMock(side_effect=RuntimeError("secret")))
    result = run(router.sync_mail_tracking(object(), router.MailTrackingSyncBody(limit=1)))
    assert result["failed"] == 1
    assert "secret" not in repr(result)
    assert leads.update_one.await_args_list[-1].args[1] == {"$unset": {"mail_tracking_claim": ""}}


@pytest.mark.parametrize("limit,stale", [(0, 12), (51, 12), (1, 0), (1, 169)])
def test_invalid_batch_bounds_never_touch_provider_or_database(monkeypatch, limit, stale):
    monkeypatch.setattr(router, "auth_admin", AsyncMock())
    monkeypatch.setattr(router, "_lob_key", lambda: (_ for _ in ()).throw(AssertionError("no provider")))
    monkeypatch.setattr(router, "get_db", lambda: (_ for _ in ()).throw(AssertionError("no database")))
    with pytest.raises(HTTPException) as exc:
        run(router.sync_mail_tracking(
            object(), router.MailTrackingSyncBody(limit=limit, stale_hours=stale)))
    assert exc.value.status_code == 422
