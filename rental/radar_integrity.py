"""Pure integrity and privacy rules for Ross House opportunity radar."""
from __future__ import annotations

import hashlib
import re
import unicodedata


ROSS_HOUSE_SOURCE = "Ross House Rentals LLC"
_SOURCE_KEYS = frozenset({"ross house rentals", "ross house rentals llc", "ross_house_rentals"})


def require_ross_house_source(value: object) -> str:
    if value is None or value == "":
        return ROSS_HOUSE_SOURCE
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("radar_source_business_invalid")
    key = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if key not in _SOURCE_KEYS:
        raise ValueError("radar_foreign_business_source_prohibited")
    return ROSS_HOUSE_SOURCE


def safe_search_pattern(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("radar_search_invalid")
    query = value.strip()
    if not query or len(query) > 80 or any(unicodedata.category(c).startswith("C") for c in query):
        raise ValueError("radar_search_invalid")
    return re.escape(query)


def serialize_radar_client(client: dict) -> dict:
    result = dict(client)
    result["_id"] = str(result["_id"])
    for field in ("a_number", "passport"):
        value = str(result.pop(field, "") or "")
        result[f"{field}_masked"] = "••••" + value[-4:] if len(value) > 4 else ("SET" if value else "")
    return result


def deterministic_lead_id(client_id: object) -> str:
    value = str(client_id or "")
    if not value or len(value) > 128 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("radar_client_id_invalid")
    return "radar-lead-" + hashlib.sha256(value.encode()).hexdigest()
