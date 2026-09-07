"""Deterministic, reviewable evidence matching for acquisition opportunities."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit


_NAME_NOISE = {
    "JR", "SR", "II", "III", "IV", "THE", "OF", "ESTATE", "TRUST",
    "REVOCABLE", "LIVING", "ET", "AL",
}
_ENTITY_MARKERS = {"LLC", "INC", "CORP", "LTD", "LP", "COMPANY", "BANK", "CHURCH"}
_STREET_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "DRIVE": "DR", "LANE": "LN",
    "ROAD": "RD", "BOULEVARD": "BLVD", "COURT": "CT", "PLACE": "PL",
    "HIGHWAY": "HWY", "CIRCLE": "CIR", "TRAIL": "TRL", "PARKWAY": "PKWY",
}
_ADDRESS_NOISE = {"ST", "AVE", "DR", "LN", "RD", "BLVD", "CT", "PL", "HWY",
                  "CIR", "TRL", "PKWY", "N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_OBITUARY_HOSTS = {"echovita.com", "morrisonfuneraldirectors.com"}


def _ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").upper()
    return re.findall(r"[A-Z0-9]+", ascii_value)


def person_tokens(value: str) -> list[str]:
    return [token for token in _ascii_words(value)
            if len(token) > 1 and token not in _NAME_NOISE]


def match_person(record_name: str, owner_name: str) -> dict:
    record = person_tokens(record_name)
    owner_raw = _ascii_words(owner_name)
    owner = [token for token in owner_raw if len(token) > 1 and token not in _NAME_NOISE]
    if any(marker in owner_raw for marker in _ENTITY_MARKERS):
        return {"matched": False, "confidence": 0, "reasons": ["entity_owner"]}
    if len(record) < 2 or len(owner) < 2:
        return {"matched": False, "confidence": 0, "reasons": ["insufficient_name_tokens"]}
    record_set, owner_set = set(record), set(owner)
    anchors = {record[0], record[-1]}
    shared = record_set & owner_set
    if len(anchors) < 2 or not anchors.issubset(owner_set) or len(shared) < 2:
        return {"matched": False, "confidence": 0, "reasons": ["name_anchors_mismatch"]}
    exact = record_set == owner_set
    confidence = 100 if exact else 92
    return {"matched": True, "confidence": confidence,
            "reasons": ["exact_name_tokens" if exact else "first_last_tokens"]}


def _normalized_address(value: str) -> list[str]:
    words = _ascii_words(value)
    return [_STREET_SUFFIX.get(word, word) for word in words]


def address_probe(value: str) -> tuple[str, str] | None:
    words = _normalized_address(value)
    if not words or not words[0].isdigit():
        return None
    core = [word for word in words[1:] if word not in _ADDRESS_NOISE and not word.isdigit()]
    return (words[0], core[0]) if core else None


def match_address(record_address: str, lead_address: str) -> dict:
    record = _normalized_address(record_address)
    lead = _normalized_address(lead_address)
    if not record or not lead or not record[0].isdigit() or record[0] != lead[0]:
        return {"matched": False, "confidence": 0, "reasons": ["house_number_mismatch"]}
    record_core = {w for w in record[1:] if w not in _ADDRESS_NOISE and not w.isdigit()}
    lead_core = {w for w in lead[1:] if w not in _ADDRESS_NOISE and not w.isdigit()}
    shared = record_core & lead_core
    if not shared:
        return {"matched": False, "confidence": 0, "reasons": ["street_tokens_mismatch"]}
    return {"matched": True, "confidence": 98,
            "reasons": ["house_number", "street_token"]}


def obituary_source_allowed(url: str) -> bool:
    try:
        parsed = urlsplit(str(url or ""))
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    trusted = any(host == allowed or host.endswith("." + allowed)
                  for allowed in _OBITUARY_HOSTS)
    return (parsed.scheme == "https" and trusted and not parsed.username
            and not parsed.password and port in (None, 443))


def evidence_id(source: str, record: dict) -> str:
    primary = record.get("case_number") or ""
    if not primary:
        primary = "|".join(str(record.get(k) or "")
                           for k in ("name", "address", "date", "city"))
    canonical = json.dumps({"source": source, "primary": primary},
                           sort_keys=True, ensure_ascii=True).upper()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def evidence_detail(source: str, record: dict, match: dict, *, source_url: str = "",
                    now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("opportunity_evidence_clock_must_be_aware")
    clean = {key: str(record.get(key) or "")[:300]
             for key in ("name", "address", "case_number", "date", "city")}
    return {
        "evidence_id": evidence_id(source, clean),
        "source": source,
        "record_name": clean["name"],
        "record_address": clean["address"],
        "case_number": clean["case_number"],
        "date": clean["date"],
        "city": clean["city"],
        "source_url": source_url if obituary_source_allowed(source_url) else "",
        "confidence": int(match["confidence"]),
        "match_reasons": list(match["reasons"]),
        "review_status": "needs_review",
        "at": current.astimezone(timezone.utc).isoformat(),
    }


async def add_evidence_atomic(db, lead_id, signal: str | list[str], detail: dict) -> bool:
    """Add one evidence fact once; concurrent writers cannot lose other facts."""
    eid = detail.get("evidence_id")
    if not eid:
        raise ValueError("opportunity_evidence_id_required")
    signals = [signal] if isinstance(signal, str) else list(dict.fromkeys(signal))
    if not signals:
        raise ValueError("opportunity_evidence_signal_required")
    result = await db.deal_finder_leads.update_one(
        {"_id": lead_id, "motivation.details.evidence_id": {"$ne": eid}},
        [
            {"$set": {"motivation": {"$cond": [
                {"$eq": [{"$type": "$motivation"}, "object"]}, "$motivation", {},
            ]}}},
            {"$set": {
                "motivation.signals": {"$setUnion": [
                    {"$cond": [{"$isArray": "$motivation.signals"},
                               "$motivation.signals", []]}, signals,
                ]},
                "motivation.details": {"$concatArrays": [
                    {"$cond": [{"$isArray": "$motivation.details"},
                               "$motivation.details", []]}, [detail],
                ]},
                "motivation.updated_at": detail["at"],
            }},
        ],
    )
    return result.modified_count == 1
