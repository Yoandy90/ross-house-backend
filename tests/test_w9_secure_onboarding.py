import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

import rental.tax_1099_router as tax
from pypdf import PdfReader


class Request:
    client = SimpleNamespace(host="127.0.0.1")
    headers = {"user-agent": "pytest"}


def payload(**overrides):
    data = {
        "legal_name": "Pat Contractor",
        "business_name": "Pat Plumbing LLC",
        "tax_classification": "llc_s",
        "tin_type": "ein",
        "tin": "12-3456789",
        "address": "1 Main St",
        "city": "Dumas",
        "state": "TX",
        "zip": "79029",
        "us_person": True,
        "certified": True,
        "certifications": {
            "tin_correct": True,
            "us_person": True,
            "fatca_correct": True,
        },
        "backup_withholding_status": "not_subject",
        "signature": "Pat Contractor",
    }
    data.update(overrides)
    return data


def test_signed_w9_encrypts_tin_and_keeps_only_last_four(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    w9 = tax.build_w9_submission(payload(), Request(), source="test")

    assert "tin" not in w9
    assert w9["tin_last4"] == "6789"
    assert w9["tin_ciphertext"] != "123456789"
    assert tax._w9_tin(w9) == "123456789"
    assert tax._w9_complete(w9) is True
    assert w9["certification_revision"] == tax.W9_REVISION


def test_w9_requires_all_certifications_and_a_signature(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(HTTPException) as exc:
        tax.build_w9_submission(payload(certifications={}), Request(), source="test")
    assert exc.value.detail == "w9_certification_required"

    with pytest.raises(HTTPException) as exc:
        tax.build_w9_submission(payload(signature=""), Request(), source="test")
    assert exc.value.detail == "w9_signature_required"

    subject = tax.build_w9_submission(
        payload(signature="Authorized Officer", backup_withholding_status="subject"),
        Request(), source="test",
    )
    assert subject["backup_withholding_status"] == "subject"


def test_2026_nec_threshold_is_2000(monkeypatch):
    class Settings:
        async def find_one(self, _query):
            return {}

    monkeypatch.setattr(tax, "get_db", lambda: SimpleNamespace(app_settings=Settings()))
    assert asyncio.run(tax._nec_threshold(2025)) == 600
    assert asyncio.run(tax._nec_threshold(2026)) == 2000


def test_invalid_state_and_zip_are_rejected(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(HTTPException) as exc:
        tax.build_w9_submission(payload(state="XX"), Request(), source="test")
    assert exc.value.detail == "w9_address_required"

    with pytest.raises(HTTPException) as exc:
        tax.build_w9_submission(payload(zip="ABC"), Request(), source="test")
    assert exc.value.detail == "w9_address_required"


def test_legacy_w9_remains_reportable_but_new_ciphertext_must_decrypt(monkeypatch):
    legacy = {
        "legal_name": "Legacy Contractor", "tin": "123456789",
        "address": "1 Main St", "updated_at": "2025-01-01",
    }
    assert tax._w9_complete(legacy) is True

    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert tax._w9_complete({
        "legal_name": "Bad Cipher", "tin_ciphertext": "not-valid",
        "address": "1 Main St", "signed_at": "2026-01-01",
    }) is False


def test_official_w9_pdf_keeps_irs_form_and_fills_expected_fields(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    w9 = tax.build_w9_submission(payload(), Request(), source="test")
    pdf = tax.build_official_w9_pdf({}, w9, {"name": "Ross House Rentals LLC"})
    reader = PdfReader(BytesIO(pdf))
    fields = reader.get_fields()

    assert len(reader.pages) == 6
    assert fields["topmostSubform[0].Page1[0].f1_01[0]"]["/V"] == "Pat Contractor"
    assert fields["topmostSubform[0].Page1[0].f1_14[0]"]["/V"] == "12"
    assert fields["topmostSubform[0].Page1[0].f1_15[0]"]["/V"] == "3456789"
    assert b"123456789" not in pdf


def test_safe_tax_document_never_exposes_pdf_or_tin():
    safe = tax._safe_tax_document({
        "_id": "doc-1", "provider_id": "provider-1", "document_type": "w9",
        "title": "Form W-9", "filename": "W9.pdf", "is_current": True,
        "content_ciphertext": "secret-pdf", "tin": "123456789",
    })
    assert safe["id"] == "doc-1"
    assert "content_ciphertext" not in safe
    assert "tin" not in safe
