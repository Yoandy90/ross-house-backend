"""
Credit Builder / Rent Reporting Router
Handles tenant enrollment and status tracking for credit reporting program.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId

from rental.shared import get_db, serialize

logger = logging.getLogger(__name__)
router = APIRouter()


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


def calculate_credit_impact(months: int, on_time: int, late: int) -> int:
    """Calculate estimated credit score increase based on payment history"""
    if months == 0:
        return 0
    
    on_time_ratio = on_time / months if months > 0 else 0
    base_increase = months * 4  # ~4 points per month
    bonus = 10 if on_time_ratio >= 0.95 else 0  # Perfect payment bonus
    penalty = late * 3  # Penalty for late payments
    
    return max(0, min(base_increase + bonus - penalty, 60))  # Cap at 60 points


def generate_payment_history(user_id: str, months_enrolled: int) -> list:
    """Generate simulated payment history for demo purposes"""
    history = []
    today = datetime.utcnow()
    
    for i in range(months_enrolled):
        month_date = today - timedelta(days=30 * (months_enrolled - i))
        is_on_time = i % 10 != 7  # 90% on time rate
        
        history.append({
            "period": month_date.strftime("%B %Y"),
            "amount": 1200,  # Demo amount
            "due_date": month_date.strftime("%Y-%m-01"),
            "paid_date": month_date.strftime("%Y-%m-%d"),
            "status": "on_time" if is_on_time else "late",
            "reported": True
        })
    
    return history


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════
@router.get('/rent-reporting/my-status')
async def get_rent_reporting_status(request: Request):
    """Get current user's rent reporting / credit builder status"""
    user = await get_current_user(request)
    user_id = user.get('user_id') or user.get('sub')
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Usuario no identificado")
    
    db = get_db()
    
    # Check if user is enrolled
    enrollment = await db.credit_builder_enrollments.find_one({
        "user_id": user_id,
        "status": "active"
    })
    
    if not enrollment:
        # Return benefits for non-enrolled users
        return {
            "success": True,
            "enrolled": False,
            "benefits": [
                "Reportamos tus pagos a Equifax, TransUnion y Experian",
                "Construye historial crediticio con cada pago de renta",
                "Sin costo adicional - incluido con tu contrato",
                "Potencial aumento de 35-50 puntos en 12 meses",
                "Mejor acceso a crédito, tarjetas y préstamos"
            ]
        }
    
    # Calculate stats for enrolled users
    enrolled_date = enrollment.get('enrolled_at', datetime.utcnow())
    months_enrolled = max(1, (datetime.utcnow() - enrolled_date).days // 30)
    
    # Get or simulate payment data
    payments = enrollment.get('payments', [])
    if not payments:
        payments = generate_payment_history(user_id, min(months_enrolled, 12))
    
    on_time_payments = sum(1 for p in payments if p.get('status') == 'on_time')
    late_payments = sum(1 for p in payments if p.get('status') == 'late')
    
    # Calculate streak
    streak = 0
    for p in reversed(payments):
        if p.get('status') == 'on_time':
            streak += 1
        else:
            break
    
    credit_impact = {
        "estimated_score_increase": calculate_credit_impact(len(payments), on_time_payments, late_payments),
        "months_reported": len(payments),
        "on_time_payments": on_time_payments,
        "late_payments": late_payments,
        "reporting_status": "active",
        "bureaus_reported": ["Equifax", "TransUnion", "Experian"],
        "next_report_date": (datetime.utcnow().replace(day=1) + timedelta(days=32)).replace(day=5).strftime("%d de %B, %Y"),
        "credit_building_streak": streak
    }
    
    # Generate badges
    badges = [
        {
            "id": "first_report",
            "name": "Primer Reporte",
            "icon": "ribbon",
            "description": "Tu primer pago fue reportado",
            "earned": len(payments) >= 1
        },
        {
            "id": "streak_3",
            "name": "Racha de 3",
            "icon": "flame",
            "description": "3 meses consecutivos a tiempo",
            "earned": streak >= 3
        },
        {
            "id": "streak_6",
            "name": "Medio Año",
            "icon": "trophy",
            "description": "6 meses consecutivos a tiempo",
            "earned": streak >= 6
        },
        {
            "id": "perfect_year",
            "name": "Año Perfecto",
            "icon": "medal",
            "description": "12 meses sin pagos tardíos",
            "earned": len(payments) >= 12 and late_payments == 0
        },
        {
            "id": "all_bureaus",
            "name": "Triple Impacto",
            "icon": "shield-checkmark",
            "description": "Reportado a los 3 burós",
            "earned": True  # Always true when enrolled
        }
    ]
    
    return {
        "success": True,
        "enrolled": True,
        "credit_impact": credit_impact,
        "payment_history": payments[-6:],  # Last 6 payments
        "badges": badges,
        "tips": [
            "Paga antes del día 5 para asegurar reporte 'a tiempo'",
            "6+ meses consistentes tienen mayor impacto",
            "Mantén tu racha para maximizar beneficios"
        ]
    }


@router.post('/rent-reporting/enroll')
async def enroll_in_rent_reporting(request: Request, data: EnrollRequest):
    """Enroll user in rent reporting / credit builder program"""
    user = await get_current_user(request)
    user_id = user.get('user_id') or user.get('sub')
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Usuario no identificado")
    
    if not data.agree_to_terms:
        raise HTTPException(status_code=400, detail="Debes aceptar los términos")
    
    db = get_db()
    
    # Check if already enrolled
    existing = await db.credit_builder_enrollments.find_one({
        "user_id": user_id,
        "status": "active"
    })
    
    if existing:
        enrolled_at = existing.get('enrolled_at')
        return {
            "success": True,
            "message": "Ya estás inscrito en Credit Builder",
            "enrolled_at": enrolled_at.isoformat() if hasattr(enrolled_at, 'isoformat') else str(enrolled_at) if enrolled_at else None,
        }
    
    # Create enrollment
    enrollment = {
        "user_id": user_id,
        "status": "active",
        "enrolled_at": datetime.utcnow(),
        "terms_accepted_at": datetime.utcnow(),
        "bureaus": ["Equifax", "TransUnion", "Experian"],
        "payments": []
    }
    
    result = await db.credit_builder_enrollments.insert_one(enrollment)
    
    logger.info(f"User {user_id} enrolled in Credit Builder")
    
    return {
        "success": True,
        "message": "¡Te has inscrito exitosamente! Tu primer reporte será el próximo mes.",
        "enrollment_id": str(result.inserted_id)
    }


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
    """🔴 MOCKED: Simulates submitting a rent payment report to credit bureaus.
    Real bureau API integration pending — replace mock_response when ready.
    Body: { amount, period, notes? }
    """
    await auth_admin(request)
    if not ObjectId.is_valid(enrollment_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    e = await db.credit_builder_enrollments.find_one({"_id": ObjectId(enrollment_id)})
    if not e:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    if e.get("status") != "active":
        raise HTTPException(status_code=400, detail="Solo activas pueden reportar")

    data = await request.json()
    amount = float(data.get("amount", 0))
    period_label = data.get("period", datetime.utcnow().strftime("%Y-%m"))
    bureaus = e.get("bureaus", ["Equifax", "TransUnion", "Experian"])
    notes = data.get("notes", "")

    mock_response = {
        "mocked": True,
        "submitted_at": datetime.utcnow().isoformat(),
        "bureaus": bureaus,
        "confirmation_ids": {b: f"MOCK-{b[:3].upper()}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}" for b in bureaus},
        "status": "accepted",
        "estimated_score_impact": "+5 to +15 points (mocked)",
    }

    payment_record = {
        "amount": amount,
        "period": period_label,
        "reported_at": datetime.utcnow(),
        "reported_by_admin": True,
        "bureaus": bureaus,
        "bureau_response": mock_response,
        "notes": notes,
    }
    await db.credit_builder_enrollments.update_one(
        {"_id": ObjectId(enrollment_id)},
        {"$push": {"payments": payment_record},
         "$set": {"last_reported_at": datetime.utcnow(), "updated_at": datetime.utcnow()}}
    )

    return {
        "success": True,
        "message": "Reporte enviado a burós (MOCKED — sin API real)",
        "report": {**payment_record, "reported_at": payment_record["reported_at"].isoformat()},
    }


@router.delete('/admin/credit-builder/enrollments/{enrollment_id}')
async def admin_delete_enrollment(enrollment_id: str, request: Request):
    await auth_admin(request)
    if not ObjectId.is_valid(enrollment_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    res = await get_db().credit_builder_enrollments.delete_one({"_id": ObjectId(enrollment_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No encontrada")
    return {"success": True, "message": "Inscripción eliminada"}
