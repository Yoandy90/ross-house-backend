"""Hosted-checkout financial source of truth.

This module is intentionally small: payment processors receive amounts resolved
from the canonical monthly rent invoice, never from tenant-provided amount or
late-fee fields.
"""
from __future__ import annotations

from datetime import datetime

from .rent_charge_policy import resolve_current_rent_charge


async def resolve_hosted_rent_charge(db, contract: dict, now: datetime) -> dict:
    """Return processor-safe financial values for the current rent period.

    ``resolve_current_rent_charge`` already fails closed for settled, cancelled,
    ambiguous and unavailable invoices. A hosted checkout additionally requires
    a strictly positive outstanding balance.
    """
    charge = await resolve_current_rent_charge(db, contract, now)
    outstanding = float(charge.get("outstanding") or 0)
    if outstanding <= 0:
        raise ValueError("current rent invoice has no chargeable balance")

    return {
        "invoice_id": charge["invoice_id"],
        "status": charge["status"],
        "amount": float(charge["amount"]),
        "late_fee": float(charge["late_fee"]),
        "total_due": float(charge["total_due"]),
        "total_paid": float(charge["total_paid"]),
        "outstanding": outstanding,
    }
