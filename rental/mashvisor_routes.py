"""
Mashvisor API Integration Routes (with MongoDB Cache)
=====================================================
Real estate market data, property analysis, and investment metrics via RapidAPI.
All API calls pass through a MongoDB caching layer to minimize RapidAPI costs.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import httpx
import os
import logging

from rental.mashvisor_cache import (
    get_cached, set_cached, get_cache_stats, clear_cache,
)

logger = logging.getLogger("mashvisor")

router = APIRouter(prefix="/admin/market-data", tags=["Market Data"])

MASHVISOR_BASE = "https://mashvisor-api.p.rapidapi.com"
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "21ea9bf87bmsh34cf3a650404000p1365f9jsn9af040d3e196")
RAPIDAPI_HOST = "mashvisor-api.p.rapidapi.com"

HEADERS = {
    "x-rapidapi-host": RAPIDAPI_HOST,
    "x-rapidapi-key": RAPIDAPI_KEY,
}


# ═══════════════════════════════════════════════════════════════════════════════
# CORE HELPER — with cache integration
# ═══════════════════════════════════════════════════════════════════════════════

async def _mashvisor_get(path: str, params: dict = None, cache_category: str = "default") -> dict:
    """
    GET request to Mashvisor API with transparent MongoDB caching.
    1. Check cache first → return if hit
    2. Call Mashvisor API on miss
    3. Store response in cache for next caller
    """
    # ── 1. Try cache ──
    cached = await get_cached(cache_category, path, params)
    if cached is not None:
        return cached

    # ── 2. Call Mashvisor API ──
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{MASHVISOR_BASE}{path}"
        resp = await client.get(url, headers=HEADERS, params=params)
        data = resp.json()
        if resp.status_code != 200 or data.get("status") == "error":
            raise HTTPException(
                status_code=resp.status_code or 500,
                detail=data.get("message", "Mashvisor API error"),
            )

    # ── 3. Store in cache ──
    await set_cached(cache_category, path, data, params)

    return data


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS (require auth)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/city/{state}/{city}")
async def get_city_market_data(state: str, city: str):
    """Get city-level investment performance metrics."""
    data = await _mashvisor_get(
        f"/city/investment/{state.upper()}/{city}",
        cache_category="city_market",
    )
    return {
        "status": "success",
        "market_data": data.get("content", {}),
        "city": city,
        "state": state.upper(),
    }


@router.get("/neighborhoods/{state}/{city}")
async def get_top_neighborhoods(
    state: str, city: str, items: int = Query(default=10, le=20)
):
    """Get top neighborhoods with investment metrics."""
    data = await _mashvisor_get(
        "/trends/neighborhoods",
        params={"city": city, "state": state.upper(), "items": items},
        cache_category="neighborhoods",
    )
    content = data.get("content", {})
    return {
        "status": "success",
        "neighborhoods": content.get("neighborhoods", []),
        "total": content.get("total_results", 0),
        "city": city,
        "state": state.upper(),
    }


@router.get("/listings/{state}/{city}")
async def get_city_listings(
    state: str,
    city: str,
    page: int = Query(default=1, ge=1),
    page_limit: int = Query(default=12, le=50),
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    beds: Optional[int] = None,
    baths: Optional[int] = None,
    property_type: Optional[str] = None,
    status: Optional[str] = Query(default="active", description="Filter: active, inactive, all"),
):
    """Get property listings in a city. Defaults to active listings."""
    params: dict = {
        "city": city,
        "state": state.upper(),
        "page": page,
        "page_limit": page_limit,
    }
    if status and status.lower() != "all":
        params["status"] = status
    if min_price:
        params["min_price"] = min_price
    if max_price:
        params["max_price"] = max_price
    if beds:
        params["beds"] = beds
    if baths:
        params["baths"] = baths
    if property_type:
        params["property_type"] = property_type

    data = await _mashvisor_get("/city/listings", params=params, cache_category="listings")
    content = data.get("content", {})
    
    # Secure image URLs for iOS ATS compliance
    properties = content.get("properties", [])
    for p in properties:
        img = p.get("image") or p.get("image_url", "")
        p["image"] = _secure_url(img)
    
    return {
        "status": "success",
        "listings": properties,
        "total": content.get("total_results", 0),
        "page": content.get("page", page),
        "total_pages": content.get("total_pages", 0),
    }


@router.get("/property-analysis")
async def analyze_property(
    address: str = Query(...),
    city: str = Query(...),
    state: str = Query(...),
    zip_code: Optional[str] = None,
):
    """Get detailed property analysis including valuation, ROI, and neighborhood data."""
    params: dict = {
        "address": address,
        "city": city,
        "state": state.upper(),
    }
    if zip_code:
        params["zip_code"] = zip_code

    data = await _mashvisor_get("/property", params=params, cache_category="property_analysis")
    content = data.get("content", {})

    roi = content.get("ROI", {})
    neighborhood = content.get("neighborhood", {})

    return {
        "status": "success",
        "property": {
            "id": content.get("id"),
            "address": address,
            "city": city,
            "state": state.upper(),
            "zip": content.get("zip"),
            "beds": content.get("beds"),
            "baths": content.get("baths"),
            "sqft": content.get("sqft"),
            "home_type": content.get("homeType"),
            "year_built": content.get("yearBuilt"),
            "last_sale_price": content.get("lastSalePrice"),
            "last_sale_date": content.get("lastSaleDate"),
            "tax": content.get("tax"),
            "image": content.get("image", {}).get("image"),
            "extra_images": content.get("extra_images", []),
        },
        "investment": {
            "traditional_ROI": roi.get("traditional_ROI"),
            "airbnb_ROI": roi.get("airbnb_ROI"),
            "traditional_rental": roi.get("traditional_rental"),
            "airbnb_rental": roi.get("airbnb_rental"),
            "traditional_cap_rate": roi.get("traditional_cap_rate"),
            "airbnb_cap_rate": roi.get("airbnb_cap_rate"),
        },
        "neighborhood": {
            "name": neighborhood.get("name"),
            "median_value": neighborhood.get("singleHomeValue"),
            "median_value_formatted": neighborhood.get("singleHomeValue_formatted"),
            "mashMeter": neighborhood.get("mashMeter"),
            "walkscore": neighborhood.get("walkscore"),
            "airbnb_count": neighborhood.get("airbnb_properties_count"),
            "traditional_count": neighborhood.get("traditional_properties_count"),
        },
        "mortgage_rates": {
            "thirty_year_fixed": content.get("stateInterest", {}).get("thirtyYearFixed"),
            "fifteen_year_fixed": content.get("stateInterest", {}).get("fifteenYearFixed"),
            "five_one_arm": content.get("stateInterest", {}).get("fiveOneARM"),
        },
    }


@router.get("/top-properties/{state}/{city}")
async def get_top_properties(state: str, city: str):
    """Get top investment properties in a city."""
    data = await _mashvisor_get(
        f"/city/properties/{state.upper()}/{city}",
        cache_category="top_properties",
    )
    content = data.get("content", {})
    properties = content.get("properties", [])

    formatted = []
    for p in properties[:20]:
        formatted.append({
            "id": p.get("id"),
            "address": p.get("address"),
            "zip_code": p.get("zip_code"),
            "city": p.get("city"),
            "state": p.get("state"),
            "type": p.get("type"),
            "beds": p.get("beds"),
            "baths": p.get("baths"),
            "sqft": p.get("sqft"),
            "list_price": p.get("list_price"),
            "list_price_formatted": p.get("list_price_formatted"),
            "image": _secure_url(p.get("image") or p.get("image_url")),
            "traditional_ROI": p.get("traditional_ROI"),
            "airbnb_ROI": p.get("airbnb_ROI"),
            "traditional_rental": p.get("traditional_rental"),
            "airbnb_rental": p.get("airbnb_rental"),
            "traditional_cap": p.get("traditional_cap"),
            "airbnb_cap": p.get("airbnb_cap"),
            "days_on_market": p.get("days_on_market"),
            "neighborhood": p.get("neighborhood"),
        })

    return {
        "status": "success",
        "properties": formatted,
        "total": len(formatted),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE MANAGEMENT ENDPOINTS (Admin)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/cache/stats")
async def cache_stats():
    """Get cache performance statistics."""
    stats = await get_cache_stats()
    return {"status": "success", "cache": stats}


@router.delete("/cache/clear")
async def cache_clear(category: Optional[str] = Query(None, description="Clear specific category or all")):
    """Clear cache entries. Optionally specify a category."""
    deleted = await clear_cache(category)
    return {
        "status": "success",
        "message": f"Cleared {deleted} cache entries" + (f" in '{category}'" if category else ""),
        "deleted": deleted,
    }


def _secure_url(url: str) -> str:
    """Convert http:// to https:// for iOS App Transport Security."""
    if url and url.startswith("http://"):
        return "https://" + url[7:]
    return url or ""


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENDPOINTS (No auth — for mobile app users)
# ═══════════════════════════════════════════════════════════════════════════════

