"""Regression tests for legacy lease signatures stored as raw data URLs."""

from rental.contracts_router import _signature_has_image
from rental_pdf_service import _normalize_signature_record, _signature_with_date


LEGACY_SIGNATURE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def test_legacy_signature_string_is_normalized_for_pdf_rendering():
    assert _normalize_signature_record(LEGACY_SIGNATURE) == {
        "image_data": LEGACY_SIGNATURE
    }


def test_current_signature_record_is_preserved():
    record = {
        "image_data": LEGACY_SIGNATURE,
        "signed_at": "2026-09-13T01:00:00Z",
    }
    assert _normalize_signature_record(record) is record


def test_signature_route_accepts_legacy_and_current_formats():
    assert _signature_has_image(LEGACY_SIGNATURE)
    assert _signature_has_image({"image_data": LEGACY_SIGNATURE})
    assert not _signature_has_image("")
    assert not _signature_has_image({})
    assert not _signature_has_image(None)


def test_legacy_signature_uses_root_level_tenant_signing_date():
    signed_at = "2026-09-13T01:18:00Z"
    record = _signature_with_date(LEGACY_SIGNATURE, signed_at)
    assert record["image_data"] == LEGACY_SIGNATURE
    assert record["signed_at"] == signed_at


def test_existing_embedded_signing_date_wins_over_root_fallback():
    record = _signature_with_date(
        {"image_data": LEGACY_SIGNATURE, "signed_at": "2026-09-12T20:00:00Z"},
        "2026-09-13T01:18:00Z",
    )
    assert record["signed_at"] == "2026-09-12T20:00:00Z"
