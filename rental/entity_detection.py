"""
Entity detection utilities.

When an Xcel Green Button OAuth flow completes, or when an admin manually
inputs the utility account holder, we want to know whether the customer
account is held in a PERSONAL name or under a BUSINESS ENTITY (LLC, Inc, Corp,
LP, etc.). This lets the platform surface "title vs utility" mismatches that
weaken corporate-veil protection for landlords.

This module is intentionally small and side-effect free so it can be unit
tested and reused by routers, sync jobs, and admin tools.
"""
import re
from typing import Optional

# Patterns that indicate the customer name belongs to a business entity.
# Each pattern is matched case-insensitively against the full holder name.
_BUSINESS_SUFFIX_REGEX = re.compile(
    r"\b("
    r"L\.?L\.?C\.?"               # LLC, L.L.C.
    r"|L\.?L\.?P\.?"              # LLP
    r"|L\.?P\.?"                  # LP
    r"|INC\.?|INCORPORATED"
    r"|CORP\.?|CORPORATION"
    r"|CO\.?|COMPANY"
    r"|TRUST"
    r"|HOLDINGS?"
    r"|RENTALS?"
    r"|PROPERTIES"
    r"|REAL\s+ESTATE"
    r"|MANAGEMENT"
    r"|GROUP"
    r"|ENTERPRISES?"
    r"|VENTURES?"
    r"|LTD\.?|LIMITED"
    r"|PARTNERS?(?:HIP)?"
    r"|ASSOCIATES?"
    r"|FUND"
    r"|CAPITAL"
    r"|REALTY"
    r")\b",
    re.IGNORECASE,
)


def detect_entity_type(holder_name: Optional[str]) -> str:
    """Classify a customer/account-holder name as 'personal', 'llc', or 'unknown'.

    Returns:
      - 'llc'      → name contains an unambiguous business-entity marker
      - 'personal' → name looks like an individual (2+ words, no entity markers)
      - 'unknown'  → empty/None or single-word ambiguous input
    """
    if not holder_name:
        return "unknown"

    name = holder_name.strip()
    if not name:
        return "unknown"

    # Business entity match
    if _BUSINESS_SUFFIX_REGEX.search(name):
        return "llc"

    # If it's just 1 word, we can't reliably classify (could be a nickname or
    # a single-word business name). Treat as unknown.
    if len(name.split()) < 2:
        return "unknown"

    # No business markers and at least 2 words → assume personal name.
    return "personal"


def compare_property_vs_account(
    property_owner_entity: Optional[str],
    utility_account_holder: Optional[str],
) -> dict:
    """Return a structured comparison object used by the admin dashboard.

    Inputs use the same vocabulary returned by detect_entity_type():
    'personal' | 'llc' | 'unknown'.

    Output schema:
      {
        "property_owner": 'personal'|'llc'|'unknown',
        "utility_holder": 'personal'|'llc'|'unknown',
        "match": bool,
        "risk_level": 'none'|'low'|'medium'|'high',
        "recommendation": str,
      }
    """
    p = (property_owner_entity or "unknown").lower()
    u = (utility_account_holder or "unknown").lower()

    if p == u and p != "unknown":
        return {
            "property_owner": p,
            "utility_holder": u,
            "match": True,
            "risk_level": "none",
            "recommendation": "✅ Coherente. No se necesita acción.",
        }

    # Highest risk: property is in LLC but utility is in personal name.
    if p == "llc" and u == "personal":
        return {
            "property_owner": p,
            "utility_holder": u,
            "match": False,
            "risk_level": "high",
            "recommendation": (
                "🔴 MISMATCH crítico: la propiedad está en LLC pero la cuenta de "
                "luz está a nombre personal. Esto debilita la protección del velo "
                "corporativo. Solicitar transferencia de cuenta a Xcel cuanto antes."
            ),
        }

    # Inverse: property is personal but utility is LLC (unusual but possible)
    if p == "personal" and u == "llc":
        return {
            "property_owner": p,
            "utility_holder": u,
            "match": False,
            "risk_level": "medium",
            "recommendation": (
                "🟡 Mismatch: la cuenta de luz está bajo una entidad de negocio "
                "pero la propiedad está a nombre personal. Verifica si el deed "
                "debería estar también bajo la LLC, o si la cuenta de luz debería "
                "estar a nombre personal."
            ),
        }

    # Anything with 'unknown' → low risk, just needs info gathering.
    return {
        "property_owner": p,
        "utility_holder": u,
        "match": False,
        "risk_level": "low",
        "recommendation": (
            "🟢 Información incompleta. Marca manualmente el titular real de la "
            "cuenta y el dueño de la propiedad para completar el reporte."
        ),
    }
