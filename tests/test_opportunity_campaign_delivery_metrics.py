import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rental import deal_finder_router as router


def run(coro):
    return asyncio.run(coro)


class AsyncDocs:
    def __init__(self, docs):
        self.docs = docs

    def __aiter__(self):
        self.iterator = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration


def mailed(mail, *, visits=0, response=None, county="moore", signals=None):
    return {
        "county": county, "signals": signals or ["tax_delinquent"], "mail": mail,
        "offer": {"visits": visits, "response": response},
    }


def setup(monkeypatch, docs):
    leads = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncDocs(docs))
    monkeypatch.setattr(router, "auth_admin", AsyncMock())
    monkeypatch.setattr(router, "get_db", lambda: SimpleNamespace(deal_finder_leads=leads))


def test_delivery_state_prioritizes_returned_and_confirmed_events():
    today = "2026-09-08"
    assert router._mail_delivery_state({
        "expected_delivery": "2026-09-01",
        "tracking_events": [{"name": "Delivered"}, {"name": "Returned to Sender"}],
    }, today) == "returned"
    assert router._mail_delivery_state({
        "expected_delivery": "2099-01-01", "tracking_events": [{"name": "Delivered"}],
    }, today) == "confirmed"
    assert router._mail_delivery_state({"expected_delivery": "2026-09-01"}, today) == "estimated"
    assert router._mail_delivery_state({"expected_delivery": "invalid"}, today) == "pending"
    assert router._mail_delivery_state({"expected_delivery": 20260901}, today) == "pending"


def test_campaign_separates_confirmed_estimated_returned_and_pending(monkeypatch):
    docs = [
        mailed({"mailed_at": "x", "expected_delivery": "2099-01-01",
                "tracking_events": [{"name": "Delivered"}]}, visits=1,
               response={"action": "accept"}),
        mailed({"mailed_at": "x", "expected_delivery": "2020-01-01"}),
        mailed({"mailed_at": "x", "expected_delivery": "2020-01-01",
                "tracking_status": "returned"}),
        mailed({"mailed_at": "x", "expected_delivery": "2099-01-01"}),
    ]
    setup(monkeypatch, docs)
    result = run(router.campaign_stats(object()))
    funnel = result["funnel"]
    assert funnel["sent"] == 4
    assert funnel["delivered"] == 2
    assert funnel["delivered_confirmed"] == 1
    assert funnel["delivered_estimated"] == 1
    assert funnel["returned"] == 1
    assert funnel["scanned"] == 1 and funnel["responded"] == 1
    assert funnel["confirmed_delivery_rate"] == 25.0
    assert funnel["return_rate"] == 25.0
    assert result["by_county"]["moore"]["returned"] == 1
    assert result["by_signal"]["tax_delinquent"]["delivered"] == 2
    assert result["by_action"] == {"accept": 1}
