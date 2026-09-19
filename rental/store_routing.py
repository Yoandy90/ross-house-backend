"""Server-side Google Routes adapter for live store delivery.

No API key or provider payload is exposed to clients. Failures are best-effort:
live GPS tracking remains available without road geometry.
"""
import os
from datetime import datetime, timezone
import httpx

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TIMEOUT_SECONDS = 4.0
MAX_POINTS = 2000


def _point(value):
    if not isinstance(value, dict):
        return None
    try:
        lat, lng = float(value["latitude"]), float(value["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"latitude": lat, "longitude": lng}


def _decode_polyline(encoded):
    points, index, lat, lng = [], 0, 0, 0
    try:
        while index < len(encoded) and len(points) < MAX_POINTS:
            values = []
            for _ in range(2):
                result = shift = 0
                while True:
                    byte = ord(encoded[index]) - 63
                    index += 1
                    result |= (byte & 0x1F) << shift
                    shift += 5
                    if byte < 0x20:
                        break
                values.append(~(result >> 1) if result & 1 else result >> 1)
            lat += values[0]
            lng += values[1]
            points.append({"latitude": lat / 1e5, "longitude": lng / 1e5})
    except (IndexError, TypeError, ValueError):
        return []
    return points


def _seconds(value):
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        seconds = float(value[:-1])
    except ValueError:
        return None
    return round(seconds) if seconds >= 0 else None


async def road_route(origin, destination):
    key = os.getenv("GOOGLE_ROUTES_API_KEY", "").strip()
    origin, destination = _point(origin), _point(destination)
    if not key or not origin or not destination:
        return None
    body = {
        "origin": {"location": {"latLng": {"latitude": origin["latitude"], "longitude": origin["longitude"]}}},
        "destination": {"location": {"latLng": {"latitude": destination["latitude"], "longitude": destination["longitude"]}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "polylineQuality": "HIGH_QUALITY",
        "polylineEncoding": "ENCODED_POLYLINE",
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "IMPERIAL",
    }
    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(ROUTES_URL, json=body, headers=headers)
        if response.status_code != 200:
            return None
        routes = response.json().get("routes") or []
        if not routes:
            return None
        first = routes[0]
        coordinates = _decode_polyline((first.get("polyline") or {}).get("encodedPolyline", ""))
        distance = first.get("distanceMeters")
        duration = _seconds(first.get("duration"))
        if len(coordinates) < 2 or not isinstance(distance, int) or distance < 0 or duration is None:
            return None
        return {
            "coordinates": coordinates,
            "distance_meters": distance,
            "duration_seconds": duration,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }
    except (httpx.HTTPError, ValueError, TypeError):
        return None
