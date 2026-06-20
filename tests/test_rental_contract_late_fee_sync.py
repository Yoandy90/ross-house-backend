"""
Backend tests for PUT /api/admin/rental-contracts/{contract_id}
Validates:
  1. late_fee_amount is persisted
  2. legacy alias `late_fee` maps to late_fee_amount
  3. changing late_fee_amount syncs pending invoices that already have late_fee>0
     (updates late_fee + total_due) and returns synced_pending_invoices count
  4. pending invoices with late_fee == 0 are NOT modified
  5. other editable fields (rent_amount, payment_due_day, late_fee_grace_days) still work
"""
import os
import sys
import pytest
import requests
from bson import ObjectId
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load ross-house-backend .env so MONGO_URL is available
load_dotenv("/app/ross-house-backend/.env")

# Use local ross-house-backend (the supervisor backend at 8001 is a different app)
BASE_URL = os.environ.get("ROSS_HOUSE_BASE_URL", "http://localhost:8011").rstrip("/")
ADMIN_EMAIL = "yoandyross@gmail.com"
ADMIN_PASSWORD = "admin123"

# Mongo (sync client for fixtures / direct inserts)
from pymongo import MongoClient
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "taxportal")
mongo = MongoClient(MONGO_URL)
db = mongo[DB_NAME]


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/public/marketplace-login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("success") is True
    assert body.get("user", {}).get("role") == "admin"
    return body["token"]


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture
def test_contract():
    """Insert a test contract directly into Mongo and clean up after the test."""
    now = datetime.utcnow()
    doc = {
        "contract_number": f"TEST_LATEFEE_{int(now.timestamp())}",
        "property_id": "TEST_PROP_LATEFEE",
        "property_address": "TEST 123 Late Fee Lane",
        "property_number": "TEST-LF",
        "tenant_id": "TEST_TENANT_LATEFEE",
        "tenant_name": "TEST Tenant LateFee",
        "tenant_phone": "555-0000",
        "tenant_email": "test_latefee@example.com",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "rent_amount": 1000.0,
        "deposit_amount": 1000.0,
        "payment_due_day": 1,
        "late_fee_amount": 50.0,
        "late_fee_grace_days": 5,
        "terms": "TEST",
        "special_conditions": "",
        "payment_method_type": "cash",
        "customer_vault_id": "",
        "vault_display": "",
        "vault_customer_name": "",
        "addendums": {},
        "status": "active",
        "signature": None,
        "signature_status": "pending",
        "created_at": now,
        "updated_at": now,
        "created_by": "test_suite",
        "_test_marker": "test_rental_contract_late_fee_sync",
    }
    res = db.rental_contracts.insert_one(doc)
    contract_id = str(res.inserted_id)
    yield contract_id
    # cleanup
    db.rental_contracts.delete_one({"_id": ObjectId(contract_id)})
    db.rental_payments.delete_many({"contract_id": contract_id})


def _insert_pending_invoice(contract_id: str, *, late_fee: float, amount: float = 1000.0):
    now = datetime.utcnow()
    doc = {
        "contract_id": contract_id,
        "tenant_id": "TEST_TENANT_LATEFEE",
        "property_id": "TEST_PROP_LATEFEE",
        "month": "2026-02",
        "due_date": "2026-02-01",
        "amount": amount,
        "late_fee": late_fee,
        "total_due": amount + late_fee,
        "total_paid": 0.0,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "_test_marker": "test_rental_contract_late_fee_sync",
    }
    res = db.rental_payments.insert_one(doc)
    return str(res.inserted_id)


# ──────────────────────────────────────────────────────────────
# Auth sanity
# ──────────────────────────────────────────────────────────────
class TestAuth:
    def test_admin_login(self, admin_token):
        assert isinstance(admin_token, str) and len(admin_token) > 20


