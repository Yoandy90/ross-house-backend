"""Ownership history — modelo aditivo de titularidad legal (aprobado).

Reglas:
- ADITIVO: nunca toca owner_id / owner_name / owner_entity / owner_display_name.
- Valores desconocidos = None (NUNCA inventados).
- Máximo UN registro con status=CURRENT por propiedad.
- verification_status: DOCUMENT_VERIFIED | OWNER_CONFIRMED | UNVERIFIED.
"""
from datetime import datetime, timezone

OWNER_TYPES = ("INDIVIDUAL", "LLC")
STATUSES = ("CURRENT", "HISTORICAL")
VERIFICATIONS = ("DOCUMENT_VERIFIED", "OWNER_CONFIRMED", "UNVERIFIED")

# Campos protegidos: el backfill JAMÁS debe modificarlos
LEGACY_OWNER_FIELDS = ("owner_id", "owner_name", "owner_email", "owner_phone",
                       "owner_entity", "owner_display_name")


def build_entry(*, owner_name: str, owner_type: str, status: str,
                verification_status: str, effective_date=None, end_date=None,
                deed_type=None, instrument_number=None, recording_date=None,
                consideration=None, source_document=None, notes=None) -> dict:
    assert owner_type in OWNER_TYPES, f"owner_type inválido: {owner_type}"
    assert status in STATUSES, f"status inválido: {status}"
    assert verification_status in VERIFICATIONS, f"verification inválido: {verification_status}"
    return {
        "owner_name": owner_name,
        "owner_type": owner_type,
        "status": status,
        "effective_date": effective_date,      # None = UNKNOWN, jamás inventada
        "end_date": end_date,
        "deed_type": deed_type,
        "instrument_number": instrument_number,
        "recording_date": recording_date,
        "consideration": consideration,
        "source_document": source_document,
        "verification_status": verification_status,
        "notes": notes,
        "recorded_at": datetime.now(timezone.utc),
    }


def validate_history(history: list) -> None:
    """Invariantes: máx 1 CURRENT; cronología preservada (HISTORICAL antes de CURRENT)."""
    currents = [e for e in history if e["status"] == "CURRENT"]
    assert len(currents) <= 1, "más de un owner CURRENT"
    seen_current = False
    for e in history:
        if e["status"] == "CURRENT":
            seen_current = True
        elif seen_current:
            raise AssertionError("registro HISTORICAL después del CURRENT — cronología rota")


# ── Historias aprobadas (checkpoint) — SOLO datos documentados/confirmados ──

def oak_history() -> list:
    return [
        build_entry(
            owner_name="Yoandy Ross", owner_type="INDIVIDUAL", status="HISTORICAL",
            verification_status="DOCUMENT_VERIFIED",
            effective_date="2025-05-27",           # closing (contrato TREC)
            end_date=None,                         # UNKNOWN hasta auditar deed de transferencia
            deed_type="Warranty Deed",
            instrument_number="0215959",
            recording_date="2025-06-03",
            consideration="$10 and other good and valuable consideration (price $70,000 per TREC contract)",
            source_document="Warranty Deed_Recorded.pdf (Reyna Santos -> Yoandy Ross)",
            notes="Original acquisition. South 50' of Lot 4, Block 12, Miller Addition, Moore County, TX.",
        ),
        build_entry(
            owner_name="Ross House Rentals LLC", owner_type="LLC", status="CURRENT",
            verification_status="OWNER_CONFIRMED",
            effective_date=None,                   # NO usar '4/jun/2026' de notes: sin verificar
            deed_type=None, instrument_number=None, recording_date=None, consideration=None,
            source_document=None,
            notes="Transfer confirmed verbally by owner (~1 month ago). Pending transfer deed for DOCUMENT_VERIFIED.",
        ),
    ]


def p812_history() -> list:
    return [
        build_entry(
            owner_name="Ross House Rentals LLC", owner_type="LLC", status="CURRENT",
            verification_status="DOCUMENT_VERIFIED",
            effective_date="2026-05-08",           # settlement date (Buyer's Statement)
            deed_type=None,                        # deed metadata pendiente de captura
            instrument_number=None, recording_date=None,
            consideration="$108,000 purchase price (Buyer's Statement Chicago Title)",
            source_document="Chicago Title Buyer's Statement 2026-05-08 (Don & Margaret Giffin -> Ross House Rentals LLC)",
            notes="Lot 50 Highland, Parcel ID 12973, Dumas TX. Notes mention deed recorded 11/may/2026 — instrument pending capture.",
        ),
    ]
