"""NOAA/NCEI Climate Data Online historical backfill for Ross House.

NWS remains the live/forecast source. NCEI CDO is optional and is used only to
backfill historical daily weather so Ross House can compare HVAC/property
performance against past outdoor conditions.

The NCEI token is read from NCEI_CDO_TOKEN. In production that value may be
stored encrypted through the existing Admin API Keys manager.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import httpx
from bson import ObjectId
from fastapi import HTTPException
from pymongo import UpdateOne


NCEI_BASE = "https://www.ncei.noaa.gov/cdo-web/api/v2"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
DATASET = "GHCND"
DATA_TYPES = ("TAVG", "TMAX", "TMIN", "PRCP", "SNOW", "SNWD")
MAX_YEAR = 2100


def now() -> datetime:
    return datetime.now(timezone.utc)


def configured() -> bool:
    return bool(str(os.getenv("NCEI_CDO_TOKEN") or "").strip())


def _token() -> str:
    value = str(os.getenv("NCEI_CDO_TOKEN") or "").strip()
    if not value:
        raise HTTPException(503, "ncei_token_not_configured")
    return value


def _id_values(value: str):
    values = [str(value)]
    if ObjectId.is_valid(str(value)):
        values.append(ObjectId(str(value)))
    return values


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _haversine_miles(lat1, lon1, lat2, lon2):
    if not all(_finite(v) for v in (lat1, lon1, lat2, lon2)):
        return None
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def _ncei_json(path: str, params=None):
    if not path.startswith("/"):
        path = "/" + path
    url = NCEI_BASE + path
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "www.ncei.noaa.gov":
        raise HTTPException(503, "ncei_endpoint_invalid")
    async with httpx.AsyncClient(
        timeout=25.0,
        follow_redirects=False,
        headers={"token": _token(), "Accept": "application/json"},
    ) as client:
        response = await client.get(url, params=params)
    if response.status_code in (401, 403):
        raise HTTPException(422, "ncei_token_invalid")
    if response.status_code == 429:
        raise HTTPException(429, "ncei_rate_limited")
    if response.status_code >= 400:
        raise HTTPException(502, "ncei_upstream_error")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(502, "ncei_invalid_response") from exc


async def test_connection():
    payload = await _ncei_json("/datasets/GHCND")
    return {
        "ok": bool(payload.get("id") == "GHCND"),
        "dataset": payload.get("id"),
        "name": payload.get("name"),
        "min_date": payload.get("mindate"),
        "max_date": payload.get("maxdate"),
    }


async def ensure_indexes(db):
    await db.climate_historical_weather_daily.create_index(
        [("property_id", 1), ("date", 1)], unique=True
    )
    await db.climate_historical_weather_daily.create_index(
        [("property_id", 1), ("year", 1)]
    )
    await db.climate_ncei_station_cache.create_index(
        [("property_id", 1), ("year", 1)], unique=True
    )
    await db.climate_ncei_backfill_state.create_index("updated_at")


async def _property(db, property_id: str):
    return await db.properties.find_one(
        {"_id": {"$in": _id_values(property_id)}},
        {
            "address": 1,
            "city": 1,
            "state": 1,
            "zip": 1,
            "zip_code": 1,
            "latitude": 1,
            "longitude": 1,
            "name": 1,
        },
    )


def _address(prop: dict | None) -> str:
    if not prop:
        return ""
    return ", ".join(
        str(v).strip()
        for v in (
            prop.get("address"),
            prop.get("city"),
            prop.get("state"),
            prop.get("zip") or prop.get("zip_code"),
        )
        if v and str(v).strip()
    )


async def _census_geocode(address: str):
    if not address:
        return None
    params = {
        "address": address[:160],
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.get(CENSUS_GEOCODER, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    matches = (((payload or {}).get("result") or {}).get("addressMatches") or [])
    if not matches:
        return None
    coords = matches[0].get("coordinates") or {}
    lat, lon = coords.get("y"), coords.get("x")
    if not (_finite(lat) and _finite(lon)):
        return None
    return float(lat), float(lon)


async def _property_location(db, property_id: str, prop: dict):
    saved = await db.weather_locations.find_one(
        {"$or": [{"_id": property_id}, {"property_id": property_id}]},
        {"latitude": 1, "longitude": 1},
    )
    if saved and _finite(saved.get("latitude")) and _finite(saved.get("longitude")):
        return float(saved["latitude"]), float(saved["longitude"])
    if _finite(prop.get("latitude")) and _finite(prop.get("longitude")):
        return float(prop["latitude"]), float(prop["longitude"])
    return await _census_geocode(_address(prop))


def _station_public(row: dict, distance=None):
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "elevation": row.get("elevation"),
        "datacoverage": row.get("datacoverage"),
        "mindate": row.get("mindate"),
        "maxdate": row.get("maxdate"),
        "distance_miles": round(distance, 2) if _finite(distance) else None,
    }


async def station_for_property(db, property_id: str, year: int):
    cache_id = f"{property_id}:{year}"
    cached = await db.climate_ncei_station_cache.find_one({"_id": cache_id})
    if cached and cached.get("station"):
        return cached["station"]

    prop = await _property(db, property_id)
    if not prop:
        raise HTTPException(404, "climate_property_invalid")

    start = f"{year:04d}-01-01"
    end = f"{year:04d}-12-31"
    coords = await _property_location(db, property_id, prop)

    rows = []
    if coords:
        lat, lon = coords
        for span in (0.75, 1.5, 3.0):
            extent = f"{lat-span:.4f},{lon-span:.4f},{lat+span:.4f},{lon+span:.4f}"
            payload = await _ncei_json(
                "/stations",
                {
                    "datasetid": DATASET,
                    "extent": extent,
                    "startdate": start,
                    "enddate": end,
                    "sortfield": "datacoverage",
                    "sortorder": "desc",
                    "limit": 1000,
                },
            )
            rows = payload.get("results") or []
            if rows:
                break
    if not rows:
        zip_code = str(prop.get("zip") or prop.get("zip_code") or "").strip()
        if zip_code:
            payload = await _ncei_json(
                "/stations",
                {
                    "datasetid": DATASET,
                    "locationid": f"ZIP:{zip_code}",
                    "startdate": start,
                    "enddate": end,
                    "sortfield": "datacoverage",
                    "sortorder": "desc",
                    "limit": 1000,
                },
            )
            rows = payload.get("results") or []

    if not rows:
        raise HTTPException(404, "ncei_station_not_found")

    best = rows[0]
    best_distance = None
    if coords:
        lat, lon = coords
        candidates = []
        for row in rows:
            distance = _haversine_miles(
                lat, lon, row.get("latitude"), row.get("longitude")
            )
            if distance is not None:
                coverage = float(row.get("datacoverage") or 0)
                candidates.append((distance, -coverage, row))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            best_distance, _, best = candidates[0]

    station = _station_public(best, best_distance)
    await db.climate_ncei_station_cache.update_one(
        {"_id": cache_id},
        {
            "$set": {
                "property_id": property_id,
                "year": year,
                "station": station,
                "updated_at": now(),
            },
            "$setOnInsert": {"created_at": now()},
        },
        upsert=True,
    )
    return station


async def _year_rows(station_id: str, year: int):
    today = date.today()
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    if year == today.year:
        end = today
    params = [
        ("datasetid", DATASET),
        ("stationid", station_id),
        ("startdate", start.isoformat()),
        ("enddate", end.isoformat()),
        ("units", "standard"),
        ("limit", "1000"),
    ]
    for data_type in DATA_TYPES:
        params.append(("datatypeid", data_type))

    rows = []
    offset = 1
    while True:
        page_params = [*params, ("offset", str(offset))]
        payload = await _ncei_json("/data", page_params)
        page = payload.get("results") or []
        rows.extend(page)
        meta = ((payload.get("metadata") or {}).get("resultset") or {})
        total = int(meta.get("count") or len(rows))
        if not page or len(rows) >= total:
            break
        offset += len(page)
        if len(rows) > 10000:
            raise HTTPException(502, "ncei_result_too_large")
    return rows


def _daily_docs(property_id: str, year: int, station: dict, rows: list[dict]):
    by_date: dict[str, dict] = {}
    for row in rows:
        raw_date = str(row.get("date") or "")[:10]
        data_type = str(row.get("datatype") or "")
        value = row.get("value")
        if not raw_date or data_type not in DATA_TYPES or not _finite(value):
            continue
        doc = by_date.setdefault(raw_date, {})
        doc[data_type] = float(value)

    fields = {
        "TAVG": "tavg_f",
        "TMAX": "tmax_f",
        "TMIN": "tmin_f",
        "PRCP": "prcp_in",
        "SNOW": "snow_in",
        "SNWD": "snow_depth_in",
    }
    docs = []
    for day, values in sorted(by_date.items()):
        mapped = {fields[key]: value for key, value in values.items() if key in fields}
        docs.append(
            {
                "_id": f"{property_id}:{day}",
                "property_id": property_id,
                "date": day,
                "year": year,
                "source": "NCEI_CDO_GHCND",
                "dataset": DATASET,
                "station_id": station.get("id"),
                "station_name": station.get("name"),
                "station_distance_miles": station.get("distance_miles"),
                "units": "standard",
                **mapped,
                "imported_at": now(),
            }
        )
    return docs


async def backfill_year(db, property_id: str, year: int, actor: str = ""):
    current_year = date.today().year
    if year < 1900 or year > current_year or year > MAX_YEAR:
        raise HTTPException(422, "ncei_year_invalid")
    if not configured():
        raise HTTPException(503, "ncei_token_not_configured")
    prop = await _property(db, property_id)
    if not prop:
        raise HTTPException(404, "climate_property_invalid")

    station = await station_for_property(db, property_id, year)
    rows = await _year_rows(str(station.get("id") or ""), year)
    docs = _daily_docs(property_id, year, station, rows)
    if not docs:
        raise HTTPException(404, "ncei_historical_data_not_found")

    operations = [
        UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
        for doc in docs
    ]
    if operations:
        await db.climate_historical_weather_daily.bulk_write(operations, ordered=False)

    state = {
        "property_id": property_id,
        "last_year": year,
        "last_station": station,
        "last_days_imported": len(docs),
        "last_actor": actor,
        "updated_at": now(),
    }
    await db.climate_ncei_backfill_state.update_one(
        {"_id": property_id},
        {
            "$set": state,
            "$addToSet": {"completed_years": year},
            "$setOnInsert": {"created_at": now()},
        },
        upsert=True,
    )
    return {
        "ok": True,
        "property_id": property_id,
        "year": year,
        "days_imported": len(docs),
        "station": station,
    }


async def property_summary(db, property_id: str):
    state = await db.climate_ncei_backfill_state.find_one({"_id": property_id}) or {}
    rows = await db.climate_historical_weather_daily.aggregate(
        [
            {"$match": {"property_id": property_id}},
            {
                "$group": {
                    "_id": None,
                    "days": {"$sum": 1},
                    "first_date": {"$min": "$date"},
                    "last_date": {"$max": "$date"},
                    "years": {"$addToSet": "$year"},
                }
            },
        ]
    ).to_list(1)
    summary = rows[0] if rows else {}
    return {
        "property_id": property_id,
        "configured": configured(),
        "days": int(summary.get("days") or 0),
        "first_date": summary.get("first_date"),
        "last_date": summary.get("last_date"),
        "years": sorted(summary.get("years") or []),
        "completed_years": sorted(state.get("completed_years") or []),
        "last_station": state.get("last_station"),
        "updated_at": state.get("updated_at").isoformat()
        if isinstance(state.get("updated_at"), datetime)
        else state.get("updated_at"),
    }


async def status(db):
    property_count = await db.climate_historical_weather_daily.distinct("property_id")
    day_count = await db.climate_historical_weather_daily.count_documents({})
    state_count = await db.climate_ncei_backfill_state.count_documents({})
    return {
        "configured": configured(),
        "provider": "NOAA / NCEI Climate Data Online",
        "dataset": DATASET,
        "purpose": "historical_backfill",
        "limits": {"requests_per_second": 5, "requests_per_day": 10000},
        "properties_with_history": len(property_count),
        "historical_days": day_count,
        "backfill_states": state_count,
    }
