"""NOAA/NWS weather context for Ross House climate intelligence.

This module uses only fixed US Government endpoints:
- National Weather Service api.weather.gov for observations, forecasts and alerts.
- US Census Geocoder for property-address coordinates when a manual coordinate
  override is not already stored.

Network failures are deliberately non-fatal to thermostat telemetry. Weather data
is cached in MongoDB and must never be treated as a physical sensor installed at
the property.
"""
from __future__ import annotations

import math
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from bson import ObjectId
from fastapi import HTTPException


NWS_BASE = "https://api.weather.gov"
CENSUS_GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
DEFAULT_CACHE_MINUTES = 10
POINT_REFRESH_DAYS = 7


def now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return os.getenv("CLIMATE_WEATHER_ENABLED") == "true"


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _property_ids(value: str):
    values = [value]
    if ObjectId.is_valid(str(value)):
        values.append(ObjectId(str(value)))
    return values


def _nws_user_agent() -> str:
    return os.getenv(
        "NWS_USER_AGENT",
        "RossHouseRentals/1.0 (https://rosshouserentals.com)",
    )[:240]


def _cache_minutes() -> int:
    try:
        value = int(os.getenv("CLIMATE_WEATHER_CACHE_MINUTES", str(DEFAULT_CACHE_MINUTES)))
    except ValueError:
        value = DEFAULT_CACHE_MINUTES
    return max(5, min(value, 60))


def _as_utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _fahrenheit(value, unit_code: str | None = None):
    if not _finite(value):
        return None
    code = str(unit_code or "")
    if "degC" in code:
        return value * 9 / 5 + 32
    if "degF" in code:
        return value
    return value


def _mph(value, unit_code: str | None = None):
    if not _finite(value):
        return None
    code = str(unit_code or "")
    if "km_h-1" in code or "km/h" in code:
        return value * 0.621371
    if "m_s-1" in code or "m/s" in code:
        return value * 2.23694
    return value


def _measure(properties: dict, key: str, converter=None):
    item = properties.get(key)
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    if not _finite(value):
        return None
    if converter:
        value = converter(value, item.get("unitCode"))
    return round(value, 2)


def _safe_nws_url(value: str | None) -> str:
    if not value:
        raise HTTPException(503, "weather_endpoint_unavailable")
    parsed = urlparse(str(value))
    if parsed.scheme != "https" or parsed.netloc.lower() != "api.weather.gov":
        raise HTTPException(503, "weather_endpoint_invalid")
    return str(value)


