"""Admin-only, read-only database isolation inventory."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .database_isolation_inventory import build_database_isolation_inventory
from .shared import auth_admin, get_db

router = APIRouter(tags=["operations"])


@router.get("/admin/operations/database-isolation-inventory")
async def database_isolation_inventory(
    admin=Depends(auth_admin),
    db=Depends(get_db),
):
    del admin
    return {
        "success": True,
        **await build_database_isolation_inventory(db),
    }
