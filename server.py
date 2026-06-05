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
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", os.environ.get("JWT_SECRET", "ross-house-default-secret"))
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


# ─── Run ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
