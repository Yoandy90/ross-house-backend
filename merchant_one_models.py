"""
Merchant One ACH Customer Vault & Recurring Models
Data models for Merchant One integration
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, date
import re


# ==================== VALIDATORS ====================

def validate_routing_number(routing: str) -> str:
    """Validate ABA routing number (9 digits + checksum)"""
    routing = re.sub(r'\D', '', routing)  # Remove non-digits
    if len(routing) != 9:
        raise ValueError('Routing number must be 9 digits')
    
    # ABA checksum validation
    # 3(d1 + d4 + d7) + 7(d2 + d5 + d8) + (d3 + d6 + d9) mod 10 = 0
    digits = [int(d) for d in routing]
    checksum = (
        3 * (digits[0] + digits[3] + digits[6]) +
        7 * (digits[1] + digits[4] + digits[7]) +
        (digits[2] + digits[5] + digits[8])
    )
    if checksum % 10 != 0:
        raise ValueError('Invalid routing number checksum')
    
    return routing


def normalize_phone(phone: str) -> str:
    """Normalize phone to digits only"""
    return re.sub(r'\D', '', phone) if phone else ''


def normalize_state(state: str) -> str:
    """Normalize state to 2-letter abbreviation"""
    state = state.strip().upper()
    
    # Common state name to abbreviation mapping
    state_map = {
        'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR',
        'CALIFORNIA': 'CA', 'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE',
        'FLORIDA': 'FL', 'GEORGIA': 'GA', 'HAWAII': 'HI', 'IDAHO': 'ID',
        'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA', 'KANSAS': 'KS',
        'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD',
        'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS',
        'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV',
        'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM', 'NEW YORK': 'NY',
        'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH', 'OKLAHOMA': 'OK',
        'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC',
        'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT',
        'VERMONT': 'VT', 'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV',
        'WISCONSIN': 'WI', 'WYOMING': 'WY', 'DISTRICT OF COLUMBIA': 'DC'
    }
    
    if state in state_map:
        return state_map[state]
    elif len(state) == 2 and state.isalpha():
        return state
    else:
        return state[:2].upper()  # Fallback to first 2 chars


def mask_account_number(account: str) -> str:
    """Mask account number showing only last 4 digits"""
    if not account or len(account) < 4:
        return '****'
    return '*' * (len(account) - 4) + account[-4:]


# ==================== REQUEST MODELS ====================

class CustomerInfo(BaseModel):
    """Customer personal information"""
    firstName: str = Field(..., min_length=1, max_length=50)
    lastName: str = Field(..., min_length=1, max_length=50)
    email: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    company: Optional[str] = Field(None, max_length=100)
    address1: str = Field(..., min_length=1, max_length=100)
    address2: Optional[str] = Field(None, max_length=100)
    city: str = Field(..., min_length=1, max_length=50)
    state: str = Field(..., min_length=2, max_length=50)
    postalCode: str = Field(..., min_length=5, max_length=10)
    country: str = Field(default='US', max_length=2)
    dateOfBirth: Optional[str] = Field(None, max_length=10)

    @validator('email')
    def validate_email(cls, v):
        if v:
            v = v.lower().strip()
            if '@' not in v or '.' not in v:
                raise ValueError('Invalid email format')
        return v
    
    @validator('phone')
    def validate_phone(cls, v):
        return normalize_phone(v) if v else None
    
    @validator('state')
    def validate_state(cls, v):
        return normalize_state(v)
    
    @validator('postalCode')
    def validate_postal_code(cls, v):
        v = re.sub(r'[^0-9\-]', '', v)
        if len(v) < 5:
            raise ValueError('Postal code must be at least 5 characters')
        return v


class BankInfo(BaseModel):
    """Bank account information for ACH"""
    checkName: str = Field(..., min_length=1, max_length=100, description="Name on check")
    routing: str = Field(..., min_length=9, max_length=9, description="ABA routing number")
    accountNumber: str = Field(..., min_length=4, max_length=17, description="Bank account number")
    accountHolderType: str = Field(default='personal', description="personal or business")
    accountType: str = Field(default='checking', description="checking or savings")
    secCode: str = Field(default='PPD', description="SEC code: PPD, CCD, WEB")

    @validator('routing')
    def validate_routing(cls, v):
        return validate_routing_number(v)
    
    @validator('accountNumber')
    def validate_account_number(cls, v):
        v = re.sub(r'[\s\-]', '', v)  # Remove spaces and dashes
        if not v.isdigit():
            raise ValueError('Account number must contain only digits')
        return v
    
    @validator('accountHolderType')
    def validate_holder_type(cls, v):
        v = v.lower()
        if v not in ['personal', 'business']:
            raise ValueError('Account holder type must be personal or business')
        return v
    
    @validator('accountType')
    def validate_account_type(cls, v):
        v = v.lower()
        if v not in ['checking', 'savings']:
            raise ValueError('Account type must be checking or savings')
        return v
    
    @validator('secCode')
    def validate_sec_code(cls, v):
        v = v.upper()
        if v not in ['PPD', 'CCD', 'WEB', 'TEL']:
            raise ValueError('SEC code must be PPD, CCD, WEB, or TEL')
        return v


class SubscriptionInfo(BaseModel):
    """Recurring subscription information"""
    planName: str = Field(..., min_length=1, max_length=100)
    amount: float = Field(..., gt=0)
    dayFrequency: int = Field(..., gt=0, description="Days between charges")
    startDate: str = Field(..., description="Start date MMDDYYYY or YYYY-MM-DD")
    planPayments: Optional[int] = Field(default=0, ge=0, description="0 for unlimited")
    productSku: Optional[str] = Field(None, max_length=50)
    orderDescription: Optional[str] = Field(None, max_length=255)

    @validator('startDate')
    def validate_start_date(cls, v):
        # Convert to MMDDYYYY format if needed
        v = v.replace('-', '').replace('/', '')
        if len(v) == 8:
            # Check if YYYYMMDD format
            if int(v[:4]) > 2000:
                # Convert YYYYMMDD to MMDDYYYY
                v = v[4:6] + v[6:8] + v[:4]
        return v
    
    @validator('amount')
    def validate_amount(cls, v):
        return round(float(v), 2)


class CreateVaultRequest(BaseModel):
    """Request to create customer vault"""
    customer: CustomerInfo
    bank: BankInfo


class CreateSubscriptionRequest(BaseModel):
    """Request to create subscription for existing vault customer"""
    customerVaultId: str
    subscription: SubscriptionInfo


class CreateVaultAndSubscriptionRequest(BaseModel):
    """Combined request to create vault and subscription"""
    customer: CustomerInfo
    bank: BankInfo
    subscription: SubscriptionInfo
    useAchDefaults: bool = Field(default=True)


# ==================== RESPONSE MODELS ====================

class MerchantOneResponse(BaseModel):
    """Parsed Merchant One API response"""
    success: bool
    responseCode: Optional[str] = None
    responseText: Optional[str] = None
    customerVaultId: Optional[str] = None
    subscriptionId: Optional[str] = None
    transactionId: Optional[str] = None
    authCode: Optional[str] = None
    rawResponse: Optional[str] = None
    errorMessage: Optional[str] = None


class VaultCustomerRecord(BaseModel):
    """Database record for vault customer"""
    id: str
    firstName: str
    lastName: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address1: str
    city: str
    state: str
    postalCode: str
    country: str
    maskedAccount: str
    customerVaultId: str
    subscriptionId: Optional[str] = None
    planName: Optional[str] = None
    planAmount: Optional[float] = None
    dayFrequency: Optional[int] = None
    subscriptionStartDate: Optional[str] = None
    dateOfBirth: Optional[str] = None
    age: Optional[int] = None
    vaultStatus: str = 'active'
    subscriptionStatus: Optional[str] = None
    lastError: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime


class CreateVaultResponse(BaseModel):
    """Response for vault creation"""
    success: bool
    vaultSuccess: bool = False
    subscriptionSuccess: bool = False
    customerVaultId: Optional[str] = None
    subscriptionId: Optional[str] = None
    maskedAccount: Optional[str] = None
    summaryMessage: str
    vaultError: Optional[str] = None
    subscriptionError: Optional[str] = None
    record: Optional[VaultCustomerRecord] = None


# ==================== AI PARSER MODELS ====================

class ParsedClientInfo(BaseModel):
    """Parsed client info from AI/text"""
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    country: str = 'US'
    checkName: Optional[str] = None
    routing: Optional[str] = None
    accountNumber: Optional[str] = None
    accountHolderType: str = 'personal'
    accountType: str = 'checking'
    reviewRequired: bool = False
    parsingNotes: List[str] = []
