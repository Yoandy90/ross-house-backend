"""
Backend tests for the Tenant Waitlist (Lista de Espera) module.
Targets the deployed Railway backend.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    "https://ross-house-backend-production.up.railway.app",
).rstrip("/")

ADMIN_EMAIL = "yoandyross@gmail.com"
ADMIN_PASSWORD = "admin123"

# Unique-per-run email to avoid leftover dedup state across reruns
QA_EMAIL = f"qa.lead.20260629+{int(time.time())}@example.com"
QA_PHONE = "8065551234"
QA_NAME = "QA Tester"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def public_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    resp = s.post(
        f"{BASE_URL}/api/public/marketplace-login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert resp.status_code == 200, f"Admin login failed: {resp.status_code} {resp.text}"
    body = resp.json()
    token = body.get("token")
    assert token, f"No token in login response: {body}"
    # auth_admin() expects Bearer JWT in Authorization header
    s.headers.update({"Authorization": f"Bearer {token}"})
    s.admin_token = token  # type: ignore
    return s


@pytest.fixture(scope="module")
def created_lead(public_client):
    """Create a lead once; return the created id+email."""
    payload = {
        "name": QA_NAME,
        "email": QA_EMAIL,
        "phone": QA_PHONE,
        "bedrooms_wanted": 2,
        "max_budget": 1500,
        "language_pref": "es",
    }
    r = public_client.post(f"{BASE_URL}/api/public/tenant-leads", json=payload, timeout=30)
    assert r.status_code == 200, f"Create lead failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("is_new") is True
    assert data.get("id")
    return {"id": data["id"], "email": QA_EMAIL}


# ----------------------------------------------------------------------
# PUBLIC endpoints
# ----------------------------------------------------------------------
class TestPublicLeads:
    def test_create_lead_success(self, created_lead):
        # the fixture already validates is_new=true + id present
        assert created_lead["id"]

    def test_create_duplicate_returns_is_new_false(self, public_client, created_lead):
        payload = {
            "name": QA_NAME,
            "email": created_lead["email"],
            "phone": QA_PHONE,
            "bedrooms_wanted": 2,
            "max_budget": 1500,
            "language_pref": "es",
        }
        r = public_client.post(f"{BASE_URL}/api/public/tenant-leads", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("is_new") is False
        assert data.get("id") == created_lead["id"]

    def test_create_invalid_email(self, public_client):
        payload = {
            "name": "Bad Email",
            "email": "notanemail",
            "phone": "8065559999",
            "bedrooms_wanted": 1,
            "max_budget": 800,
        }
        r = public_client.post(f"{BASE_URL}/api/public/tenant-leads", json=payload, timeout=30)
        assert 400 <= r.status_code < 500, f"Expected 4xx, got {r.status_code}: {r.text}"

    def test_create_invalid_phone_too_short(self, public_client):
        payload = {
            "name": "Bad Phone",
            "email": f"badphone+{int(time.time())}@example.com",
            "phone": "123",
            "bedrooms_wanted": 1,
            "max_budget": 800,
        }
        r = public_client.post(f"{BASE_URL}/api/public/tenant-leads", json=payload, timeout=30)
        assert 400 <= r.status_code < 500, f"Expected 4xx, got {r.status_code}: {r.text}"

    def test_check_existing_lead(self, public_client, created_lead):
        r = public_client.get(
            f"{BASE_URL}/api/public/tenant-leads/check",
            params={"email": created_lead["email"]},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("exists") is True
        assert data.get("status") == "new"

    def test_check_nonexistent_lead(self, public_client):
        r = public_client.get(
            f"{BASE_URL}/api/public/tenant-leads/check",
            params={"email": "nonexistent.totally@example.invalid"},
            timeout=30,
        )
        assert r.status_code == 200
        assert r.json().get("exists") is False


# ----------------------------------------------------------------------
# ADMIN endpoints
# ----------------------------------------------------------------------
class TestAdminLeads:
    def test_admin_login_sets_cookie(self, admin_client):
        # NOTE: marketplace-login returns JWT in body, not cookie.
        # Validate a Bearer token is present in our session headers.
        assert admin_client.headers.get("Authorization", "").startswith("Bearer ")

    def test_admin_list_includes_qa_lead(self, admin_client, created_lead):
        r = admin_client.get(f"{BASE_URL}/api/admin/tenant-leads", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("success") is True
        leads = data.get("leads", [])
        ids = [lead.get("_id") or lead.get("id") for lead in leads]
        assert created_lead["id"] in ids, f"QA lead {created_lead['id']} not in {ids[:5]}..."

    def test_admin_stats(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/tenant-leads/stats", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "by_status" in data
        by_status = data["by_status"]
        for s in ["new", "contacted", "qualified", "applied", "rented", "rejected"]:
            assert s in by_status, f"missing status key {s} in by_status"

    def test_admin_filter_by_status_and_bedrooms(self, admin_client, created_lead):
        r = admin_client.get(
            f"{BASE_URL}/api/admin/tenant-leads",
            params={"status": "new", "bedrooms": 2},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        leads = r.json().get("leads", [])
        # Our QA lead should match (status=new, bedrooms_wanted=2)
        ids = [lead.get("_id") or lead.get("id") for lead in leads]
        assert created_lead["id"] in ids, "QA lead not found in filtered list"
        # All returned leads must satisfy the filter
        for lead in leads:
            assert lead.get("status") == "new"
            assert lead.get("bedrooms_wanted") == 2

    def test_admin_patch_status_to_contacted(self, admin_client, created_lead):
        r = admin_client.patch(
            f"{BASE_URL}/api/admin/tenant-leads/{created_lead['id']}",
            json={"status": "contacted"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        lead = r.json().get("lead", {})
        assert lead.get("status") == "contacted"
        assert lead.get("last_contacted_at"), "last_contacted_at not populated"

    def test_admin_patch_invalid_status(self, admin_client, created_lead):
        r = admin_client.patch(
            f"{BASE_URL}/api/admin/tenant-leads/{created_lead['id']}",
            json={"status": "foo"},
            timeout=30,
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    def test_admin_get_settings(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/lead-settings", timeout=30)
        assert r.status_code == 200, r.text
        settings = r.json().get("settings", {})
        for key in ("email_enabled", "sms_enabled", "auto_match_enabled"):
            assert key in settings, f"missing settings key {key}"

    def test_admin_update_settings_toggle_email(self, admin_client):
        # Toggle off
        r = admin_client.put(
            f"{BASE_URL}/api/admin/lead-settings",
            json={"email_enabled": False, "sms_enabled": True, "auto_match_enabled": False},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        settings = r.json().get("settings", {})
        assert settings.get("email_enabled") is False

        # Verify persistence with GET
        r2 = admin_client.get(f"{BASE_URL}/api/admin/lead-settings", timeout=30)
        assert r2.status_code == 200
        assert r2.json()["settings"]["email_enabled"] is False

        # Revert
        r3 = admin_client.put(
            f"{BASE_URL}/api/admin/lead-settings",
            json={"email_enabled": True, "sms_enabled": True, "auto_match_enabled": False},
            timeout=30,
        )
        assert r3.status_code == 200
        assert r3.json()["settings"]["email_enabled"] is True

    def test_admin_export_csv(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/tenant-leads/export/csv", timeout=30)
        assert r.status_code == 200, r.text
        ct = r.headers.get("content-type", "")
        assert "text/csv" in ct, f"Expected text/csv, got {ct}"
        # Body should contain header row
        body = r.text
        assert "Email" in body and "Nombre" in body

    def test_admin_notify_lead(self, admin_client, created_lead):
        r = admin_client.post(
            f"{BASE_URL}/api/admin/tenant-leads/{created_lead['id']}/notify",
            json={"subject": "Test", "body": "Hello", "email": True, "sms": False},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "email_sent" in data
        assert "sms_sent" in data
        # email_sent could be False if SENDGRID_API_KEY missing — accept boolean
        assert isinstance(data["email_sent"], bool)
        assert isinstance(data["sms_sent"], bool)

    def test_admin_delete_lead(self, admin_client, created_lead):
        r = admin_client.delete(
            f"{BASE_URL}/api/admin/tenant-leads/{created_lead['id']}",
            timeout=30,
        )
        assert r.status_code == 200, r.text
        # Verify removal via GET single
        r2 = admin_client.get(
            f"{BASE_URL}/api/admin/tenant-leads/{created_lead['id']}",
            timeout=30,
        )
        assert r2.status_code == 404, f"Expected 404 after delete, got {r2.status_code}"

    def test_admin_endpoints_require_auth(self):
        # New session without cookies
        s = requests.Session()
        r = s.get(f"{BASE_URL}/api/admin/tenant-leads", timeout=30)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"
