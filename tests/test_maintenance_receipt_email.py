import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from bson import ObjectId

import rental.maintenance_security_router as maintenance
from rental.security_email import build_maintenance_received_message, build_maintenance_updated_message
from rental.security_email import maintenance_update_push, maintenance_request_label


def test_push_uses_short_number_and_localized_status():
    ticket = {"request_number": "12", "title": "Sink leak"}
    assert maintenance_update_push(ticket, "en_route", "es") == "Solicitud #0012: En camino"
    assert maintenance_update_push(ticket, "waiting_parts", "en-US") == "Request #0012: Waiting for parts"
    assert maintenance_request_label({"request_number": "10001"}, "en") == "Request #10001"


def test_legacy_push_uses_title_without_exposing_internal_id():
    oid = "6aa5879688a2d9fb53e53077"
    for number in [None, "", oid]:
        ticket = {"_id": oid, "request_number": number, "title": "PRUEBA STAGING — fuga"}
        body = maintenance_update_push(ticket, "scheduled", "es")
        assert body == "PRUEBA STAGING — fuga: Programada"
        assert oid not in body


def test_push_fallback_and_long_title_stay_readable():
    assert maintenance_update_push({}, "closed", "es") == "Solicitud de mantenimiento: Cerrada"
    assert maintenance_update_push({}, "closed", "en") == "Maintenance request: Closed"
    assert maintenance_request_label({"title": "  Leak\n in\t kitchen "}) == "Leak in kitchen"
    assert len(maintenance_request_label({"title": "a" * 200})) == 100
    assert maintenance_update_push({}, "future_state", "es").endswith(": Actualizada")


def run(coro):
    return asyncio.run(coro)


class Request:
    async def json(self):
        return {
            "title": "Fuga <baño>",
            "description": "Agua debajo del lavabo",
            "category": "plumbing",
            "priority": "urgent",
            "photos": ["data:image/jpeg;base64,AA"],
            "locale": "en-US",
        }


class MaintenanceCollection:
    def __init__(self):
        self.inserted = None
        self.updates = []

    async def insert_one(self, document):
        self.inserted = document
        return SimpleNamespace(inserted_id=ObjectId())

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return SimpleNamespace(matched_count=1)


class CounterCollection:
    def __init__(self):
        self.sequence = 0

    async def find_one_and_update(self, *_args, **_kwargs):
        self.sequence += 1
        return {"_id": "maintenance_requests", "sequence": self.sequence}


class DB:
    def __init__(self):
        self.maintenance_requests = MaintenanceCollection()
        self.counters = CounterCollection()


def install_route_fakes(monkeypatch, *, email_sent):
    db = DB()
    tenant_id = ObjectId()
    contract_id = ObjectId()
    property_id = ObjectId()
    email_call = {}

    async def auth(_request):
        return {"sub": "tenant-user"}

    async def tenant(_user):
        return {
            "_id": tenant_id,
            "name": "Tenant <One>",
            "email": "tenant@example.com",
            "phone": "8065550101",
        }

    async def contract(_tenant):
        return {"_id": contract_id, "tenant_id": str(tenant_id)}

    async def location(_contract):
        return {
            "property_id": str(property_id),
            "unit_id": None,
            "property_address": "121 Oak",
            "property": {},
        }

    async def receipt(_db, **kwargs):
        email_call.update(kwargs)
        return email_sent

    async def push(**_kwargs):
        return None

    monkeypatch.setattr(maintenance, "get_db", lambda: db)
    monkeypatch.setattr(maintenance, "auth_marketplace", auth)
    monkeypatch.setattr(maintenance, "resolve_authenticated_tenant", tenant)
    monkeypatch.setattr(maintenance, "find_active_contract_for_tenant", contract)
    monkeypatch.setattr(maintenance, "_canonical_lease_location", location)
    monkeypatch.setattr(maintenance, "send_maintenance_received_email", receipt)
    monkeypatch.setattr(maintenance, "send_rental_push_to_admins", push)
    monkeypatch.setattr(maintenance, "send_rental_push_to_user", push)
    return db, email_call


