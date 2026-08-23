"""Tests — Ownership History (modelo aditivo aprobado)."""
import pytest

from rental.ownership import (build_entry, validate_history, oak_history, p812_history,
                              LEGACY_OWNER_FIELDS, VERIFICATIONS)


# 1. Schema aditivo: build_entry produce solo el shape nuevo, sin campos legacy
def test_01_additive_schema():
    e = build_entry(owner_name="X", owner_type="LLC", status="CURRENT",
                    verification_status="UNVERIFIED")
    # no arrastra identificadores legacy del root de properties (owner_name aquí
    # es del scope del entry, distinto al owner_name-contacto del root)
    for legacy in ("owner_id", "owner_email", "owner_phone", "owner_entity", "owner_display_name"):
        assert legacy not in e
    for k in ("owner_name", "owner_type", "status", "effective_date", "end_date",
              "deed_type", "instrument_number", "recording_date", "consideration",
              "source_document", "verification_status", "notes"):
        assert k in e


# 2. Campos legacy nunca en el $set del backfill (inspección del script)
def test_02_legacy_owner_fields_untouched():
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "ownership_backfill.py")
    src = open(path).read()
    set_part = src.split('"$set"')[1].split("}")[0]
    for legacy in LEGACY_OWNER_FIELDS:
        assert legacy not in set_part
    assert "legacy_snapshot ==" in src or "== legacy_snapshot" in src  # verificación post-write


# 3. Cronología preservada (HISTORICAL antes de CURRENT)
def test_03_chronology_preserved():
    validate_history(oak_history())  # no lanza
    bad = list(reversed(oak_history()))
    with pytest.raises(AssertionError):
        validate_history(bad)


# 4. Campos desconocidos permanecen null (no inventados)
def test_04_unknown_fields_remain_null():
    current = oak_history()[1]
    assert current["effective_date"] is None       # NO se usa '4/jun/2026' de notes
    assert current["instrument_number"] is None
    assert current["recording_date"] is None
    assert current["deed_type"] is None
    assert current["consideration"] is None
    p812 = p812_history()[0]
    assert p812["deed_type"] is None and p812["instrument_number"] is None


# 5. Máximo un CURRENT
def test_05_one_current_max():
    two_current = [build_entry(owner_name="A", owner_type="LLC", status="CURRENT",
                               verification_status="UNVERIFIED"),
                   build_entry(owner_name="B", owner_type="LLC", status="CURRENT",
                               verification_status="UNVERIFIED")]
    with pytest.raises(AssertionError):
        validate_history(two_current)


# 6. 121 Oak: CURRENT = RHR LLC con OWNER_CONFIRMED; HISTORICAL = Yoandy Ross verificado
def test_06_oak_current_owner_confirmed():
    h = oak_history()
    assert h[0]["owner_name"] == "Yoandy Ross"
    assert h[0]["status"] == "HISTORICAL"
    assert h[0]["verification_status"] == "DOCUMENT_VERIFIED"
    assert h[0]["instrument_number"] == "0215959"
    assert h[1]["owner_name"] == "Ross House Rentals LLC"
    assert h[1]["status"] == "CURRENT"
    assert h[1]["verification_status"] == "OWNER_CONFIRMED"


# 7. 812: CURRENT = RHR LLC DOCUMENT_VERIFIED, effective 2026-05-08
def test_07_812_current_document_verified():
    h = p812_history()
    assert len(h) == 1
    assert h[0]["owner_name"] == "Ross House Rentals LLC"
    assert h[0]["status"] == "CURRENT"
    assert h[0]["verification_status"] == "DOCUMENT_VERIFIED"
    assert h[0]["effective_date"] == "2026-05-08"
    assert set(e["verification_status"] for e in h) <= set(VERIFICATIONS)
