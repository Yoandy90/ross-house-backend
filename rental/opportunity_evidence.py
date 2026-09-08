"""Deterministic, reviewable evidence matching for acquisition opportunities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import unicodedata
from urllib.parse import urlsplit

from pymongo import ReturnDocument


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
_REVIEW_STATUSES = {"needs_review", "confirmed", "dismissed"}
_LEGACY_SOURCE_BY_SIGNAL = {
    "possible_deceased": ["obituary"],
    "probate_confirmed": ["probate"],
    "eviction_filed": ["eviction"],
    "divorce_filed": ["divorce"],
    "tax_sale": ["tax_sale"],
    "code_violation": ["code_violation"],
    "vacant": ["vacancy"],
}


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


def _city_key(value: str) -> str:
    noise = {"TX", "TEXAS", "USA", "US"}
    return " ".join(word for word in _ascii_words(value) if word not in noise)


def _lead_city_keys(lead: dict) -> set[str]:
    cities = {_city_key(lead.get("mailing_city") or "")}
    address = str(lead.get("address") or "")
    match = re.search(r",\s*([^,]+?)(?:\s+(?:TX|TEXAS)\b|\s+\d{5}(?:-\d{4})?\b|$)",
                      address, re.IGNORECASE)
    if match:
        cities.add(_city_key(match.group(1)))
    return {city for city in cities if city}


def match_obituary(record: dict, lead: dict) -> dict:
    """Require geographic corroboration when an obituary supplies a city."""
    matched = match_person(str(record.get("name") or ""),
                           str(lead.get("owner_name") or ""))
    if not matched["matched"]:
        return matched
    obituary_city = _city_key(record.get("city") or "")
    lead_cities = _lead_city_keys(lead)
    if obituary_city and lead_cities and obituary_city not in lead_cities:
        return {"matched": False, "confidence": 0,
                "reasons": [*matched["reasons"], "city_mismatch"]}
    if obituary_city and obituary_city in lead_cities:
        return {"matched": True, "confidence": min(100, matched["confidence"] + 5),
                "reasons": [*matched["reasons"], "city_match"]}
    return {"matched": True, "confidence": max(0, matched["confidence"] - 10),
            "reasons": [*matched["reasons"], "city_unverified"]}


def obituary_owner_identity(lead: dict) -> str:
    """Group a portfolio by normalized owner and mailing destination."""
    suffixes = {"JR", "SR", "II", "III", "IV"}
    owner = " ".join(sorted(set(
        word for word in _ascii_words(lead.get("owner_name") or "")
        if len(word) > 1 and (word not in _NAME_NOISE or word in suffixes)
    )))
    mailing = " ".join(_ascii_words(" ".join([
        *[str(line) for line in (lead.get("mailing_lines") or []) if isinstance(line, str)],
        str(lead.get("mailing_city") or ""), str(lead.get("mailing_state") or ""),
        str(lead.get("mailing_zip") or ""),
    ])))
    return f"{owner}|{mailing}" if mailing else owner


def resolve_obituary_candidates(record: dict, candidates: list[dict]) -> dict:
    """Keep multi-property portfolios while quarantining distinct same-name owners."""
    verified = []
    for lead in candidates:
        match = match_obituary(record, lead)
        if match["matched"]:
            verified.append((lead, match))
    identities = {obituary_owner_identity(lead) for lead, _ in verified}
    ambiguous = len(identities) > 1
    return {"matches": [] if ambiguous else verified,
            "ambiguous": ambiguous, "candidate_count": len(verified),
            "candidate_leads": [lead for lead, _ in verified] if ambiguous else []}


def serialize_obituary_ambiguities(items: object) -> list[dict]:
    """Return a bounded public-admin view without claim or reviewer internals."""
    if not isinstance(items, list):
        return []
    output = []
    for raw in items[:100]:
        if not isinstance(raw, dict):
            continue
        obituary = raw.get("obituary")
        if (not isinstance(obituary, dict) or
                not isinstance(obituary.get("name"), str) or
                not obituary["name"].strip()):
            continue
        ambiguity_id = str(raw.get("ambiguity_id") or "")
        status = str(raw.get("status") or "pending")
        candidates = raw.get("candidates")
        safe_candidates = []
        if isinstance(candidates, list):
            for candidate in candidates[:50]:
                if not isinstance(candidate, dict):
                    continue
                lead_id = str(candidate.get("lead_id") or "")
                if re.fullmatch(r"[0-9a-f]{24}", lead_id):
                    safe_candidates.append({
                        "lead_id": lead_id,
                        "address": str(candidate.get("address") or "")[:200],
                        "owner_name": str(candidate.get("owner_name") or "")[:200],
                    })
        candidate_count = raw.get("candidate_count")
        age = obituary.get("age")
        safe_obituary = {
            "name": obituary["name"].strip()[:200],
            "age": age if type(age) is int and 0 <= age <= 130 else None,
            "city": str(obituary.get("city") or "")[:200]
                    if isinstance(obituary.get("city"), str) else "",
            "date": str(obituary.get("date") or "")[:50]
                    if isinstance(obituary.get("date"), str) else "",
            "source_url": (str(obituary.get("source_url"))[:300]
                           if obituary_source_allowed(obituary.get("source_url")) else ""),
        }
        addresses = raw.get("candidate_addresses")
        if not isinstance(addresses, list):
            addresses = []
        result = {
            "ambiguity_id": ambiguity_id if re.fullmatch(r"[0-9a-f]{24}", ambiguity_id) else "",
            "status": status if status in {"pending", "resolving", "resolved"} else "pending",
            "obituary": safe_obituary,
            "candidate_count": max(0, candidate_count) if type(candidate_count) is int else 0,
            "candidates": safe_candidates,
            "candidate_addresses": [str(value)[:200] for value in addresses[:5]
                                    if isinstance(value, str)],
        }
        if result["status"] == "resolved":
            resolved_lead_id = str(raw.get("resolved_lead_id") or "")
            result["resolved_lead_id"] = (resolved_lead_id
                                           if re.fullmatch(r"[0-9a-f]{24}", resolved_lead_id)
                                           else "")
            result["resolved_at"] = str(raw.get("resolved_at") or "")[:50]
        output.append(result)
    return output


async def resolve_obituary_ambiguity_atomic(db, ambiguity_id: str, lead_id,
                                             reviewer_id: str, *,
                                             now: datetime | None = None) -> dict | None:
    """Claim one ambiguity, revalidate it, add evidence once and finalize it."""
    if not re.fullmatch(r"[0-9a-f]{24}", str(ambiguity_id or "")):
        raise ValueError("obituary_ambiguity_id_invalid")
    lead_id_text = str(lead_id)
    if not re.fullmatch(r"[0-9a-f]{24}", lead_id_text):
        raise ValueError("obituary_ambiguity_lead_id_invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("obituary_ambiguity_clock_must_be_aware")
    at = current.astimezone(timezone.utc).isoformat()
    stale_before = (current.astimezone(timezone.utc) - timedelta(minutes=15)).isoformat()
    claim_token = secrets.token_hex(12)
    claimable = [{"status": "pending"},
                 {"status": "resolving", "claimed_lead_id": lead_id_text,
                  "claimed_at": {"$lt": stale_before}}]
    claimed = await db.admin_config.find_one_and_update(
        {
            "type": "enrichment_config",
            "last_obituary_ambiguities": {"$elemMatch": {
                "ambiguity_id": ambiguity_id,
                "$or": claimable,
                "candidates": {"$elemMatch": {"lead_id": lead_id_text}},
            }},
        },
        {"$set": {
            "last_obituary_ambiguities.$[item].status": "resolving",
            "last_obituary_ambiguities.$[item].claim_token": claim_token,
            "last_obituary_ambiguities.$[item].claimed_at": at,
            "last_obituary_ambiguities.$[item].claimed_lead_id": lead_id_text,
        }},
        array_filters=[{"item.ambiguity_id": ambiguity_id, "$or": [
            {"item.status": "pending"},
            {"item.status": "resolving", "item.claimed_lead_id": lead_id_text,
             "item.claimed_at": {"$lt": stale_before}},
        ]}],
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return None
    ambiguity = next((item for item in claimed.get("last_obituary_ambiguities", [])
                      if isinstance(item, dict) and item.get("claim_token") == claim_token), None)
    if not ambiguity:
        await db.admin_config.update_one(
            {"type": "enrichment_config"},
            {"$set": {"last_obituary_ambiguities.$[item].status": "pending"},
             "$unset": {"last_obituary_ambiguities.$[item].claim_token": "",
                        "last_obituary_ambiguities.$[item].claimed_at": "",
                        "last_obituary_ambiguities.$[item].claimed_lead_id": ""}},
            array_filters=[{"item.ambiguity_id": ambiguity_id,
                            "item.claim_token": claim_token}],
        )
        return None
    evidence_added = False
    try:
        obituary = ambiguity.get("obituary")
        if not isinstance(obituary, dict):
            raise ValueError("obituary_ambiguity_record_invalid")
        lead = await db.deal_finder_leads.find_one({"_id": lead_id})
        if not lead:
            raise ValueError("obituary_ambiguity_lead_not_found")
        match = match_obituary(obituary, lead)
        if not match["matched"]:
            raise ValueError("obituary_ambiguity_candidate_no_longer_matches")
        manual_match = {**match,
                        "reasons": [*match["reasons"], "manual_identity_resolution"]}
        detail = evidence_detail(
            "obituary", obituary, manual_match,
            source_url=str(obituary.get("source_url") or ""), now=current)
        created = await add_evidence_atomic(
            db, lead_id, "possible_deceased", detail)
        evidence_added = True
        finalized = await db.admin_config.update_one(
            {"type": "enrichment_config"},
            {"$set": {
                "last_obituary_ambiguities.$[item].status": "resolved",
                "last_obituary_ambiguities.$[item].resolved_lead_id": lead_id_text,
                "last_obituary_ambiguities.$[item].resolved_at": at,
                "last_obituary_ambiguities.$[item].resolved_by": str(reviewer_id or "")[:100],
            }, "$unset": {
                "last_obituary_ambiguities.$[item].claim_token": "",
                "last_obituary_ambiguities.$[item].claimed_at": "",
                "last_obituary_ambiguities.$[item].claimed_lead_id": "",
            }},
            array_filters=[{"item.ambiguity_id": ambiguity_id,
                            "item.claim_token": claim_token}],
        )
        if finalized.modified_count != 1:
            raise RuntimeError("obituary_ambiguity_finalize_failed")
        return {"created": created, "lead_id": lead_id_text,
                "ambiguity_id": ambiguity_id, "evidence": detail}
    finally:
        # Release only before evidence is written. After that, leaving the claim
        # closed is safer than allowing a second owner to be selected.
        if not evidence_added:
            await db.admin_config.update_one(
                {"type": "enrichment_config"},
                {"$set": {"last_obituary_ambiguities.$[item].status": "pending"},
                 "$unset": {
                     "last_obituary_ambiguities.$[item].claim_token": "",
                     "last_obituary_ambiguities.$[item].claimed_at": "",
                     "last_obituary_ambiguities.$[item].claimed_lead_id": "",
                 }},
                array_filters=[{"item.ambiguity_id": ambiguity_id,
                                "item.claim_token": claim_token}],
            )


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


def parse_public_records_response(raw: str) -> list[dict]:
    """Distinguish valid empty extraction from an unusable model response."""
    text = raw.strip() if isinstance(raw, str) else ""
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text[:-3], flags=re.IGNORECASE).strip()
    try:
        records = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("public_records_response_invalid") from None
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError("public_records_response_invalid")
    # Reject malformed fields before any records can mutate leads.
    for record in records:
        for field in ("name", "address", "case_number", "date", "city"):
            if record.get(field) is not None and not isinstance(record[field], str):
                raise ValueError("public_records_response_invalid")
        if not any(str(record.get(field) or "").strip() for field in ("name", "address")):
            raise ValueError("public_records_response_invalid")
    return records[:200]


def parse_obituary_response(raw: str) -> list[dict]:
    """Validate obituary extraction before matching any owner names."""
    try:
        records = parse_public_records_response(raw)
    except ValueError:
        raise ValueError("obituary_response_invalid") from None
    for record in records:
        if not str(record.get("name") or "").strip():
            raise ValueError("obituary_response_invalid")
        age = record.get("age")
        if age is not None and (type(age) is not int or not 0 <= age <= 130):
            raise ValueError("obituary_response_invalid")
    return [{field: record.get(field) for field in ("name", "age", "city", "date")}
            for record in records[:50]]


def validate_radar_results(data: object) -> list[dict]:
    """Reject malformed batches before any lead can be updated."""
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("radar_response_invalid")
    results = data["results"]
    if len(results) > 200:
        raise ValueError("radar_response_invalid")
    for record in results:
        if not isinstance(record, dict):
            raise ValueError("radar_response_invalid")
        for field in ("Address", "City", "Owner"):
            if record.get(field) is not None and not isinstance(record[field], str):
                raise ValueError("radar_response_invalid")
    return results


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
    detail = {**detail, "signals": signals}
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


def serialize_evidence_review_history(evidence: dict) -> dict:
    """Return a bounded admin-safe timeline without reviewer identifiers."""
    history = []
    events = evidence.get("review_history")
    events = events if isinstance(events, list) else []
    for index, raw in enumerate(events):
        if (not isinstance(raw, dict) or not isinstance(raw.get("status"), str)
                or raw["status"] not in _REVIEW_STATUSES):
            continue
        at = str(raw.get("at") or "")[:50]
        try:
            instant = datetime.fromisoformat(at.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("timezone_required")
            instant = instant.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            instant = datetime.min.replace(tzinfo=timezone.utc)
            at = "unknown"
        else:
            at = instant.isoformat()
        history.append({
            "status": raw["status"],
            "at": at,
            "note": str(raw.get("note") or "").strip()[:500],
            "_index": index,
            "_instant": instant,
        })
    history.sort(key=lambda item: (item["_instant"], item["_index"]), reverse=True)
    for item in history:
        item.pop("_index", None)
        item.pop("_instant", None)
    return {
        "evidence_id": str(evidence.get("evidence_id") or "")[:50],
        "current_status": evidence.get("review_status")
        if evidence.get("review_status") in _REVIEW_STATUSES else "needs_review",
        "reviewed_at": str(evidence.get("reviewed_at") or "")[:50],
        "review_note": str(evidence.get("review_note") or "").strip()[:500],
        "history": history[:50],
    }


async def get_evidence_review_history(db, lead_id, evidence_id_value: str) -> dict | None:
    """Fetch one evidence timeline with a narrow projection and safe serialization."""
    if not re.fullmatch(r"[a-f0-9]{24}", str(evidence_id_value or "")):
        raise ValueError("opportunity_evidence_id_invalid")
    document = await db.deal_finder_leads.find_one(
        {"_id": lead_id, "motivation.details": {
            "$elemMatch": {"evidence_id": evidence_id_value}}},
        {"_id": 0, "motivation.details": {
            "$elemMatch": {"evidence_id": evidence_id_value}}},
    )
    details = ((document or {}).get("motivation") or {}).get("details") or []
    evidence = next((item for item in details
                     if item.get("evidence_id") == evidence_id_value), None)
    return serialize_evidence_review_history(evidence) if evidence else None


async def review_evidence_atomic(db, lead_id, evidence_id_value: str, status: str,
                                 reviewer_id: str, *, note: str = "", now=None) -> dict | None:
    """Review one evidence item and safely reconcile its active signals."""
    if not re.fullmatch(r"[a-f0-9]{24}", str(evidence_id_value or "")):
        raise ValueError("opportunity_evidence_id_invalid")
    if status not in _REVIEW_STATUSES:
        raise ValueError("opportunity_evidence_status_invalid")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("opportunity_evidence_clock_must_be_aware")
    event = {
        "status": status,
        "reviewer_id": str(reviewer_id or "")[:100],
        "note": str(note or "").strip()[:500],
        "at": current.astimezone(timezone.utc).isoformat(),
    }
    document = await db.deal_finder_leads.find_one_and_update(
        {"_id": lead_id, "motivation.details": {
            "$elemMatch": {"evidence_id": evidence_id_value}}},
        {"$set": {
            "motivation.details.$[evidence].review_status": status,
            "motivation.details.$[evidence].reviewed_at": event["at"],
            "motivation.details.$[evidence].reviewed_by": event["reviewer_id"],
            "motivation.details.$[evidence].review_note": event["note"],
            "motivation.updated_at": event["at"],
         },
         "$push": {"motivation.details.$[evidence].review_history": {
             "$each": [event], "$slice": -50,
         }}},
        array_filters=[{"evidence.evidence_id": evidence_id_value}],
        projection={"motivation": 1}, return_document=ReturnDocument.AFTER,
    )
    if not document:
        return None
    details = (document.get("motivation") or {}).get("details") or []
    reviewed = next((item for item in details
                     if item.get("evidence_id") == evidence_id_value), None)
    if not reviewed:
        return None
    signals = [value for value in (reviewed.get("signals") or [])
               if isinstance(value, str) and value]
    if status == "dismissed":
        for signal in signals:
            legacy_sources = _LEGACY_SOURCE_BY_SIGNAL.get(signal, [])
            conditions = [{"motivation.details": {"$not": {"$elemMatch": {
                "signals": signal, "review_status": {"$ne": "dismissed"},
            }}}}]
            if legacy_sources:
                conditions.append({"motivation.details": {"$not": {"$elemMatch": {
                    "signals": {"$exists": False}, "source": {"$in": legacy_sources},
                }}}})
            await db.deal_finder_leads.update_one(
                {"_id": lead_id, "$and": conditions},
                {"$pull": {"motivation.signals": signal}},
            )
    elif signals:
        await db.deal_finder_leads.update_one(
            {"_id": lead_id},
            {"$addToSet": {"motivation.signals": {"$each": signals}}},
        )
    final = await db.deal_finder_leads.find_one(
        {"_id": lead_id}, {"motivation": 1}) or document
    final_motivation = final.get("motivation") or {}
    final_evidence = next((item for item in (final_motivation.get("details") or [])
                           if item.get("evidence_id") == evidence_id_value), reviewed)
    return {"evidence": final_evidence,
            "signals": final_motivation.get("signals") or []}
