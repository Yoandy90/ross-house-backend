"""
Credit Builder / Rent Reporting Router
Handles tenant enrollment and status tracking for credit reporting program.
"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId

from rental.shared import get_db

router = APIRouter()
CREDIT_BUILDER_UNAVAILABLE = {
    "code": "credit_builder_unavailable",
    "message": (
        "Credit Builder está en revisión y no acepta inscripciones ni reportes. "
        "No se enviará información a burós hasta habilitar un proveedor verificado."
    ),
}


# ═══════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════
class EnrollRequest(BaseModel):
    agree_to_terms: bool = True


# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════
async def get_current_user(request: Request):
    """Extract user from JWT token"""
    import jwt
    from rental.shared import TENANT_JWT_SECRET

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")

    token = auth_header.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, TENANT_JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")

    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido")

    # Normalize user_id field — different tokens use different keys
    if "user_id" not in payload:
        payload["user_id"] = (
            payload.get("sub")
            or payload.get("tenant_id")
            or payload.get("id")
        )

    return payload


def _service_unavailable() -> None:
    """Fail closed until a real reporting provider and evidence path exist."""
    raise HTTPException(status_code=503, detail=CREDIT_BUILDER_UNAVAILABLE)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════
@router.get('/rent-reporting/my-status')
async def get_rent_reporting_status(request: Request):
    """Get current user's rent reporting / credit builder status"""
    await get_current_user(request)
    _service_unavailable()


@router.post('/rent-reporting/enroll')
async def enroll_in_rent_reporting(request: Request, data: EnrollRequest):
    """Enroll user in rent reporting / credit builder program"""
    await get_current_user(request)
    _service_unavailable()


@router.post('/rent-reporting/unenroll')
async def unenroll_from_rent_reporting(request: Request):
    """Unenroll user from rent reporting program"""
    user = await get_current_user(request)
    user_id = user.get('user_id') or user.get('sub')
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Usuario no identificado")
    
    db = get_db()
    
    result = await db.credit_builder_enrollments.update_one(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "cancelled", "cancelled_at": datetime.utcnow()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="No se encontró inscripción activa")
    
    return {
        "success": True,
        "message": "Tu inscripción ha sido cancelada"
    }


# ─────────────────────────────────────────────────────────────────
#  ADMIN: Credit Builder management
# ─────────────────────────────────────────────────────────────────

from rental.shared import auth_admin
from bson import ObjectId


@router.get('/admin/credit-builder/enrollments')
async def admin_list_enrollments(request: Request):
    """List all credit builder enrollments with user info + stats."""
    await auth_admin(request)
    _service_unavailable()
    db = get_db()
    enrollments = await db.credit_builder_enrollments.find({}).sort("enrolled_at", -1).to_list(500)

    out = []
    active_count = paused_count = cancelled_count = reports_this_month = 0
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    for e in enrollments:
        eid = str(e.get("_id"))
        user_id = e.get("user_id")
        user = None
        if user_id:
            try:
                user = await db.app_users.find_one({"_id": ObjectId(str(user_id))})
            except Exception:
                user = await db.app_users.find_one({"_id": user_id})
        status = e.get("status", "active")
        if status == "active": active_count += 1
        elif status == "paused": paused_count += 1
        elif status == "cancelled": cancelled_count += 1

        payments = e.get("payments", [])
        last_report = None
        for p in payments:
            pdate = p.get("reported_at")
            if isinstance(pdate, datetime) and pdate >= month_start:
                reports_this_month += 1
                break
        if payments:
            la = payments[-1].get("reported_at")
            last_report = la.isoformat() if isinstance(la, datetime) else str(la)

        out.append({
            "id": eid,
            "user_id": str(user_id) if user_id else "",
            "user_name": user.get("name", "") if user else "",
            "user_email": user.get("email", "") if user else "",
            "user_phone": user.get("phone", "") if user else "",
            "status": status,
            "enrolled_at": e.get("enrolled_at").isoformat() if isinstance(e.get("enrolled_at"), datetime) else str(e.get("enrolled_at", "")),
            "bureaus": e.get("bureaus", []),
            "payments_count": len(payments),
            "last_report": last_report,
            "credit_score": e.get("credit_score"),
            "notes": e.get("admin_notes", ""),
        })

    return {
        "success": True,
        "enrollments": out,
        "stats": {
            "total": len(out),
            "active": active_count,
            "paused": paused_count,
            "cancelled": cancelled_count,
            "reports_this_month": reports_this_month,
        }
    }


@router.get('/admin/credit-builder/enrollments/{enrollment_id}')
async def admin_get_enrollment(enrollment_id: str, request: Request):
    """Detailed enrollment with full payment history."""
    await auth_admin(request)
    _service_unavailable()
    if not ObjectId.is_valid(enrollment_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    e = await db.credit_builder_enrollments.find_one({"_id": ObjectId(enrollment_id)})
    if not e:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    user_id = e.get("user_id")
    user = None
    if user_id:
        try:
            user = await db.app_users.find_one({"_id": ObjectId(str(user_id))})
        except Exception:
            user = await db.app_users.find_one({"_id": user_id})
    e["_id"] = str(e["_id"])
    return {
        "success": True,
        "enrollment": e,
        "user": ({
            "id": str(user.get("_id")),
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
        } if user else None),
    }


@router.patch('/admin/credit-builder/enrollments/{enrollment_id}')
async def admin_update_enrollment(enrollment_id: str, request: Request):
    """Update enrollment: status (active/paused/cancelled), credit_score, admin_notes."""
    await auth_admin(request)
    _service_unavailable()
    if not ObjectId.is_valid(enrollment_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    data = await request.json()
    update = {}
    if "status" in data and data["status"] in ("active", "paused", "cancelled"):
        update["status"] = data["status"]
    if "credit_score" in data:
        try: update["credit_score"] = int(data["credit_score"])
        except Exception: pass
    if "admin_notes" in data:
        update["admin_notes"] = str(data["admin_notes"])
    update["updated_at"] = datetime.utcnow()
    await db.credit_builder_enrollments.update_one({"_id": ObjectId(enrollment_id)}, {"$set": update})
    return {"success": True, "message": "Inscripción actualizada"}


@router.post('/admin/credit-builder/enrollments/{enrollment_id}/report')
async def admin_report_payment(enrollment_id: str, request: Request):
    """Reject reports until a verified bureau provider is integrated."""
    await auth_admin(request)
    _service_unavailable()


@router.delete('/admin/credit-builder/enrollments/{enrollment_id}')
async def admin_delete_enrollment(enrollment_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(enrollment_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    res = await get_db().credit_builder_enrollments.delete_one({"_id": ObjectId(enrollment_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No encontrada")
    return {"success": True, "message": "Inscripción eliminada"}