public_router = APIRouter(prefix="/public/market", tags=["Public Market Data"])


@public_router.get("/listings/{state}/{city}")
async def public_market_listings(
    state: str, city: str,
    page: int = Query(1, ge=1), page_limit: int = Query(12, ge=1, le=50),
    min_price: Optional[int] = None, max_price: Optional[int] = None,
    beds: Optional[int] = None, baths: Optional[int] = None,
    property_type: Optional[str] = None,
    status: Optional[str] = Query(default="active", description="Filter: active, inactive, all"),
):
    """Public: browse properties for sale (no auth required). Cached."""
    params = {
        "state": state.upper(), "city": city,
        "page": page, "page_limit": page_limit,
    }
    if status and status.lower() != "all":
        params["status"] = status
    if min_price:
        params["min_price"] = min_price
    if max_price:
        params["max_price"] = max_price
    if beds:
        params["beds"] = beds
    if baths:
        params["baths"] = baths
    if property_type:
        params["property_type"] = property_type

    data = await _mashvisor_get("/city/listings", params, cache_category="listings")
    content = data.get("content", {})
    listings_raw = content.get("properties", [])
    total = content.get("total_results", 0)

    formatted = []
    for p in listings_raw[:page_limit]:
        formatted.append({
            "id": p.get("id", ""),
            "address": p.get("address", ""),
            "city": p.get("city", city),
            "state": p.get("state", state),
            "zip_code": p.get("zip_code", ""),
            "neighborhood": p.get("neighborhood", ""),
            "type": p.get("type", ""),
            "beds": p.get("beds", 0),
            "baths": p.get("baths", 0),
            "sqft": p.get("sqft", 0),
            "list_price": p.get("list_price", 0),
            "image_url": _secure_url(p.get("image") or p.get("image_url", "")),
            "status": p.get("status", ""),
            "days_on_market": p.get("days_on_market", 0),
            "is_foreclosure": p.get("is_foreclosure", 0),
            "latitude": p.get("latitude"),
            "longitude": p.get("longitude"),
        })
    return {"status": "success", "listings": formatted, "total": total, "page": page}