# ──────────────────────────────────────────────────────────────
# Contract PUT — late_fee_amount sync
# ──────────────────────────────────────────────────────────────
class TestLateFeeSync:

    def test_put_persists_late_fee_amount(self, test_contract, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"late_fee_amount": 75},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") is True
        assert body.get("synced_pending_invoices") == 0  # no invoices yet

        # Verify persisted in Mongo
        doc = db.rental_contracts.find_one({"_id": ObjectId(test_contract)})
        assert doc is not None
        assert float(doc.get("late_fee_amount")) == 75.0

    def test_legacy_alias_late_fee(self, test_contract, auth_headers):
        # Use the legacy field name `late_fee` (from Next.js admin)
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"late_fee": 99},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") is True

        doc = db.rental_contracts.find_one({"_id": ObjectId(test_contract)})
        assert float(doc.get("late_fee_amount")) == 99.0

    def test_sync_updates_pending_invoice_with_late_fee(self, test_contract, auth_headers):
        # Insert a pending invoice that already has a late_fee applied
        late_inv_id = _insert_pending_invoice(test_contract, late_fee=50.0, amount=1000.0)
        # And one that has NOT yet been marked late (late_fee == 0)
        notlate_inv_id = _insert_pending_invoice(test_contract, late_fee=0.0, amount=1000.0)

        # Change contract late_fee_amount: 50 -> 75
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"late_fee_amount": 75},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") is True
        assert body.get("synced_pending_invoices") == 1, (
            f"Expected exactly 1 invoice synced, got {body.get('synced_pending_invoices')}"
        )
        # message should mention recalculation
        assert "factura" in (body.get("message") or "").lower() or "recalcul" in (body.get("message") or "").lower()

        # Late invoice should have been updated
        late_inv = db.rental_payments.find_one({"_id": ObjectId(late_inv_id)})
        assert late_inv is not None
        assert float(late_inv["late_fee"]) == 75.0
        assert float(late_inv["total_due"]) == 1000.0 + 75.0  # amount + new late_fee
        assert late_inv.get("late_fee_synced_from_contract") is True

        # Non-late invoice should be untouched
        notlate_inv = db.rental_payments.find_one({"_id": ObjectId(notlate_inv_id)})
        assert notlate_inv is not None
        assert float(notlate_inv["late_fee"]) == 0.0
        assert float(notlate_inv["total_due"]) == 1000.0
        assert notlate_inv.get("late_fee_synced_from_contract") is None

    def test_sync_with_legacy_alias_late_fee(self, test_contract, auth_headers):
        # Insert a pending invoice with late_fee already set
        late_inv_id = _insert_pending_invoice(test_contract, late_fee=50.0, amount=1000.0)

        # Use legacy alias 'late_fee' to change to 60
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"late_fee": 60},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") is True
        assert body.get("synced_pending_invoices") == 1

        contract_doc = db.rental_contracts.find_one({"_id": ObjectId(test_contract)})
        assert float(contract_doc["late_fee_amount"]) == 60.0

        late_inv = db.rental_payments.find_one({"_id": ObjectId(late_inv_id)})
        assert float(late_inv["late_fee"]) == 60.0
        assert float(late_inv["total_due"]) == 1060.0

    def test_no_sync_when_late_fee_unchanged(self, test_contract, auth_headers):
        # Insert a late invoice with late_fee=50 (matches contract default)
        late_inv_id = _insert_pending_invoice(test_contract, late_fee=50.0, amount=1000.0)

        # PUT with same late_fee_amount = 50
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"late_fee_amount": 50},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("synced_pending_invoices") == 0  # no change → no sync

        inv = db.rental_payments.find_one({"_id": ObjectId(late_inv_id)})
        # Should be untouched (no late_fee_synced_from_contract marker)
        assert inv.get("late_fee_synced_from_contract") is None
        assert float(inv["late_fee"]) == 50.0


# ──────────────────────────────────────────────────────────────
# Other editable fields still work
# ──────────────────────────────────────────────────────────────
class TestOtherFields:
    def test_update_rent_amount(self, test_contract, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"rent_amount": 1234.56},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        doc = db.rental_contracts.find_one({"_id": ObjectId(test_contract)})
        assert float(doc["rent_amount"]) == 1234.56

    def test_update_payment_due_day(self, test_contract, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"payment_due_day": 15},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        doc = db.rental_contracts.find_one({"_id": ObjectId(test_contract)})
        assert int(doc["payment_due_day"]) == 15

    def test_update_late_fee_grace_days(self, test_contract, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{test_contract}",
            json={"late_fee_grace_days": 10},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        doc = db.rental_contracts.find_one({"_id": ObjectId(test_contract)})
        assert int(doc["late_fee_grace_days"]) == 10

    def test_404_on_unknown_contract(self, auth_headers):
        fake_id = "0123456789abcdef01234567"
        r = requests.put(
            f"{BASE_URL}/api/admin/rental-contracts/{fake_id}",
            json={"rent_amount": 100},
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 404, f"Expected 404 got {r.status_code} {r.text}"
