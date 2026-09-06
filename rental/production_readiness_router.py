"""Admin-only, read-only production readiness report."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from .production_readiness import assess_production_readiness
from .shared import auth_admin

router = APIRouter(tags=["operations"])


@router.get("/admin/operations/production-readiness")
async def production_readiness(admin=Depends(auth_admin)):
    del admin
    return {
        "success": True,
        **assess_production_readiness(
            os.environ,
            database_name=os.environ.get("DB_NAME", ""),
        ),
    }
