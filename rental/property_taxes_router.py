"""
Property Taxes Router — Moore County TX live tax-due sync
==========================================================
The county portal (BIS Consultants eSearch, https://esearch.co.moore.tx.us)
has NO public API, but its tax-due modal endpoint is reachable server-side:

    GET /Property/GetPropertyTaxDueModalResult?id={account}&year={year}

It returns an HTML table with one row per delinquent year:
Year, Taxable Value, Base Tax, Base Taxes Paid, Base Tax Due,
Discount/Penalty & Interest, Attorney Fees, Amount Due.

We sync each property that has `tax_account_id`, store the parsed result in
`property_tax_status`, and refresh once a day via a cron loop.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter()

ESEARCH = "https://esearch.co.moore.tx.us"
UA = {"User-Agent": "Mozilla/5.0 (RossHouseRentals admin; tax status check)"}
SYNC_INTERVAL_SECONDS = 24 * 60 * 60  # daily


def _money(txt: str) -> float:
    try:
        return float(txt.replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _cell(row_html: str, css: str) -> float:
    m = re.search(css + r'">\s*\$?([\d,.\-]+)', row_html)
    return _money(m.group(1)) if m else 0.0


def parse_tax_due_html(html: str) -> list[dict]:
    """Parse the GetPropertyTaxDueModalResult table into year rows."""
    years = []
    for row in html.split("<tr>"):
        ym = re.search(r"<td>(20\d\d)</td>", row)
        if not ym or "PreviousYearsModalView_AmountDue" not in row:
            continue
        years.append({
            "year": int(ym.group(1)),
            "taxable_value": _cell(row, "PreviousYearsModalView_TaxableValue"),
            "base_tax": _cell(row, "PreviousYearsModalView_BaseTax"),
            "base_paid": _cell(row, "PreviousYearsModalView_BaseTaxPaid"),
            "base_due": _cell(row, "PreviousYearsModalView_BaseTaxDue"),
            "penalty_interest": _cell(row, "PreviousYearsModalView_DiscountPenaltyAndInterest"),
            "attorney_fees": _cell(row, "PreviousYearsModalView_AttorneyFees"),
            "amount_due": _cell(row, "PreviousYearsModalView_AmountDue"),
        })
    return years


async def fetch_account_tax_due(account_id: str, base: str = "") -> dict:
    """Fetch live delinquent-tax data for one county account (default: Moore)."""
    year = datetime.now().year
    url = f"{base or ESEARCH}/Property/GetPropertyTaxDueModalResult?id={account_id}&year={year}"
    async with httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
    years = parse_tax_due_html(r.text)
    total_due = round(sum(y["amount_due"] for y in years), 2)
    return {
        "account_id": str(account_id),
        "years_due": years,
        "total_due": total_due,
        "status": "delinquent" if total_due > 0 else "current",
        "portal_url": f"{ESEARCH}/Property/View/{account_id}",
        "last_synced_at": datetime.now(timezone.utc),
    }


async def sync_all_properties(db) -> dict:
    """Sync tax status for every property that has a tax_account_id."""
    props = await db.properties.find(
        {"tax_account_id": {"$nin": [None, ""]}},
        {"address": 1, "tax_account_id": 1},
    ).to_list(100)

    synced, errors = [], []
    for p in props:
        acct = str(p["tax_account_id"]).strip()
        try:
            data = await fetch_account_tax_due(acct)
            data["property_id"] = str(p["_id"])
            data["address"] = p.get("address", "")
            await db.property_tax_status.update_one(
                {"account_id": acct}, {"$set": data}, upsert=True
            )
            synced.append({"account_id": acct, "address": data["address"],
                           "total_due": data["total_due"], "status": data["status"]})
        except Exception as e:
            logger.warning(f"[property_taxes] sync failed for acct {acct}: {e}")
            errors.append({"account_id": acct, "error": str(e)})
        await asyncio.sleep(1.5)  # be polite with the county portal

    return {"success": True, "synced": synced, "errors": errors,
            "synced_count": len(synced), "error_count": len(errors)}


def _serialize_status(doc: dict) -> dict:
    return {
        "account_id": doc.get("account_id", ""),
        "property_id": doc.get("property_id", ""),
        "address": doc.get("address", ""),
        "status": doc.get("status", "unknown"),
        "total_due": doc.get("total_due", 0),
        "years_due": doc.get("years_due", []),
        "portal_url": doc.get("portal_url", ""),
        "last_synced_at": doc["last_synced_at"].isoformat() if doc.get("last_synced_at") else "",
    }


@router.get('/admin/property-taxes')
async def list_property_taxes(request: Request):
    """Admin: tax status for all properties (from last sync)."""
    await auth_admin(request)
    db = get_db()
    props = await db.properties.find(
        {}, {"address": 1, "tax_account_id": 1, "tax_annual_estimate": 1}
    ).to_list(100)
    statuses = {s["account_id"]: s async for s in db.property_tax_status.find({})}

    items = []
    for p in props:
        acct = str(p.get("tax_account_id") or "").strip()
        st = statuses.get(acct)
        items.append({
            "property_id": str(p["_id"]),
            "address": p.get("address", ""),
            "account_id": acct,
            "tax_annual_estimate": p.get("tax_annual_estimate") or 0,
            "tax_status": _serialize_status(st) if st else None,
        })

    total_due = round(sum(i["tax_status"]["total_due"] for i in items if i["tax_status"]), 2)
    return {"success": True, "properties": items, "total_due": total_due}


@router.post('/admin/property-taxes/sync')
async def sync_property_taxes(request: Request):
    """Admin: sync live tax status from the Moore County portal NOW."""
    await auth_admin(request)
    return await sync_all_properties(get_db())


async def property_tax_sync_loop():
    """Daily background sync of county tax status."""
    await asyncio.sleep(120)  # let the app settle after boot
    while True:
        try:
            db = get_db()
            if db is not None:
                result = await sync_all_properties(db)
                logger.info(f"[property_taxes] daily sync: {result['synced_count']} ok, "
                            f"{result['error_count']} errors")
        except Exception as e:
            logger.warning(f"[property_taxes] daily sync failed: {e}")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