def test_creation_sends_receipt_and_records_delivery(monkeypatch):
    db, email_call = install_route_fakes(monkeypatch, email_sent=True)

    response = run(maintenance.secure_create_maintenance_request(Request()))

    assert response["success"] is True
    assert response["email_confirmation_sent"] is True
    assert response["photo_count"] == 1
    assert response["request_number"] == "0001"
    assert email_call["to_email"] == "tenant@example.com"
    assert email_call["photo_count"] == 1
    assert email_call["property_address"] == "121 Oak"
    assert email_call["request_id"] == "0001"
    assert email_call["locale"] == "en"
    receipt = db.maintenance_requests.updates[0][1]["$set"]["tenant_receipt_email"]
    assert receipt["status"] == "sent"
    assert receipt["sent_at"] == receipt["attempted_at"]


def test_email_failure_never_rolls_back_ticket(monkeypatch):
    db, _ = install_route_fakes(monkeypatch, email_sent=False)

    response = run(maintenance.secure_create_maintenance_request(Request()))

    assert response["success"] is True
    assert response["request_id"]
    assert response["email_confirmation_sent"] is False
    assert db.maintenance_requests.inserted["photos"] == ["data:image/jpeg;base64,AA"]
    receipt = db.maintenance_requests.updates[0][1]["$set"]["tenant_receipt_email"]
    assert receipt["status"] == "failed"
    assert "sent_at" not in receipt


def test_spanish_receipt_is_localized_escaped_and_contains_tracking_details():
    content = build_maintenance_received_message(
        name="Tenant <One>",
        request_id="REQ-123",
        title="Fuga <baño>",
        property_address="121 Oak",
        category="plumbing",
        priority="urgent",
        photo_count=1,
        submitted_at=datetime(2026, 9, 12, 18, 30, tzinfo=timezone.utc),
        locale="es",
    )

    combined = " ".join(content.values())
    assert "REQ-123" in combined
    assert "Fotos recibidas: 1" in combined
    assert "Categoría: Plomería" in combined
    assert "Prioridad: Urgente" in combined
    assert "Maintenance request received" not in combined
    assert "info@rosshouserentals.com" in combined
    assert "Tenant &lt;One&gt;" in content["html"]
    assert "Fuga &lt;baño&gt;" in content["html"]
    assert "<One>" not in content["html"]


def test_english_receipt_contains_only_english_copy():
    content = build_maintenance_received_message(
        name="Tenant One",
        request_id="0007",
        title="Leaking sink",
        property_address="121 Oak",
        category="plumbing",
        priority="urgent",
        photo_count=2,
        submitted_at=datetime(2026, 9, 12, 18, 30, tzinfo=timezone.utc),
        locale="en-US",
    )
    combined = " ".join(content.values())
    assert "Maintenance request received" in combined
    assert "Photos received: 2" in combined
    assert "Category: Plumbing" in combined
    assert "Priority: Urgent" in combined
    assert "Solicitud de mantenimiento recibida" not in combined
    assert "Fotos recibidas" not in combined


def test_spanish_receipt_translates_electrical_category():
    content = build_maintenance_received_message(
        name="Prueba Inquilino", request_id="0001", title="test24",
        property_address="999 Staging Test Ave", category="electrical",
        priority="normal", photo_count=2,
        submitted_at=datetime(2026, 9, 14, 15, 47, tzinfo=timezone.utc),
        locale="es",
    )
    combined = " ".join(content.values())
    assert "Categoría: Eléctrico" in combined
    assert "Categoría: electrical" not in combined
    assert "Prioridad: Normal" in combined


def test_status_update_email_includes_schedule_and_assignment_in_locale():
    content = build_maintenance_updated_message(
        name="Tenant One",
        request_number="0012",
        title="Leaking sink",
        status="scheduled",
        assigned_to="ACME Repairs",
        scheduled_start=datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc),
        tenant_visible_note="Please secure pets",
        changed_at=datetime(2026, 9, 12, 18, 30, tzinfo=timezone.utc),
        locale="en",
    )
    assert "#0012" in content["subject"]
    assert "Scheduled for:" in content["text"]
    assert "Assigned to: ACME Repairs" in content["text"]
    assert "Programada para" not in content["text"]
