"""Read-only Helcim financial reporting for the Ross House admin panel."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from .helcim_vault_router import _helcim_cfg
from .shared import auth_admin

router = APIRouter(tags=["helcim-admin-reporting"])
HELCIM_BASE = "https://api.helcim.com/v2"

CARD_TRANSACTION_FIELDS = {
    "transactionId", "cardBatchId", "dateCreated", "status", "user", "type",
    "amount", "currency", "cardType", "approvalCode", "customerCode",
    "invoiceNumber", "warning",
}
CARD_BATCH_FIELDS = {
    "id", "dateCreated", "dateUpdated", "dateClosed", "closed", "terminalId",
    "batchNumber", "netSales", "totalSales", "totalRefunds", "totalReversed",
    "totalRefundsReversed", "countTotal", "countApproved", "countDeclined",
}
ACH_TRANSACTION_FIELDS = {
    "id", "dateCreated", "statusAuth", "statusClearing", "batchId",
    "transactionType", "amount", "currency", "approvalCode", "test",
    "acquirerTransactionId", "responseMessage", "originalTransactionId",
    "statusBatch", "dateClosed",
}
ACH_BATCH_FIELDS = {
    "batchId", "batchReference", "statusBatch", "amountWithdrawals",
    "countWithdrawals", "amountDeposits", "countDeposits", "amountReversed",
    "countReversed", "amountRefunded", "countRefunded", "dateOpened",
    "dateClosed",
}


def _items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "transactions", "batches", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _allowlist(items: list[dict], fields: set[str]) -> list[dict]:
    return [{key: item.get(key) for key in fields if key in item} for item in items]


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_dashboard(
    *, card_transactions: Any, card_batches: Any,
    ach_transactions: Any, ach_batches: Any, errors: list[str],
    date_from: str, date_to: str, page: int, limit: int,
) -> dict:
    cards = _allowlist(_items(card_transactions), CARD_TRANSACTION_FIELDS)
    card_batch_items = _allowlist(_items(card_batches), CARD_BATCH_FIELDS)
    ach = _allowlist(_items(ach_transactions), ACH_TRANSACTION_FIELDS)
    ach_batch_items = _allowlist(_items(ach_batches), ACH_BATCH_FIELDS)

    approved_cards = [
        item for item in cards if str(item.get("status", "")).upper() == "APPROVED"
    ]
    closed_card_batches = [item for item in card_batch_items if item.get("closed") is True]
    closed_ach_batches = [
        item for item in ach_batch_items
        if str(item.get("statusBatch", "")) == "2" or item.get("dateClosed")
    ]
    settlement_events = [
        {
            "rail": "card",
            "batch_id": item.get("id"),
            "batch_number": item.get("batchNumber"),
            "provider_closed_at": item.get("dateClosed"),
            "provider_net_amount": _number(item.get("netSales")),
            "currency": "USD",
            "bank_arrival_confirmed": False,
        }
        for item in closed_card_batches
    ] + [
        {
            "rail": "ach",
            "batch_id": item.get("batchId"),
            "batch_number": item.get("batchReference"),
            "provider_closed_at": item.get("dateClosed"),
            "provider_net_amount": (
                _number(item.get("amountWithdrawals"))
                - _number(item.get("amountDeposits"))
                - _number(item.get("amountRefunded"))
                - _number(item.get("amountReversed"))
            ),
            "currency": "USD",
            "bank_arrival_confirmed": False,
        }
        for item in closed_ach_batches
    ]
    settlement_events.sort(
        key=lambda item: str(item.get("provider_closed_at") or ""), reverse=True
    )

    return {
        "success": not errors,
        "provider": "helcim",
        "filters": {
            "date_from": date_from, "date_to": date_to, "page": page, "limit": limit,
        },
        "summary": {
            "card_approved_count": len(approved_cards),
            "card_approved_amount": round(sum(_number(x.get("amount")) for x in approved_cards), 2),
            "card_batch_net_sales": round(
                sum(_number(x.get("netSales")) for x in closed_card_batches), 2
            ),
            "ach_transaction_count": len(ach),
            "ach_transaction_amount": round(sum(_number(x.get("amount")) for x in ach), 2),
            "closed_card_batches": len(closed_card_batches),
            "closed_ach_batches": len(closed_ach_batches),
            "available_bank_balance": None,
        },
        "card_transactions": cards,
        "card_batches": card_batch_items,
        "ach_transactions": ach,
        "ach_batches": ach_batch_items,
        "settlement_events": settlement_events,
        "capabilities": {
            "card_transactions": True,
            "card_batches": True,
            "ach_transactions": True,
            "ach_batches": True,
            "available_bank_balance": False,
            "bank_deposit_arrival_date": False,
            "provider_batch_closed_at": True,
        },
        "limitations": [
            "available_bank_balance_not_exposed_by_helcim_api",
            "bank_deposit_arrival_date_not_exposed_by_helcim_api",
            "provider_closed_at_is_not_bank_arrival_confirmation",
        ],
        "errors": errors,
    }


async def _provider_get(
    client: httpx.AsyncClient, path: str, *, token: str, params: dict | None = None
) -> Any:
    response = await client.get(
        f"{HELCIM_BASE}{path}",
        headers={"api-token": token, "accept": "application/json"},
        params=params,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"helcim_http_{response.status_code}")
    return response.json()


def _validate_dates(date_from: str, date_to: str) -> None:
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as exc:
        raise HTTPException(422, "Las fechas deben usar YYYY-MM-DD") from exc
    if start > end or (end - start).days > 366:
        raise HTTPException(422, "El rango debe ser de 0 a 366 días")


@router.get("/admin/payment-processors/helcim/financial-dashboard")
async def helcim_financial_dashboard(
    request: Request,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
):
    await auth_admin(request)
    today = date.today()
    date_to = date_to or today.isoformat()
    date_from = date_from or (today - timedelta(days=30)).isoformat()
    _validate_dates(date_from, date_to)

    cfg = await _helcim_cfg()
    token = cfg.get("api_token", "")
    if not token:
        raise HTTPException(400, "Helcim no está configurado")

    requests = (
        ("/card-transactions", {
            "dateFrom": date_from, "dateTo": date_to, "page": page, "limit": limit,
        }),
        ("/card-batches", {"collect-stats": "true"}),
        ("/ach/transactions", {
            "startDate": date_from, "endDate": date_to, "page": page, "limit": limit,
        }),
        ("/ach/batches", None),
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        results = await asyncio.gather(
            *(_provider_get(client, path, token=token, params=params)
              for path, params in requests),
            return_exceptions=True,
        )

    payloads: list[Any] = []
    errors: list[str] = []
    names = ("card_transactions", "card_batches", "ach_transactions", "ach_batches")
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            payloads.append([])
            errors.append(f"{name}_unavailable")
        else:
            payloads.append(result)

    if len(errors) == len(names):
        raise HTTPException(502, "Helcim reporting no está disponible")

    return build_dashboard(
        card_transactions=payloads[0], card_batches=payloads[1],
        ach_transactions=payloads[2], ach_batches=payloads[3],
        errors=errors, date_from=date_from, date_to=date_to, page=page, limit=limit,
    )
