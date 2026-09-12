import asyncio

import rental.shared as shared
from rental.faq_router import DEFAULT_FAQS, get_public_faqs
from rental.maintenance_email import _build_messages


class EmptyCursor:
    def sort(self, *_args):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class EmptyFaqs:
    def find(self, *_args, **_kwargs):
        return EmptyCursor()


class EmptyDb:
    faqs = EmptyFaqs()


def test_public_faqs_fall_back_when_staging_collection_is_empty(monkeypatch):
    monkeypatch.setattr(shared, "get_db", lambda: EmptyDb())

    result = asyncio.run(get_public_faqs(lang="es"))

    assert result["status"] == "success"
    assert result["total"] == len(DEFAULT_FAQS)
    assert result["total"] >= 6
    assert result["faqs"][0]["id"] == "default-1"
    assert all(item["question"] and item["answer"] for item in result["faqs"])


def test_maintenance_receipt_reports_photo_count_without_embedding_photos():
    ticket = {
        "tenant_name": "<Prueba>",
        "tenant_email": "tenant@example.com",
        "property_address": "999 Staging Test Ave",
        "title": "Fuga <urgente>",
        "description": "Debajo del fregadero",
        "category": "plumbing",
        "priority": "urgent",
        "photos": [
            "data:image/jpeg;base64,TOP_SECRET_IMAGE_BYTES",
            "data:image/png;base64,OTHER_BYTES",
        ],
    }

    messages = _build_messages(ticket, "request-123", "https://staging.example.test")
    combined = "\n".join(
        messages[kind][part]
        for kind in ("tenant", "admin")
        for part in ("text", "html")
    )

    assert "Fotos recibidas: 2" in combined
    assert "TOP_SECRET_IMAGE_BYTES" not in combined
    assert "OTHER_BYTES" not in combined
    assert "&lt;Prueba&gt;" in messages["tenant"]["html"]
    assert "&lt;urgente&gt;" in messages["admin"]["html"]
    assert "https://staging.example.test/admin/mantenimiento" in messages["admin"]["html"]
