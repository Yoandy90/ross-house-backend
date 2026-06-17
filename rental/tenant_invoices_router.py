"""
Tenant unified invoices history (rent + utility bills) for the marketplace mobile app.
Authenticated with `auth_marketplace` (the same auth used by /tenant/xcel/usage and
/tenant/utility-bills), so the tenant can see their full payment history regardless
of whether they were created as a `tenant` or signed up as a marketplace `app_user`.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional, List
from bson import ObjectId

from fastapi import APIRouter, HTTPException, Request

from .shared import get_db, auth_marketplace, serialize

logger = logging.getLogger("tenant_invoices")
router = APIRouter()


async def _resolve_tenant_ids_for_user(user: dict) -> List[str]:
    """Return the set of tenant_ids (strings) linked to this marketplace user via
    direct id match, app_user_id link, email, or normalized phone.
    Mirrors the lookup used by xcel_energy_router for consistency."""
    db = get_db()
    user_id = str(user["_id"])
    tenant_ids = {user_id}  # user could be the tenant themselves

    # By app_user_id link
    async for t in db.tenants.find({"app_user_id": user_id}):
        tenant_ids.add(str(t["_id"]))

    # By email
    email = (user.get("email") or "").strip().lower()
    if email:
        async for t in db.tenants.find({
            "email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}
        }):
            tenant_ids.add(str(t["_id"]))

    # By phone (normalized)
    phone = (user.get("phone") or "").strip()
    if phone:
        def _norm(p: str) -> str:
            return (p or "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("+", "")
        target = _norm(phone)
        if target:
            async for t in db.tenants.find({"phone": {"$exists": True, "$ne": ""}}):
                if _norm(t.get("phone", "")) == target:
                    tenant_ids.add(str(t["_id"]))

    return list(tenant_ids)


def _safe_iso(value) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _safe_period(date_value, fallback: str = "") -> str:
    """Return YYYY-MM string from datetime/date/string."""
    if not date_value:
        return fallback
    if isinstance(date_value, datetime):
        return date_value.strftime("%Y-%m")
    s = str(date_value)
    if len(s) >= 7:
        return s[:7]
    return fallback


@router.get("/tenant/invoices/history")
async def tenant_invoices_history(
    request: Request,
    year: Optional[int] = None,
    type: Optional[str] = None,    # 'rent' | 'utility'
    status: Optional[str] = None,  # 'paid' | 'pending'
    limit: int = 100,
):
    """Unified history of rent payments + utility bills for the authenticated tenant.

    Returns rows shaped for the mobile invoices screen with:
      id, type, label, period, amount, paid, paid_at, status, due_date, has_pdf, source
    """
    user = await auth_marketplace(request)
    db = get_db()

    tenant_ids = await _resolve_tenant_ids_for_user(user)
    if not tenant_ids:
        return {"items": [], "total_count": 0, "summary": {}}

    items: List[dict] = []

    # ─── Rent payments ─────────────────────────────────────────
    if type in (None, "rent"):
        async for p in db.rental_payments.find(
            {"tenant_id": {"$in": tenant_ids}}
        ).sort("payment_date", -1).limit(limit):
            # Real paid flag: only true if explicit flag set OR status indicates payment
            doc_status = (p.get("status") or "").lower()
            paid = bool(p.get("paid", False)) or doc_status in ("paid", "completed")
            payment_dt = p.get("payment_date")
            paid_at = payment_dt if paid else None
            # Period: prefer explicit field, else derived from due_date / payment_date
            period = p.get("period") or _safe_period(p.get("due_date") or payment_dt or p.get("created_at"))
            late_fee = float(p.get("late_fee", 0) or 0)
            amount = float(p.get("amount", 0) or 0)
            total_due = float(p.get("total_due", amount + late_fee) or amount)
            items.append({
                "id": str(p["_id"]),
                "type": "rent",
                "label": "Renta mensual",
                "subtitle": p.get("notes") or p.get("description") or "",
                "period": period,
                "amount": round(total_due, 2),
                "base_amount": round(amount, 2),
                "late_fee": round(late_fee, 2),
                "paid": paid,
                "paid_at": _safe_iso(paid_at) if paid_at else None,
                "due_date": _safe_iso(p.get("due_date")),
                "status": doc_status if doc_status in ("paid", "completed", "pending", "late", "partial", "cancelled") else ("paid" if paid else "pending"),
                "has_pdf": paid,  # PDF receipt only meaningful after payment
                "pdf_endpoint": f"/api/tenant/payment/{p['_id']}/receipt",
                "source": "rental_payments",
                "icon": "home",
                "color": "#C8102E",
            })

    # ─── Utility bills ─────────────────────────────────────────
    if type in (None, "utility"):
        async for b in db.tenant_utility_bills.find(
            {"tenant_id": {"$in": tenant_ids}}
        ).sort("created_at", -1).limit(limit):
            bstatus = b.get("status", "pending")
            paid = bstatus == "paid"
            paid_at = b.get("paid_at") or (b.get("created_at") if paid else None)
            type_label_map = {
                "electricity": ("Electricidad", "flash", "#F59E0B"),
                "gas": ("Gas", "flame", "#EF4444"),
                "water": ("Agua", "water", "#3B82F6"),
                "internet": ("Internet", "wifi", "#8B5CF6"),
                "phone": ("Teléfono", "call", "#06B6D4"),
                "tv": ("TV", "tv", "#6366F1"),
                "other": ("Otro servicio", "document-text", "#6B7280"),
            }
            btype = b.get("type", "other")
            label, icon, color = type_label_map.get(btype, type_label_map["other"])
            items.append({
                "id": str(b["_id"]),
                "type": "utility",
                "utility_type": btype,
                "label": f"{label}",
                "subtitle": (
                    f"{b.get('kwh')} kWh × ${b.get('rate_per_kwh')}/kWh"
                    if b.get("kwh") and b.get("rate_per_kwh") else (b.get("notes") or "")
                ),
                "period": b.get("period") or _safe_period(b.get("created_at")),
                "amount": round(float(b.get("amount", 0)), 2),
                "paid": paid,
                "paid_at": _safe_iso(paid_at),
                "due_date": _safe_iso(b.get("due_date")),
                "status": bstatus,
                "has_pdf": False,  # No PDF generator for utility bills yet
                "pdf_endpoint": None,
                "source": "tenant_utility_bills",
                "icon": icon,
                "color": color,
            })

    # ─── Filters ───────────────────────────────────────────────
    if year:
        items = [
            x for x in items
            if (x.get("paid_at") or x.get("due_date") or "").startswith(str(year))
            or (x.get("period") or "").startswith(str(year))
        ]
    if status in ("paid", "pending"):
        # Use the already-normalized `paid` boolean instead of literal string match
        # because rental_payments use status="completed" for paid invoices while
        # utility bills use status="paid". The `paid` flag handles both correctly.
        if status == "paid":
            items = [x for x in items if x.get("paid")]
        else:  # pending
            items = [x for x in items if not x.get("paid")]

    # ─── Sort newest first ────────────────────────────────────
    def _sort_key(x):
        return x.get("paid_at") or x.get("due_date") or x.get("period") or ""
    items.sort(key=_sort_key, reverse=True)

    # ─── Summary ──────────────────────────────────────────────
    total_paid = round(sum(x["amount"] for x in items if x["paid"]), 2)
    total_pending = round(sum(x["amount"] for x in items if not x["paid"]), 2)
    years_set = sorted(
        {(x.get("paid_at") or x.get("due_date") or x.get("period") or "")[:4]
         for x in items
         if (x.get("paid_at") or x.get("due_date") or x.get("period"))},
        reverse=True,
    )

    return {
        "items": items,
        "total_count": len(items),
        "summary": {
            "total_paid": total_paid,
            "total_pending": total_pending,
            "paid_count": sum(1 for x in items if x["paid"]),
            "pending_count": sum(1 for x in items if not x["paid"]),
        },
        "filters": {
            "available_years": [y for y in years_set if y],
            "available_types": ["rent", "utility"],
            "available_statuses": ["paid", "pending"],
        },
    }


@router.get("/tenant/invoices/{invoice_id}/pdf")
async def tenant_invoice_pdf(invoice_id: str, request: Request):
    """Download the PDF for a specific invoice. Currently supports rent payments
    (uses existing rental_pdf_service). Utility bills will be supported in Fase 2."""
    user = await auth_marketplace(request)
    db = get_db()
    tenant_ids = await _resolve_tenant_ids_for_user(user)

    # Try rental_payments first
    payment = None
    try:
        payment = await db.rental_payments.find_one({"_id": ObjectId(invoice_id)})
    except Exception:
        pass

    if payment:
        if payment.get("tenant_id") not in tenant_ids:
            raise HTTPException(status_code=403, detail="No autorizado")

        contract = None
        if payment.get("contract_id"):
            try:
                contract = await db.rental_contracts.find_one({"_id": ObjectId(payment["contract_id"])})
            except Exception:
                pass

        # Find the tenant doc for the PDF
        tenant_doc = None
        for tid in tenant_ids:
            try:
                t = await db.tenants.find_one({"_id": ObjectId(tid)})
                if t:
                    tenant_doc = t
                    break
            except Exception:
                continue
        if not tenant_doc:
            tenant_doc = user  # fall back to user shape

        from rental_pdf_service import generate_rental_receipt_pdf
        pdf_b64 = generate_rental_receipt_pdf(
            payment=serialize(payment),
            contract=serialize(contract) if contract else None,
            tenant=serialize(tenant_doc),
        )
        receipt_num = payment.get("receipt_number", invoice_id)
        return {
            "success": True,
            "pdf_base64": pdf_b64,
            "filename": f"Recibo_Renta_{receipt_num}.pdf",
            "type": "rent",
        }

    # Utility bill case (PDF generation not yet implemented for utilities)
    try:
        bill = await db.tenant_utility_bills.find_one({"_id": ObjectId(invoice_id)})
    except Exception:
        bill = None
    if bill:
        if bill.get("tenant_id") not in tenant_ids:
            raise HTTPException(status_code=403, detail="No autorizado")
        raise HTTPException(
            status_code=501,
            detail="La descarga de PDF para facturas de servicios estará disponible próximamente.",
        )

    raise HTTPException(status_code=404, detail="Factura no encontrada")
