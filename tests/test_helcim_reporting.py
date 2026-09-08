import asyncio

import pytest
from fastapi import HTTPException

from rental import helcim_reporting_router as reporting


def test_dashboard_summarizes_read_only_provider_data_without_sensitive_fields():
    result = reporting.build_dashboard(
        card_transactions=[{
            "transactionId": 1, "cardBatchId": 10, "dateCreated": "2026-09-01",
            "status": "APPROVED", "type": "purchase", "amount": 1200,
            "currency": "USD", "cardNumber": "4000000000000000",
            "cardToken": "must-not-leak", "cardHolderName": "Private Tenant",
        }],
        card_batches=[{
            "id": 10, "batchNumber": 7, "closed": True,
            "dateClosed": "2026-09-02 10:00:00", "netSales": 1170,
            "totalSales": 1200, "totalRefunds": 30,
        }],
        ach_transactions=[{
            "id": 2, "batchId": 20, "dateCreated": "2026-09-01",
            "amount": 800, "bankAccountId": 999, "bankAccountL4l4": 1234,
        }],
        ach_batches=[{
            "batchId": 20, "batchReference": "ach-20", "statusBatch": 2,
            "dateClosed": "2026-09-03 10:00:00", "amountWithdrawals": 800,
            "amountDeposits": 0, "amountRefunded": 20, "amountReversed": 0,
        }],
        errors=[], date_from="2026-09-01", date_to="2026-09-30",
        page=1, limit=100,
    )

    assert result["summary"]["card_approved_amount"] == 1200
    assert result["summary"]["card_batch_net_sales"] == 1170
    assert result["summary"]["available_bank_balance"] is None
    assert result["settlement_events"][0]["provider_net_amount"] == 780
    serialized = str(result)
    assert "must-not-leak" not in serialized
    assert "4000000000000000" not in serialized
    assert "Private Tenant" not in serialized
    assert "bankAccountId" not in serialized
    assert result["capabilities"]["bank_deposit_arrival_date"] is False


def test_partial_provider_failure_is_visible_without_losing_other_sections():
    result = reporting.build_dashboard(
        card_transactions=[], card_batches=[], ach_transactions=[], ach_batches=[],
        errors=["ach_batches_unavailable"],
        date_from="2026-09-01", date_to="2026-09-30", page=1, limit=50,
    )
    assert result["success"] is False
    assert result["errors"] == ["ach_batches_unavailable"]


def test_date_range_is_bounded_and_ordered():
    reporting._validate_dates("2026-01-01", "2026-12-31")
    with pytest.raises(HTTPException, match="0 a 366"):
        reporting._validate_dates("2026-12-31", "2026-01-01")
    with pytest.raises(HTTPException, match="0 a 366"):
        reporting._validate_dates("2025-01-01", "2026-12-31")
    with pytest.raises(HTTPException, match="YYYY-MM-DD"):
        reporting._validate_dates("09/01/2026", "2026-09-30")


def test_provider_get_uses_get_and_never_sends_token_in_query():
    class Response:
        status_code = 200
        def json(self):
            return []

    class Client:
        def __init__(self):
            self.call = None
        async def get(self, url, **kwargs):
            self.call = (url, kwargs)
            return Response()

    client = Client()
    assert asyncio.run(
        reporting._provider_get(
            client, "/card-transactions", token="opaque", params={"limit": 10}
        )
    ) == []
    url, kwargs = client.call
    assert url.endswith("/card-transactions")
    assert kwargs["headers"]["api-token"] == "opaque"
    assert "api-token" not in kwargs["params"]
    assert "json" not in kwargs