async def _nws_json(url: str):
    url = _safe_nws_url(url)
    headers = {
        "User-Agent": _nws_user_agent(),
        "Accept": "application/geo+json, application/ld+json, application/json",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=False, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _census_geocode(address: str):
    params = {
        "address": address[:100],
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
        response = await client.get(CENSUS_GEOCODER, params=params)
        response.raise_for_status()
        payload = response.json()
    matches = (((payload or {}).get("result") or {}).get("addressMatches") or [])
    if not matches:
        return None
    match = matches[0]
    coords = match.get("coordinates") or {}
    lat, lon = coords.get("y"), coords.get("x")
    if not (_finite(lat) and _finite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
        "matched_address": match.get("matchedAddress"),
        "source": "us_census",
    }


async def ensure_indexes(db):
    await db.weather_locations.create_index([("property_id", 1)], unique=True)
    await db.weather_cache.create_index([("expires_at", 1)])
    await db.weather_cache.create_index([("property_id", 1), ("updated_at", -1)])


async def _property(db, property_id: str):
    return await db.properties.find_one(
        {"_id": {"$in": _property_ids(str(property_id))}},
        {
            "address": 1,
            "city": 1,
            "state": 1,
            "zip": 1,
            "zip_code": 1,
            "latitude": 1,
            "longitude": 1,
        },
    )


def _property_address(doc: dict | None):
    if not doc:
        return ""
    values = [
        doc.get("address"),
        doc.get("city"),
        doc.get("state"),
        doc.get("zip") or doc.get("zip_code"),
    ]
    return ", ".join(str(v).strip() for v in values if v and str(v).strip())


async def set_manual_location(db, property_id: str, latitude: float, longitude: float, actor: str):
    if not (_finite(latitude) and _finite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(422, "weather_location_invalid")
    doc = {
        "_id": str(property_id),
        "property_id": str(property_id),
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "source": "manual",
        "updated_by": actor,
        "updated_at": now(),
    }
    await db.weather_locations.update_one(
        {"_id": str(property_id)},
        {"$set": doc, "$setOnInsert": {"created_at": now()}},
        upsert=True,
    )
    await db.weather_cache.delete_many({"property_id": str(property_id)})
    return public_location(doc)


def public_location(doc: dict | None):
    if not doc:
        return None
    return {
        "property_id": str(doc.get("property_id") or doc.get("_id") or ""),
        "latitude": doc.get("latitude"),
        "longitude": doc.get("longitude"),
        "source": doc.get("source"),
        "matched_address": doc.get("matched_address"),
        "nws_office": ((doc.get("nws") or {}).get("office")),
        "nws_grid": ((doc.get("nws") or {}).get("grid")),
    }


async def location_for_property(db, property_id: str, force: bool = False):
    property_id = str(property_id)
    existing = await db.weather_locations.find_one({"_id": property_id})
    if existing and _finite(existing.get("latitude")) and _finite(existing.get("longitude")):
        if existing.get("source") == "manual" or not force:
            return existing

    prop = await _property(db, property_id)
    if prop and _finite(prop.get("latitude")) and _finite(prop.get("longitude")):
        doc = {
            "_id": property_id,
            "property_id": property_id,
            "latitude": float(prop["latitude"]),
            "longitude": float(prop["longitude"]),
            "source": "property",
            "updated_at": now(),
        }
    else:
        address = _property_address(prop)
        if not address:
            return existing
        try:
            geocoded = await _census_geocode(address)
        except (httpx.HTTPError, ValueError, TypeError):
            return existing
        if not geocoded:
            return existing
        doc = {
            "_id": property_id,
            "property_id": property_id,
            **geocoded,
            "input_address": address,
            "updated_at": now(),
        }

    await db.weather_locations.update_one(
        {"_id": property_id},
        {"$set": doc, "$setOnInsert": {"created_at": now()}},
        upsert=True,
    )
    return await db.weather_locations.find_one({"_id": property_id})


async def _point_metadata(db, location: dict, force: bool = False):
    nws = location.get("nws") or {}
    checked = _as_utc(nws.get("checked_at"))
    if (
        not force
        and checked
        and checked > now() - timedelta(days=POINT_REFRESH_DAYS)
        and nws.get("forecast")
        and nws.get("forecast_hourly")
        and nws.get("observation_stations")
    ):
        return nws

    lat, lon = location.get("latitude"), location.get("longitude")
    payload = await _nws_json(f"{NWS_BASE}/points/{lat:.4f},{lon:.4f}")
    props = payload.get("properties") or {}
    office = props.get("gridId")
    grid_x, grid_y = props.get("gridX"), props.get("gridY")
    metadata = {
        "office": office,
        "grid": f"{office}/{grid_x},{grid_y}" if office is not None and grid_x is not None and grid_y is not None else None,
        "forecast": _safe_nws_url(props.get("forecast")),
        "forecast_hourly": _safe_nws_url(props.get("forecastHourly")),
        "forecast_grid_data": props.get("forecastGridData"),
        "observation_stations": _safe_nws_url(props.get("observationStations")),
        "forecast_zone": props.get("forecastZone"),
        "county": props.get("county"),
        "time_zone": props.get("timeZone"),
        "checked_at": now(),
    }
    await db.weather_locations.update_one(
        {"_id": str(location["_id"])},
        {"$set": {"nws": metadata, "updated_at": now()}},
    )
    return metadata


async def _latest_observation(stations_url: str):
    station_payload = await _nws_json(stations_url)
    features = station_payload.get("features") or []
    if not features:
        return None
    station = features[0]
    station_url = station.get("id") or station.get("@id")
    if not station_url:
        station_url = (station.get("properties") or {}).get("@id")
    if not station_url:
        return None
    station_url = _safe_nws_url(station_url)
    payload = await _nws_json(station_url.rstrip("/") + "/observations/latest")
    props = payload.get("properties") or {}
    return {
        "station": (station.get("properties") or {}).get("stationIdentifier"),
        "station_name": (station.get("properties") or {}).get("name"),
        "observed_at": props.get("timestamp"),
        "temperature_f": _measure(props, "temperature", _fahrenheit),
        "humidity": _measure(props, "relativeHumidity"),
        "dewpoint_f": _measure(props, "dewpoint", _fahrenheit),
        "wind_speed_mph": _measure(props, "windSpeed", _mph),
        "wind_gust_mph": _measure(props, "windGust", _mph),
        "wind_direction_degrees": _measure(props, "windDirection"),
        "wind_chill_f": _measure(props, "windChill", _fahrenheit),
        "heat_index_f": _measure(props, "heatIndex", _fahrenheit),
        "description": props.get("textDescription"),
    }


def _forecast_temp_f(period: dict):
    value = period.get("temperature")
    if not _finite(value):
        return None
    unit = str(period.get("temperatureUnit") or "F").upper()
    return round(value * 9 / 5 + 32, 1) if unit.startswith("C") else round(float(value), 1)


def _forecast_period(period: dict):
    rh = period.get("relativeHumidity") or {}
    pop = period.get("probabilityOfPrecipitation") or {}
    return {
        "number": period.get("number"),
        "name": period.get("name"),
        "start": period.get("startTime"),
        "end": period.get("endTime"),
        "is_daytime": period.get("isDaytime"),
        "temperature_f": _forecast_temp_f(period),
        "temperature_trend": period.get("temperatureTrend"),
        "humidity": rh.get("value") if _finite(rh.get("value")) else None,
        "precipitation_probability": pop.get("value") if _finite(pop.get("value")) else None,
        "wind_speed": period.get("windSpeed"),
        "wind_direction": period.get("windDirection"),
        "short_forecast": period.get("shortForecast"),
        "detailed_forecast": period.get("detailedForecast"),
        "icon": period.get("icon"),
    }


def _public_alert(feature: dict):
    props = feature.get("properties") or {}
    def clipped(value, limit=900):
        text = str(value or "").strip()
        return text[:limit] if text else None
    return {
        "id": props.get("id") or feature.get("id"),
        "event": props.get("event"),
        "severity": props.get("severity"),
        "certainty": props.get("certainty"),
        "urgency": props.get("urgency"),
        "headline": clipped(props.get("headline"), 400),
        "description": clipped(props.get("description")),
        "instruction": clipped(props.get("instruction")),
        "effective": props.get("effective"),
        "onset": props.get("onset"),
        "expires": props.get("expires"),
        "sender": props.get("senderName"),
    }


async def weather_for_property(db, property_id: str, force: bool = False):
    if not enabled():
        return {"enabled": False, "status": "disabled", "source": "NWS"}

    property_id = str(property_id)
    cached = await db.weather_cache.find_one({"_id": property_id})
    expires = _as_utc((cached or {}).get("expires_at"))
    if cached and not force and expires and expires > now():
        return cached.get("payload") or {}

    location = await location_for_property(db, property_id, force=False)
    if not location:
        return {
            "enabled": True,
            "status": "location_required",
            "source": "NWS",
            "property_id": property_id,
        }

    try:
        meta = await _point_metadata(db, location, force=False)
        hourly_payload, daily_payload, alerts_payload = await _parallel_weather_requests(
            meta["forecast_hourly"],
            meta["forecast"],
            f"{NWS_BASE}/alerts/active?point={location['latitude']:.4f},{location['longitude']:.4f}",
        )
        try:
            observation = await _latest_observation(meta["observation_stations"])
        except (httpx.HTTPError, HTTPException, ValueError, TypeError):
            observation = None
    except (httpx.HTTPError, HTTPException, ValueError, TypeError):
        stale = (cached or {}).get("payload")
        if stale:
            return {**stale, "stale": True}
        return {
            "enabled": True,
            "status": "unavailable",
            "source": "NWS",
            "property_id": property_id,
            "location": public_location(location),
        }

    hourly = [_forecast_period(p) for p in ((hourly_payload.get("properties") or {}).get("periods") or [])[:48]]
    daily = [_forecast_period(p) for p in ((daily_payload.get("properties") or {}).get("periods") or [])[:14]]
    alerts = [_public_alert(f) for f in (alerts_payload.get("features") or [])[:25]]

    if not observation and hourly:
        observation = {
            "station": None,
            "station_name": None,
            "observed_at": hourly[0].get("start"),
            "temperature_f": hourly[0].get("temperature_f"),
            "humidity": hourly[0].get("humidity"),
            "wind_speed_mph": None,
            "wind_gust_mph": None,
            "wind_direction_degrees": None,
            "description": hourly[0].get("short_forecast"),
            "forecast_fallback": True,
        }

    payload = {
        "enabled": True,
        "status": "ok",
        "source": "NWS",
        "provider": "National Weather Service",
        "property_id": property_id,
        "location": public_location({**location, "nws": meta}),
        "current": observation,
        "hourly": hourly[:24],
        "daily": daily,
        "alerts": alerts,
        "updated_at": now().isoformat(),
        "stale": False,
    }
    payload["advice"] = weather_advice({}, payload)

    await db.weather_cache.update_one(
        {"_id": property_id},
        {"$set": {
            "property_id": property_id,
            "payload": payload,
            "updated_at": now(),
            "expires_at": now() + timedelta(minutes=_cache_minutes()),
        }},
        upsert=True,
    )
    return payload


async def _parallel_weather_requests(hourly_url: str, daily_url: str, alerts_url: str):
    import asyncio
    return await asyncio.gather(
        _nws_json(hourly_url),
        _nws_json(daily_url),
        _nws_json(alerts_url),
    )


async def weather_for_binding(db, binding: dict, force: bool = False):
    return await weather_for_property(db, str(binding.get("property_id") or ""), force=force)


def _indoor_f(state: dict):
    value = state.get("temperature")
    if not _finite(value):
        return None
    return value if state.get("units") == "Fahrenheit" else value * 9 / 5 + 32


def _forecast_temperatures(weather: dict, hours: int = 12):
    values = []
    for row in (weather.get("hourly") or [])[:hours]:
        value = row.get("temperature_f")
        if _finite(value):
            values.append(value)
    return values


def alert_conditions(state: dict, weather: dict | None):
    result = {}
    if not weather or weather.get("status") != "ok":
        return result
    current = weather.get("current") or {}
    outdoor = current.get("temperature_f")
    indoor = _indoor_f(state)
    activity = state.get("activity")
    forecast = _forecast_temperatures(weather, 12)
    severe = any(
        str(item.get("severity") or "").lower() in ("severe", "extreme")
        for item in weather.get("alerts") or []
    )

    result["cooling_in_cold_weather"] = (
        _finite(outdoor) and outdoor <= 45 and activity == "cooling",
        "warning",
        "Cooling is active while the official outdoor temperature is unusually cold.",
    )
    result["heating_in_hot_weather"] = (
        _finite(outdoor) and outdoor >= 80 and activity == "heating",
        "warning",
        "Heating is active while the official outdoor temperature is unusually warm.",
    )
    result["forecast_freeze_risk"] = (
        bool(forecast) and min(forecast) <= 28 and _finite(indoor) and indoor <= 60,
        "critical" if _finite(indoor) and indoor <= 55 else "warning",
        "Freezing weather is forecast and indoor temperature is close to the property-protection range.",
    )
    result["nws_severe_weather"] = (
        severe,
        "warning",
        "The National Weather Service has an active severe or extreme alert for this property.",
    )
    return result


def comfort_setpoint_suggestion(state: dict, weather: dict | None):
    """Return general, advisory-only comfort guidance from official weather context.

    The range is intentionally broad and never becomes a thermostat command.
    Historical/property-specific optimization can refine this later.
    """
    if not weather or weather.get("status") != "ok":
        return None
    current = weather.get("current") or {}
    forecast = _forecast_temperatures(weather, 12)
    outdoor = current.get("temperature_f")
    low = min(forecast) if forecast else outdoor
    high = max(forecast) if forecast else outdoor
    if not (_finite(low) or _finite(high)):
        return None

    if _finite(low) and low <= 32:
        return {
            "type": "comfort_setpoint_suggestion",
            "severity": "info",
            "suggested_mode": "Heat",
            "range_f": [68, 70],
            "automatic": False,
            "basis": "cold_forecast",
            "title_es": "Rango sugerido por clima",
            "title_en": "Weather-based comfort range",
            "body_es": "Como punto de partida general, considera Heat alrededor de 68–70°F mientras vigilas el confort y la respuesta de esta vivienda. Ross House no cambiará el termostato automáticamente.",
            "body_en": "As a general starting point, consider Heat around 68–70°F while monitoring comfort and this home's response. Ross House will not change the thermostat automatically.",
        }

    if _finite(high) and high >= 95:
        return {
            "type": "comfort_setpoint_suggestion",
            "severity": "info",
            "suggested_mode": "Cool",
            "range_f": [74, 76],
            "automatic": False,
            "basis": "extreme_heat_forecast",
            "title_es": "Rango sugerido por clima",
            "title_en": "Weather-based comfort range",
            "body_es": "Como punto de partida general durante calor fuerte, considera Cool alrededor de 74–76°F y ajusta según confort. Ross House no cambiará el termostato automáticamente.",
            "body_en": "As a general starting point during high heat, consider Cool around 74–76°F and adjust for comfort. Ross House will not change the thermostat automatically.",
        }

    if _finite(high) and high >= 85:
        return {
            "type": "comfort_setpoint_suggestion",
            "severity": "info",
            "suggested_mode": "Cool",
            "range_f": [74, 78],
            "automatic": False,
            "basis": "warm_forecast",
            "title_es": "Rango sugerido por clima",
            "title_en": "Weather-based comfort range",
            "body_es": "Con clima cálido, un rango general de 74–78°F en Cool puede servir como punto de partida. Ajusta según confort; Ross House no hará cambios automáticamente.",
            "body_en": "In warm weather, a general 74–78°F Cool range can be a starting point. Adjust for comfort; Ross House will not make changes automatically.",
        }

    if _finite(low) and _finite(high) and low >= 55 and high <= 78:
        return {
            "type": "comfort_setpoint_suggestion",
            "severity": "info",
            "suggested_mode": "Auto",
            "range_f": [68, 76],
            "automatic": False,
            "basis": "mild_forecast",
            "title_es": "Clima exterior moderado",
            "title_en": "Mild outdoor weather",
            "body_es": "Con condiciones moderadas, Auto con una banda amplia cercana a 68–76°F puede reducir cambios innecesarios de modo. Ajusta según confort y humedad interior.",
            "body_en": "With mild conditions, Auto with a broad band near 68–76°F can reduce unnecessary mode changes. Adjust for comfort and indoor humidity.",
        }
    return None


def weather_advice(state: dict, weather: dict | None):
    if not weather or weather.get("status") != "ok":
        return []
    current = weather.get("current") or {}
    outdoor = current.get("temperature_f")
    forecast = _forecast_temperatures(weather, 12)
    indoor = _indoor_f(state)
    activity = state.get("activity")
    advice = []

    comfort = comfort_setpoint_suggestion(state, weather)
    if comfort:
        advice.append(comfort)

    severe_alerts = [
        item for item in weather.get("alerts") or []
        if str(item.get("severity") or "").lower() in ("severe", "extreme")
    ]
    if severe_alerts:
        event = severe_alerts[0].get("event") or "NWS alert"
        advice.append({
            "type": "official_weather_alert",
            "severity": "warning",
            "title_es": "Alerta meteorológica oficial",
            "title_en": "Official weather alert",
            "body_es": f"{event}. Revisa la alerta del National Weather Service para esta propiedad.",
            "body_en": f"{event}. Review the National Weather Service alert for this property.",
        })

    if _finite(outdoor) and outdoor <= 45 and activity == "cooling":
        advice.append({
            "type": "cooling_cold_outdoor",
            "severity": "warning",
            "title_es": "Revisa el aire acondicionado",
            "title_en": "Review cooling operation",
            "body_es": f"El enfriamiento está activo con aproximadamente {outdoor:.0f}°F afuera. Verifica el modo y el setpoint; Ross House no apagará el equipo automáticamente.",
            "body_en": f"Cooling is active with about {outdoor:.0f}°F outside. Review mode and setpoint; Ross House will not shut equipment off automatically.",
        })

    if forecast and min(forecast) <= 28:
        advice.append({
            "type": "freeze_forecast",
            "severity": "warning",
            "title_es": "Protección contra congelación",
            "title_en": "Freeze protection",
            "body_es": f"El pronóstico de las próximas 12 horas baja hasta aproximadamente {min(forecast):.0f}°F. Mantén vigilancia de la temperatura interior y del sistema de calefacción.",
            "body_en": f"The next 12-hour forecast falls to about {min(forecast):.0f}°F. Keep an eye on indoor temperature and heating operation.",
        })

    if forecast and max(forecast) >= 95:
        advice.append({
            "type": "extreme_heat_forecast",
            "severity": "info",
            "title_es": "Calor fuerte previsto",
            "title_en": "High heat forecast",
            "body_es": f"El pronóstico sube hasta aproximadamente {max(forecast):.0f}°F. Ross House puede usar el historial de esta vivienda para anticipar la carga de enfriamiento.",
            "body_en": f"The forecast rises to about {max(forecast):.0f}°F. Ross House can use this home's history to anticipate cooling load.",
        })

    if len(forecast) >= 6 and _finite(outdoor):
        future = forecast[min(5, len(forecast)-1)]
        change = future - outdoor
        if change <= -12:
            advice.append({
                "type": "rapid_cooling_outdoors",
                "severity": "info",
                "title_es": "Descenso exterior previsto",
                "title_en": "Outdoor cooldown expected",
                "body_es": f"La temperatura exterior puede bajar alrededor de {abs(change):.0f}°F en las próximas horas. El Schedule puede adelantarse cuando tengamos suficiente historial de rendimiento.",
                "body_en": f"Outdoor temperature may fall about {abs(change):.0f}°F over the next several hours. Scheduling can pre-start once enough performance history is available.",
            })
        elif change >= 12:
            advice.append({
                "type": "rapid_warming_outdoors",
                "severity": "info",
                "title_es": "Aumento exterior previsto",
                "title_en": "Outdoor warmup expected",
                "body_es": f"La temperatura exterior puede subir alrededor de {change:.0f}°F en las próximas horas. Ross House puede anticipar la carga de enfriamiento usando el historial de esta vivienda.",
                "body_en": f"Outdoor temperature may rise about {change:.0f}°F over the next several hours. Ross House can anticipate cooling load using this home's history.",
            })

    if not advice and _finite(outdoor):
        body_es = f"Condiciones exteriores alrededor de {outdoor:.0f}°F"
        body_en = f"Outdoor conditions are around {outdoor:.0f}°F"
        if _finite(indoor):
            body_es += f" y el interior está cerca de {indoor:.0f}°F."
            body_en += f" and indoors is near {indoor:.0f}°F."
        else:
            body_es += "."
            body_en += "."
        advice.append({
            "type": "weather_context",
            "severity": "info",
            "title_es": "Contexto meteorológico",
            "title_en": "Weather context",
            "body_es": body_es,
            "body_en": body_en,
        })
    return advice[:6]


def reading_context(state: dict, weather: dict | None):
    if not weather or weather.get("status") != "ok":
        return {}
    current = weather.get("current") or {}
    hourly = weather.get("hourly") or []
    forecast = [row.get("temperature_f") for row in hourly[:12] if _finite(row.get("temperature_f"))]
    return {
        "nws_temperature": current.get("temperature_f"),
        "nws_humidity": current.get("humidity"),
        "nws_wind_speed_mph": current.get("wind_speed_mph"),
        "nws_wind_gust_mph": current.get("wind_gust_mph"),
        "nws_condition": current.get("description"),
        "nws_observed_at": current.get("observed_at"),
        "nws_station": current.get("station"),
        "nws_alert_count": len(weather.get("alerts") or []),
        "nws_forecast_min_12h": min(forecast) if forecast else None,
        "nws_forecast_max_12h": max(forecast) if forecast else None,
        "weather_advice": weather_advice(state, weather),
    }