@public_router.get("/overview/{state}/{city}")
async def public_market_overview(state: str, city: str):
    """Public: market overview for a city. Cached 24h."""
    data = await _mashvisor_get(
        f"/city/investment/{state.upper()}/{city}",
        cache_category="overview",
    )
    content = data.get("content", {})
    return {
        "status": "success",
        "market": {
            "median_price": content.get("median_property_price") or content.get("median_price", 0),
            "num_properties": content.get("num_of_properties", 0),
            "sqft": content.get("sqft", 0),
            "traditional_rental": content.get("median_traditional_rental", 0),
            "airbnb_rental": content.get("median_airbnb_rental", 0),
            "traditional_roi": round(content.get("traditional_ROI", 0) or 0, 2),
            "airbnb_roi": round(content.get("airbnb_ROI", 0) or 0, 2),
            "occupancy": round(content.get("airbnb_occupancy", 0) or 0),
        },
    }


@public_router.post("/interest")
async def public_property_interest(request):
    """Record interest in a property (generates a lead for admin). NOT cached."""
    from rental.shared import get_db, send_rental_push_to_admins
    from datetime import datetime, timezone

    body = await request.json()
    db = get_db()

    lead = {
        "user_id": body.get("user_id", ""),
        "user_name": body.get("user_name", "Visitante"),
        "user_phone": body.get("user_phone", ""),
        "user_email": body.get("user_email", ""),
        "property_id": body.get("property_id", ""),
        "property_address": body.get("property_address", ""),
        "property_city": body.get("property_city", ""),
        "property_state": body.get("property_state", ""),
        "property_price": body.get("property_price", 0),
        "property_image": body.get("property_image", ""),
        "message": body.get("message", ""),
        "source": "mobile_app",
        "status": "new",
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.property_leads.insert_one(lead)

    try:
        await send_rental_push_to_admins(
            title="🏠 Nuevo Interesado",
            body=f"{lead['user_name']} interesado en {lead['property_address']} ({lead['property_city']})",
            data={"type": "property_lead", "lead_id": str(result.inserted_id)},
        )
    except Exception:
        pass

    return {"success": True, "lead_id": str(result.inserted_id)}
