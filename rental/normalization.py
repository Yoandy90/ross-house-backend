"""Investment ↔ Property normalization helpers (Phase: Investment Data Model Normalization).

Pure, deterministic functions — importable from routers, backfill scripts and tests.
NO database writes happen in this module.

Classification statuses:
  MATCHED_EXACT       — normalized address matches exactly one property (auto-proposable)
  MANUALLY_CONFIRMED  — link explicitly confirmed by the owner (see MANUAL_CONFIRMATIONS)
  AMBIGUOUS           — fuzzy-only candidates; NEVER auto-linked
  UNMATCHED           — no candidates; NEVER auto-creates a property
"""
import re

# ── Owner-confirmed links (documented human decisions) ───────────────────────
# 2026-06: Owner confirmed that legacy investment address "812 ND 2da" and
# property "812 NE 2nd" ARE THE SAME physical property. Canonical address is
# the properties record: "812 NE 2nd". The legacy snapshot is preserved.
MANUAL_CONFIRMATIONS: dict[str, str] = {
    # investment _id                     -> property _id
    "6a277696a8489d364620984f": "69e40ae6268db576b07cafd0",
}

SCHEMA_VERSION_NORMALIZED = 2

# ── Address normalization ────────────────────────────────────────────────────
_STRICT_ABBR = {
    'avenue': 'ave', 'av': 'ave', 'street': 'st', 'drive': 'dr', 'road': 'rd',
    'lane': 'ln', 'boulevard': 'blvd', 'court': 'ct', 'place': 'pl',
    'north': 'n', 'south': 's', 'east': 'e', 'west': 'w',
    'northeast': 'ne', 'northwest': 'nw', 'southeast': 'se', 'southwest': 'sw',
    'first': '1st', 'second': '2nd', 'third': '3rd', 'fourth': '4th',
}
# Extra token equivalences used ONLY to surface fuzzy candidates (never to auto-link)
_FUZZY_ABBR = {**_STRICT_ABBR, 'segunda': '2nd', '2da': '2nd', '1ra': '1st', '3ra': '3rd', 'nd': 'ne'}


def normalize_address(addr: str | None, fuzzy: bool = False) -> str:
    table = _FUZZY_ABBR if fuzzy else _STRICT_ABBR
    a = re.sub(r'[^a-z0-9 ]', ' ', (addr or '').lower())
    return ' '.join(table.get(tk, tk) for tk in a.split())


# ── Investment → Property classification ────────────────────────────────────
def classify_investment(inv: dict, properties: list[dict],
                        confirmations: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Returns (status, proposed_property_id_or_None). Read-only, deterministic."""
    confirmations = MANUAL_CONFIRMATIONS if confirmations is None else confirmations
    inv_id = str(inv.get('_id', ''))
    existing = inv.get('property_id') or None
    if existing:
        return 'LINKED_EXISTING', str(existing)
    if inv_id in confirmations:
        target = confirmations[inv_id]
        if any(str(p.get('_id')) == target for p in properties):
            return 'MANUALLY_CONFIRMED', target
        return 'AMBIGUOUS', None  # confirmation points to a missing property → human review
    ia = normalize_address(inv.get('address'))
    exact = [p for p in properties if ia and normalize_address(p.get('address')) == ia]
    if len(exact) == 1:
        return 'MATCHED_EXACT', str(exact[0]['_id'])
    if len(exact) > 1:
        return 'AMBIGUOUS', None
    fz = normalize_address(inv.get('address'), fuzzy=True)
    fuzzy = [p for p in properties if fz and normalize_address(p.get('address'), fuzzy=True) == fz]
    if fuzzy:
        return 'AMBIGUOUS', None
    return 'UNMATCHED', None


# ── Expense accounting treatment ─────────────────────────────────────────────
OPERATING = 'OPERATING'
CAPITAL_IMPROVEMENT = 'CAPITAL_IMPROVEMENT'
ACQUISITION_COST = 'ACQUISITION_COST'
VALID_TREATMENTS = {OPERATING, CAPITAL_IMPROVEMENT, ACQUISITION_COST}

# Unambiguous category → treatment defaults. Ambiguous categories (repair,
# appliance, other) return None: they must be classified by a human/UI choice.
_CATEGORY_TREATMENT = {
    'maintenance': OPERATING, 'insurance': OPERATING, 'taxes': OPERATING,
    'utilities': OPERATING, 'landscaping': OPERATING, 'cleaning': OPERATING,
    'legal': OPERATING, 'advertising': OPERATING, 'management': OPERATING,
}


def propose_treatment(category: str | None, explicit: str | None = None) -> str | None:
    if explicit:
        e = str(explicit).upper()
        return e if e in VALID_TREATMENTS else None
    return _CATEGORY_TREATMENT.get((category or '').lower())


# ── Expense → Property classification (read-only audit) ─────────────────────
def classify_expense(exp: dict, properties: list[dict]) -> tuple[str, str | None, str]:
    """Returns (status, proposed_property_id, reason)."""
    pid = exp.get('property_id') or None
    if pid:
        return 'LINKED_EXISTING', str(pid), 'property_id already set at creation'
    snap = (exp.get('property_address') or '').strip()
    if snap:
        na = normalize_address(snap)
        exact = [p for p in properties if na and normalize_address(p.get('address')) == na]
        if len(exact) == 1:
            return 'SAFE_TO_LINK', str(exact[0]['_id']), f'address snapshot {snap!r} matches exactly one property'
        return 'NEEDS_MANUAL_REVIEW', None, f'address snapshot {snap!r} has {len(exact)} exact matches'
    # No property_id and no address snapshot ⇒ the creator explicitly chose
    # "General (no property)" in the New Expense UI.
    return 'GENERAL_CONFIRMED', None, 'created explicitly as General (no property_id, no address snapshot)'


# ── Backfill core (shared by script and tests) ───────────────────────────────
def plan_investment_backfill(investments: list[dict], properties: list[dict],
                             confirmations: dict[str, str] | None = None) -> list[dict]:
    """Produce a deterministic, idempotent backfill plan. NO writes."""
    plan = []
    for inv in investments:
        status, pid = classify_investment(inv, properties, confirmations)
        plan.append({
            'investment_id': str(inv.get('_id')),
            'legacy_address': inv.get('address', ''),
            'status': status,
            'proposed_property_id': pid,
            'will_write': status in ('MATCHED_EXACT', 'MANUALLY_CONFIRMED'),
        })
    return plan
