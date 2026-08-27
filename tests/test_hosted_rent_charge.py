"""Regression tests for hosted-checkout invoice-sourced financials."""
from datetime import datetime, timezone

import pytest

from rental import hosted_rent_charge as hrc


@pytest.mark.asyncio
async def test_hosted_charge_uses_canonical_invoice_balance(monkeypatch):
    async def fake_resolve(db, contract, now):
        return {
            "invoice_id": "invoice-1",
            "status": "partial",
            "amount": 1000.0,
            "late_fee": 50.0,
            "total_due": 1050.0,
            "total_paid": 400.0,
            "outstanding": 650.0,
        }

    monkeypatch.setattr(hrc, "resolve_current_rent_charge", fake_resolve)
    result = await hrc.resolve_hosted_rent_charge(
        object(), {"_id": "contract-1"}, datetime(2026, 8, 27, tzinfo=timezone.utc)
    )
    assert result == {
        "invoice_id": "invoice-1",
        "status": "partial",
        "amount": 1000.0,
        "late_fee": 50.0,
        "total_due": 1050.0,
        "total_paid": 400.0,
        "outstanding": 650.0,
    }


@pytest.mark.asyncio
async def test_hosted_charge_rejects_zero_balance(monkeypatch):
    async def fake_resolve(db, contract, now):
        return {
            "invoice_id": "paid-1",
            "status": "completed",
            "amount": 1000.0,
            "late_fee": 50.0,
            "total_due": 1050.0,
            "total_paid": 1050.0,
            "outstanding": 0.0,
        }

    monkeypatch.setattr(hrc, "resolve_current_rent_charge", fake_resolve)
    with pytest.raises(ValueError, match="no chargeable balance"):
        await hrc.resolve_hosted_rent_charge(
            object(), {"_id": "contract-1"}, datetime(2026, 8, 27, tzinfo=timezone.utc)
        )


def test_hosted_charge_api_has_no_client_amount_inputs():
    import inspect

    params = list(inspect.signature(hrc.resolve_hosted_rent_charge).parameters)
    assert params == ["db", "contract", "now"]
