"""
Admin Nav Router — header notifications summary + global search (⌘K)
=====================================================================
GET /admin/nav-summary   → real pending counts for the admin UI
GET /admin/global-search → search tenants, properties, contracts, applications
"""
import re
from datetime import datetime

from fastapi import APIRouter, Request

from rental.shared import get_db, auth_admin

router = APIRouter()


@router.get('/admin/nav-summary')
async def nav_summary(request: Request):
    """Pending counts for header bell + sidebar badges."""
    await auth_admin(request)
    db = get_db()
    now = datetime.utcnow()

    new_applications = await db.rental_applications.count_documents({"status": "new"})
    open_maintenance = await db.maintenance_requests.count_documents(
        {"status": {"$nin": ["completed", "cancelled", "closed"]}}
    )
    pending_signatures = await db.rental_contracts.count_documents(
        {"$or": [{"signature_status": "pending"}, {"status": "pending_signatures"}]}
    )
    late_payments = await db.rental_payments.count_documents(
        {"status": {"$nin": ["completed", "cancelled", "waived"]}, "due_date": {"$lt": now}}
    )
    delinquent = await db.property_tax_status.find({"total_due": {"$gt": 0}}).to_list(50)
    delinquent_taxes = {
        "count": len(delinquent),
        "total_due": round(sum(d.get("total_due", 0) for d in delinquent), 2),
    }

    total = (new_applications + open_maintenance + pending_signatures +
             late_payments + delinquent_taxes["count"])
    return {
        "success": True,
        "total": total,
        "new_applications": new_applications,
        "open_maintenance": open_maintenance,
        "pending_signatures": pending_signatures,
        "late_payments": late_payments,
        "delinquent_taxes": delinquent_taxes,
    }


@router.get('/admin/global-search')
async def global_search(request: Request, q: str = ""):
    """Search across tenants, properties, contracts and applications."""
    await auth_admin(request)
    q = (q or "").strip()
    if len(q) < 2:
        return {"success": True, "results": []}
    db = get_db()
    rx = {"$regex": re.escape(q), "$options": "i"}
    results = []

    props = await db.properties.find(
        {"$or": [{"address": rx}, {"name": rx}, {"city": rx}]},
        {"address": 1, "name": 1, "status": 1, "rent_amount": 1},
    ).to_list(6)
    for p in props:
        results.append({
            "type": "property", "id": str(p["_id"]),
            "title": p.get("address") or p.get("name", ""),
            "subtitle": f"Propiedad · {p.get('status', '')} · ${p.get('rent_amount', 0):,.0f}/mes",
            "href": "/admin/propiedades",
        })

    tenants = await db.app_users.find(
        {"role": "tenant", "$or": [{"name": rx}, {"email": rx}, {"phone": rx}]},
        {"name": 1, "email": 1, "phone": 1},
    ).to_list(6)
    for t in tenants:
        results.append({
            "type": "tenant", "id": str(t["_id"]),
            "title": t.get("name", ""),
            "subtitle": f"Inquilino · {t.get('email', '')}",
            "href": "/admin/inquilinos",
        })

    contracts = await db.rental_contracts.find(
        {"$or": [{"contract_number": rx}, {"tenant_name": rx}, {"property_address": rx}]},
        {"contract_number": 1, "tenant_name": 1, "property_address": 1, "status": 1},
    ).to_list(6)
    for c in contracts:
        results.append({
            "type": "contract", "id": str(c["_id"]),
            "title": c.get("contract_number", ""),
            "subtitle": f"Contrato · {c.get('tenant_name', '')} · {c.get('property_address', '')}",
            "href": "/admin/contratos",
        })

    apps = await db.rental_applications.find(
        {"$or": [{"name": rx}, {"email": rx}, {"phone": rx}]},
        {"name": 1, "email": 1, "status": 1},
    ).to_list(6)
    for a in apps:
        results.append({
            "type": "application", "id": str(a["_id"]),
            "title": a.get("name", ""),
            "subtitle": f"Aplicación · {a.get('status', '')} · {a.get('email', '')}",
            "href": "/admin/aplicaciones",
        })

    return {"success": True, "results": results[:20]}
