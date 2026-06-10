"""
Consent Forms Router
API endpoints for generating and managing consent/authorization documents
"""
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId

from rental.shared import get_db, serialize
from consent_forms_service import (
    generate_background_check_consent,
    generate_income_verification_consent,
    generate_photo_video_consent,
    generate_ach_authorization,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════
class BackgroundCheckRequest(BaseModel):
    applicant_name: str
    applicant_email: Optional[str] = ""
    applicant_phone: Optional[str] = ""
    applicant_ssn_last4: Optional[str] = "XXXX"
    applicant_dob: Optional[str] = ""
    property_address: Optional[str] = ""
    signature: Optional[str] = None


class IncomeVerificationRequest(BaseModel):
    applicant_name: str
    employer_name: Optional[str] = ""
    employer_phone: Optional[str] = ""
    applicant_position: Optional[str] = ""
    signature: Optional[str] = None


class PhotoVideoConsentRequest(BaseModel):
    tenant_name: str
    property_address: Optional[str] = ""
    signature: Optional[str] = None


class ACHAuthorizationRequest(BaseModel):
    tenant_name: str
    bank_name: Optional[str] = ""
    account_type: Optional[str] = "checking"
    routing_number: Optional[str] = ""
    account_number_last4: Optional[str] = "XXXX"
    monthly_amount: Optional[float] = 0
    property_address: Optional[str] = ""
    signature: Optional[str] = None


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


async def save_consent_record(db, consent_type: str, user_id: str, data: dict, pdf_base64: str):
    """Save consent record to database"""
    record = {
        "type": consent_type,
        "user_id": user_id,
        "data": data,
        "signed_at": datetime.utcnow(),
        "pdf_base64": pdf_base64[:100] + "...",  # Store reference, not full PDF
        "status": "signed",
        "created_at": datetime.utcnow(),
    }
    result = await db.consent_records.insert_one(record)
    return str(result.inserted_id)


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════
@router.post('/consents/background-check')
async def create_background_check_consent(request: Request, data: BackgroundCheckRequest):
    """Generate Background Check Authorization PDF"""
    user = await get_current_user(request)
    
    date_signed = datetime.now().strftime('%m/%d/%Y')
    
    pdf_base64 = generate_background_check_consent(
        applicant_name=data.applicant_name,
        applicant_email=data.applicant_email,
        applicant_phone=data.applicant_phone,
        applicant_ssn_last4=data.applicant_ssn_last4,
        applicant_dob=data.applicant_dob,
        property_address=data.property_address,
        signature_data=data.signature,
        date_signed=date_signed,
    )
    
    # Save record
    db = get_db()
    record_id = await save_consent_record(
        db, "background_check", 
        user.get('user_id') or user.get('sub'),
        data.dict(),
        pdf_base64
    )
    
    filename = f"Background_Check_Authorization_{data.applicant_name.replace(' ', '_')}.pdf"
    
    return {
        "success": True,
        "pdf_base64": pdf_base64,
        "filename": filename,
        "record_id": record_id,
        "signed_at": date_signed,
    }


@router.post('/consents/income-verification')
async def create_income_verification_consent(request: Request, data: IncomeVerificationRequest):
    """Generate Income Verification Authorization PDF"""
    user = await get_current_user(request)
    
    date_signed = datetime.now().strftime('%m/%d/%Y')
    
    pdf_base64 = generate_income_verification_consent(
        applicant_name=data.applicant_name,
        employer_name=data.employer_name,
        employer_phone=data.employer_phone,
        applicant_position=data.applicant_position,
        signature_data=data.signature,
        date_signed=date_signed,
    )
    
    # Save record
    db = get_db()
    record_id = await save_consent_record(
        db, "income_verification",
        user.get('user_id') or user.get('sub'),
        data.dict(),
        pdf_base64
    )
    
    filename = f"Income_Verification_{data.applicant_name.replace(' ', '_')}.pdf"
    
    return {
        "success": True,
        "pdf_base64": pdf_base64,
        "filename": filename,
        "record_id": record_id,
        "signed_at": date_signed,
    }


@router.post('/consents/photo-video')
async def create_photo_video_consent(request: Request, data: PhotoVideoConsentRequest):
    """Generate Photo/Video Consent PDF"""
    user = await get_current_user(request)
    
    date_signed = datetime.now().strftime('%m/%d/%Y')
    
    pdf_base64 = generate_photo_video_consent(
        tenant_name=data.tenant_name,
        property_address=data.property_address,
        signature_data=data.signature,
        date_signed=date_signed,
    )
    
    # Save record
    db = get_db()
    record_id = await save_consent_record(
        db, "photo_video",
        user.get('user_id') or user.get('sub'),
        data.dict(),
        pdf_base64
    )
    
    filename = f"Photo_Video_Consent_{data.tenant_name.replace(' ', '_')}.pdf"
    
    return {
        "success": True,
        "pdf_base64": pdf_base64,
        "filename": filename,
        "record_id": record_id,
        "signed_at": date_signed,
    }


@router.post('/consents/ach-authorization')
async def create_ach_authorization(request: Request, data: ACHAuthorizationRequest):
    """Generate ACH/Auto-Debit Authorization PDF"""
    user = await get_current_user(request)
    
    date_signed = datetime.now().strftime('%m/%d/%Y')
    
    pdf_base64 = generate_ach_authorization(
        tenant_name=data.tenant_name,
        bank_name=data.bank_name,
        account_type=data.account_type,
        routing_number=data.routing_number,
        account_number_last4=data.account_number_last4,
        monthly_amount=data.monthly_amount,
        property_address=data.property_address,
        signature_data=data.signature,
        date_signed=date_signed,
    )
    
    # Save record
    db = get_db()
    record_id = await save_consent_record(
        db, "ach_authorization",
        user.get('user_id') or user.get('sub'),
        data.dict(),
        pdf_base64
    )
    
    filename = f"ACH_Authorization_{data.tenant_name.replace(' ', '_')}.pdf"
    
    return {
        "success": True,
        "pdf_base64": pdf_base64,
        "filename": filename,
        "record_id": record_id,
        "signed_at": date_signed,
    }


@router.get('/consents/my-consents')
async def get_my_consents(request: Request):
    """Get all consent records for current user"""
    user = await get_current_user(request)
    user_id = user.get('user_id') or user.get('sub')
    
    db = get_db()
    consents = await db.consent_records.find({
        "user_id": user_id
    }).sort("created_at", -1).to_list(100)
    
    # Format response (exclude PDF data)
    formatted = []
    for c in consents:
        formatted.append({
            "id": str(c["_id"]),
            "type": c.get("type"),
            "signed_at": c.get("signed_at"),
            "status": c.get("status"),
            "data": {
                "applicant_name": c.get("data", {}).get("applicant_name") or c.get("data", {}).get("tenant_name"),
                "property_address": c.get("data", {}).get("property_address"),
            }
        })
    
    return {
        "success": True,
        "consents": formatted,
        "total": len(formatted),
    }


@router.get('/consents/{consent_id}/pdf')
async def get_consent_pdf(consent_id: str, request: Request):
    """Regenerate and get PDF for a specific consent"""
    user = await get_current_user(request)
    user_id = user.get('user_id') or user.get('sub')
    
    db = get_db()
    
    try:
        consent = await db.consent_records.find_one({
            "_id": ObjectId(consent_id),
            "user_id": user_id
        })
    except:
        raise HTTPException(status_code=400, detail="ID inválido")
    
    if not consent:
        raise HTTPException(status_code=404, detail="Consentimiento no encontrado")
    
    # Regenerate PDF based on type
    consent_type = consent.get("type")
    data = consent.get("data", {})
    date_signed = consent.get("signed_at", datetime.now()).strftime('%m/%d/%Y') if consent.get("signed_at") else None
    
    if consent_type == "background_check":
        pdf_base64 = generate_background_check_consent(
            applicant_name=data.get("applicant_name", ""),
            applicant_email=data.get("applicant_email", ""),
            applicant_phone=data.get("applicant_phone", ""),
            applicant_ssn_last4=data.get("applicant_ssn_last4", "XXXX"),
            applicant_dob=data.get("applicant_dob", ""),
            property_address=data.get("property_address", ""),
            signature_data=data.get("signature"),
            date_signed=date_signed,
        )
        filename = f"Background_Check_{data.get('applicant_name', 'Unknown').replace(' ', '_')}.pdf"
        
    elif consent_type == "income_verification":
        pdf_base64 = generate_income_verification_consent(
            applicant_name=data.get("applicant_name", ""),
            employer_name=data.get("employer_name", ""),
            employer_phone=data.get("employer_phone", ""),
            applicant_position=data.get("applicant_position", ""),
            signature_data=data.get("signature"),
            date_signed=date_signed,
        )
        filename = f"Income_Verification_{data.get('applicant_name', 'Unknown').replace(' ', '_')}.pdf"
        
    elif consent_type == "photo_video":
        pdf_base64 = generate_photo_video_consent(
            tenant_name=data.get("tenant_name", ""),
            property_address=data.get("property_address", ""),
            signature_data=data.get("signature"),
            date_signed=date_signed,
        )
        filename = f"Photo_Video_Consent_{data.get('tenant_name', 'Unknown').replace(' ', '_')}.pdf"
        
    elif consent_type == "ach_authorization":
        pdf_base64 = generate_ach_authorization(
            tenant_name=data.get("tenant_name", ""),
            bank_name=data.get("bank_name", ""),
            account_type=data.get("account_type", "checking"),
            routing_number=data.get("routing_number", ""),
            account_number_last4=data.get("account_number_last4", "XXXX"),
            monthly_amount=data.get("monthly_amount", 0),
            property_address=data.get("property_address", ""),
            signature_data=data.get("signature"),
            date_signed=date_signed,
        )
        filename = f"ACH_Authorization_{data.get('tenant_name', 'Unknown').replace(' ', '_')}.pdf"
    else:
        raise HTTPException(status_code=400, detail="Tipo de consentimiento no soportado")
    
    return {
        "success": True,
        "pdf_base64": pdf_base64,
        "filename": filename,
    }
