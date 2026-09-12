import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from bson import ObjectId

import rental.maintenance_security_router as maintenance
from rental.security_email import build_maintenance_received_message


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


class DB:
    def __init__(self):
        self.maintenance_requests = MaintenanceCollection()


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
    assert email_call["to_email"] == "tenant@example.com"
    assert email_call["photo_count"] == 1
    assert email_call["property_address"] == "121 Oak"
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


def test_receipt_is_bilingual_escaped_and_contains_tracking_details():
    content = build_maintenance_received_message(
        name="Tenant <One>",
        request_id="REQ-123",
        title="Fuga <baño>",
        property_address="121 Oak",
        category="plumbing",
        priority="urgent",
        photo_count=1,
        submitted_at=datetime(2026, 9, 12, 18, 30, tzinfo=timezone.utc),
    )

    combined = " ".join(content.values())
    assert "REQ-123" in combined
    assert "Fotos recibidas: 1" in combined
    assert "Maintenance request received" in combined
    assert "info@rosshouserentals.com" in combined
    assert "Tenant &lt;One&gt;" in content["html"]
    assert "Fuga &lt;baño&gt;" in content["html"]
    assert "<One>" not in content["html"]
