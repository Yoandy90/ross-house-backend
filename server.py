"""
Ross House Rentals — Standalone FastAPI Backend
================================================
Property management API for Ross House Rentals LLC.
Handles: Auth, Properties, Tenants, Chat, Payments, Contracts, and more.
"""
import os
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

# ─── Configuration ────────────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "")
DB_NAME = os.environ.get("DB_NAME", "taxportal")
# Security: Generate secure default if not set, log warning
_default_secret = "ross-house-" + os.urandom(16).hex()
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", os.environ.get("JWT_SECRET", _default_secret))
if "ross-house-" in SECRET_KEY and len(SECRET_KEY) < 40:
    import warnings
    warnings.warn("⚠️ Using default JWT secret! Set JWT_SECRET_KEY in environment for production.")
PORT = int(os.environ.get("PORT", 8001))

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# ─── Database ─────────────────────────────────────────────────
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]


# ─── Lifespan ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🏠 Ross House Rentals API starting...")
    logger.info(f"   Database: {DB_NAME}")

    # Initialize rental storage if key available
    try:
        from rental_storage_service import init_storage
        init_storage()
        logger.info("   ✅ Object Storage initialized")
    except Exception as e:
        logger.warning(f"   ⚠️ Object Storage deferred: {e}")

    # Ensure Mashvisor cache indexes
    try:
        from rental.mashvisor_cache import ensure_indexes as cache_ensure_indexes
        await cache_ensure_indexes()
        logger.info("   ✅ Mashvisor Cache indexes ready")
    except Exception as e:
        logger.warning(f"   ⚠️ Mashvisor Cache indexes deferred: {e}")

    yield
    client.close()
    logger.info("🏠 Ross House Rentals API stopped.")


# ─── App ──────────────────────────────────────────────────────
app = FastAPI(
    title="Ross House Rentals API",
    version="1.0.0",
    description="Property Management Backend for Ross House Rentals LLC",
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────
# Security: Only allow specific origins
ALLOWED_ORIGINS = [
    "https://rosshouserentals.com",
    "https://www.rosshouserentals.com",
    "https://rosslending.com",
    "https://www.rosslending.com",
    "http://localhost:3000",  # Local development
    "http://localhost:8081",  # Expo development
]

# Add preview URLs for development/testing
import os
if os.environ.get("ENVIRONMENT") != "production":
    ALLOWED_ORIGINS.append("*")  # Allow all in non-production

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if os.environ.get("ENVIRONMENT") == "production" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health Check ─────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "ok",
        "service": "Ross House Rentals API",
        "version": "1.0.0",
        "database": db_status,
        "database_name": DB_NAME,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─── Register Rental Routers ─────────────────────────────────
try:
    from rental.shared import set_db
    set_db(db)

    # ── Initialize Mashvisor Cache ──
    from rental.mashvisor_cache import init_cache, ensure_indexes as cache_ensure_indexes
    init_cache(db)

    from rental.auth_router import router as auth_router
    from rental.properties_router import router as properties_router
    from rental.tenant_router import router as tenant_router
    from rental.contracts_router import router as contracts_router
    from rental.finances_router import router as finances_router
    from rental.owner_router import router as owner_router
    from rental.stripe_router import router as stripe_router
    from rental.investments_router import router as investments_router
    from rental.legal_router import router as legal_router
    from rental.communications_router import router as communications_router
    from rental.signatures_router import router as signatures_router
    from rental.chat_router import router as chat_router
    from rental.mashvisor_routes import router as mashvisor_router
    from rental.mashvisor_routes import public_router as mashvisor_public_router
    from rental.faq_router import router as faq_router
    from rental.faq_router import public_router as faq_public_router
    from rental.utility_payments_router import router as utility_payments_router

    app.include_router(auth_router, prefix="/api")
    app.include_router(properties_router, prefix="/api")
    app.include_router(tenant_router, prefix="/api")
    app.include_router(contracts_router, prefix="/api")
    app.include_router(finances_router, prefix="/api")
    app.include_router(owner_router, prefix="/api")
    app.include_router(stripe_router, prefix="/api")
    app.include_router(investments_router, prefix="/api")
    app.include_router(legal_router, prefix="/api")
    app.include_router(communications_router, prefix="/api")
    app.include_router(signatures_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(mashvisor_router, prefix="/api")
    app.include_router(mashvisor_public_router, prefix="/api")
    app.include_router(faq_router, prefix="/api")
    app.include_router(faq_public_router, prefix="/api")
    app.include_router(utility_payments_router, prefix="/api")

    # ── Initialize AI Brain ──
    from rental.ai_brain import RossHouseAIBrain
    from rental.ai_brain_router import router as ai_brain_router, set_ai_brain as set_router_brain
    from rental.chat_router import set_ai_brain as set_chat_ai_brain

    ai_brain = RossHouseAIBrain(db)
    set_router_brain(ai_brain)
    set_chat_ai_brain(ai_brain)
    app.include_router(ai_brain_router, prefix="/api")
    logger.info("🧠 AI Brain initialized and connected to chat")

    logger.info("✅ All rental routers + AI Brain registered successfully")

except Exception as e:
    import traceback
    logger.error(f"❌ Failed to register rental routers: {e}")
    logger.error(traceback.format_exc())


# ─── Static photo serving ─────────────────────────────────────
from fastapi.staticfiles import StaticFiles
import pathlib

photos_dir = pathlib.Path("property_photos")
photos_dir.mkdir(exist_ok=True)
app.mount("/property_photos", StaticFiles(directory=str(photos_dir)), name="property_photos")


# ─── Image Upload Endpoint ─────────────────────────────────────
from fastapi import UploadFile, File, HTTPException, Header
import uuid

@app.post("/api/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    authorization: str = Header(None)
):
    """Upload an image and return the URL. Used for receipts, photos, etc."""
    
    # Basic auth check (optional - can be stricter)
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "application/pdf"]
    content_type = file.content_type or "application/octet-stream"
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"File type {content_type} not allowed")
    
    # Read file content
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    # Generate unique filename
    ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    unique_name = f"receipts/{datetime.utcnow().strftime('%Y/%m')}/{uuid.uuid4().hex}.{ext}"
    
    try:
        # Try object storage first
        from rental_storage_service import put_object, init_storage
        init_storage()
        result = put_object(unique_name, content, content_type)
        
        # Return URL based on storage type
        if result.get("storage_type") == "object_storage":
            image_url = result.get("url", result.get("public_url", ""))
        else:
            # MongoDB fallback - return base64 data URL
            image_url = result.get("base64_data", "")
        
        return {
            "success": True,
            "url": image_url,
            "image_url": image_url,
            "filename": file.filename,
            "size": len(content),
            "storage_type": result.get("storage_type", "unknown")
        }
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        # Fallback to base64 if storage fails
        import base64
        b64_data = base64.b64encode(content).decode('utf-8')
        image_url = f"data:{content_type};base64,{b64_data}"
        return {
            "success": True,
            "url": image_url,
            "image_url": image_url,
            "filename": file.filename,
            "size": len(content),
            "storage_type": "base64_fallback"
        }


# ─── Run ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
