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

from database import get_db, serialize

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
    from rental.shared import verify_jwt_token
    
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    
    token = auth_header.replace("Bearer ", "")
    payload = verify_jwt_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido")
    
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
        return {
            "success": True,
            "message": "Ya estás inscrito en Credit Builder",
            "enrolled_at": existing.get('enrolled_at')
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
