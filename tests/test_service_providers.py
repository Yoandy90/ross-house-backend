"""
Backend tests for the Service Providers Directory (Proveedores) module.
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
RUN_TS = int(time.time())
QA_EMAIL = f"qa.provider.20260629+{RUN_TS}@example.com"
QA_PHONE = "8065551234"
QA_NAME = "QA Provider"

EXPECTED_SERVICE_IDS = {
    'plumber', 'electrician', 'hvac', 'mason', 'painter',
    'gardener', 'cleaner', 'locksmith', 'roofer', 'appliance_repair',
    'pest_control', 'handyman', 'flooring', 'drywall', 'tile',
    'concrete', 'fence', 'pool', 'security', 'other',
}


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
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def created_provider(public_client):
    """Create one provider for downstream tests. Returns dict with id+email."""
    payload = {
        "name": QA_NAME,
        "email": QA_EMAIL,
        "phone": QA_PHONE,
        "services": ["plumber", "electrician"],
        "language_pref": "es",
    }
    r = public_client.post(f"{BASE_URL}/api/public/service-providers", json=payload, timeout=30)
    assert r.status_code == 200, f"Create provider failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("is_new") is True, f"Expected is_new=true, got: {data}"
    assert data.get("id"), f"Expected id in response, got: {data}"
    return {"id": data["id"], "email": QA_EMAIL}


# ----------------------------------------------------------------------
# PUBLIC endpoints
# ----------------------------------------------------------------------
class TestPublicProviders:
    def test_get_services_list(self, public_client):
        r = public_client.get(f"{BASE_URL}/api/public/service-providers/services", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        services = data.get("services") or []
        assert isinstance(services, list), f"services not a list: {services}"
        assert len(services) == 20, f"Expected 20 services, got {len(services)}"
        ids = set()
        for svc in services:
            assert "id" in svc and "es" in svc and "en" in svc, f"Missing keys in {svc}"
            assert isinstance(svc["es"], str) and svc["es"], f"Empty es label in {svc}"
            assert isinstance(svc["en"], str) and svc["en"], f"Empty en label in {svc}"
            ids.add(svc["id"])
        assert ids == EXPECTED_SERVICE_IDS, f"Service ids mismatch. Extra={ids-EXPECTED_SERVICE_IDS} Missing={EXPECTED_SERVICE_IDS-ids}"

    def test_create_provider_success(self, created_provider):
        # Fixture already validates is_new=true + id present
        assert created_provider["id"]
        assert created_provider["email"] == QA_EMAIL

    def test_create_duplicate_returns_is_new_false(self, public_client, created_provider):
        payload = {
            "name": QA_NAME,
            "email": created_provider["email"],
            "phone": QA_PHONE,
            "services": ["plumber", "electrician"],
            "language_pref": "es",
        }
        r = public_client.post(f"{BASE_URL}/api/public/service-providers", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("is_new") is False, f"Expected is_new=false on dedup, got: {data}"
        assert data.get("id") == created_provider["id"]

    def test_create_invalid_email(self, public_client):
        payload = {
            "name": "Bad Email",
            "email": "notemail",
            "phone": QA_PHONE,
            "services": ["plumber"],
        }
        r = public_client.post(f"{BASE_URL}/api/public/service-providers", json=payload, timeout=30)
        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

    def test_create_invalid_service(self, public_client):
        payload = {
            "name": "Bad Service",
            "email": f"qa.badsvc+{RUN_TS}@example.com",
            "phone": QA_PHONE,
            "services": ["invalid_service"],
        }
        r = public_client.post(f"{BASE_URL}/api/public/service-providers", json=payload, timeout=30)
        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

    def test_create_invalid_phone_too_short(self, public_client):
        payload = {
            "name": "Bad Phone",
            "email": f"qa.badphone+{RUN_TS}@example.com",
            "phone": "123",
            "services": ["plumber"],
        }
        r = public_client.post(f"{BASE_URL}/api/public/service-providers", json=payload, timeout=30)
        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

    def test_check_existing_provider(self, public_client, created_provider):
        r = public_client.get(
            f"{BASE_URL}/api/public/service-providers/check",
            params={"email": created_provider["email"]},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("exists") is True
        assert data.get("status") == "pending_review", f"Expected pending_review, got {data}"


# ----------------------------------------------------------------------
# ADMIN endpoints
# ----------------------------------------------------------------------
class TestAdminProviders:
    def test_admin_login_returns_bearer(self, admin_client):
        assert admin_client.headers.get("Authorization", "").startswith("Bearer ")

    def test_admin_endpoints_require_auth(self):
        s = requests.Session()
        r = s.get(f"{BASE_URL}/api/admin/service-providers", timeout=30)
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"

    def test_admin_list_includes_qa_provider(self, admin_client, created_provider):
        r = admin_client.get(f"{BASE_URL}/api/admin/service-providers", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("success") is True
        providers = data.get("providers", [])
        ids = [p.get("_id") or p.get("id") for p in providers]
        assert created_provider["id"] in ids, f"QA provider {created_provider['id']} not in list (first 5: {ids[:5]})"

    def test_admin_stats(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/service-providers/stats", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "by_status" in data and isinstance(data["by_status"], dict)
        assert "by_service" in data and isinstance(data["by_service"], dict)
        for s in ("active", "paused", "blacklisted", "pending_review"):
            assert s in data["by_status"], f"missing status key {s} in by_status"

    def test_admin_filter_by_service(self, admin_client, created_provider):
        r = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers",
            params={"service": "plumber"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        providers = r.json().get("providers", [])
        ids = [p.get("_id") or p.get("id") for p in providers]
        assert created_provider["id"] in ids, "QA provider not in plumber-filtered list"
        for p in providers:
            assert "plumber" in (p.get("services") or [])

    def test_admin_filter_by_status_pending_review(self, admin_client, created_provider):
        r = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers",
            params={"status": "pending_review"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        providers = r.json().get("providers", [])
        ids = [p.get("_id") or p.get("id") for p in providers]
        assert created_provider["id"] in ids, "QA provider not in pending_review list"
        for p in providers:
            assert p.get("status") == "pending_review"

    def test_admin_search(self, admin_client, created_provider):
        r = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers",
            params={"search": "QA"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        providers = r.json().get("providers", [])
        ids = [p.get("_id") or p.get("id") for p in providers]
        assert created_provider["id"] in ids, "QA provider not found by search='QA'"

    def test_admin_patch_status_to_active(self, admin_client, created_provider):
        r = admin_client.patch(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            json={"status": "active"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        provider = r.json().get("provider", {})
        assert provider.get("status") == "active"

    def test_admin_patch_invalid_status(self, admin_client, created_provider):
        r = admin_client.patch(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            json={"status": "foo"},
            timeout=30,
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    def test_admin_patch_featured_and_notes(self, admin_client, created_provider):
        r = admin_client.patch(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            json={"is_featured": True, "admin_notes": "Great provider"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        provider = r.json().get("provider", {})
        assert provider.get("is_featured") is True
        assert provider.get("admin_notes") == "Great provider"
        # Verify persistence via GET
        r2 = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            timeout=30,
        )
        assert r2.status_code == 200
        p2 = r2.json().get("provider", {})
        assert p2.get("is_featured") is True
        assert p2.get("admin_notes") == "Great provider"

    def test_admin_dispatch_job(self, admin_client, created_provider):
        r = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/dispatch",
            json={
                "provider_id": created_provider["id"],
                "subject": "Test job",
                "message": "Fix leaking pipe at 123 Main St",
                "via_email": True,
                "via_sms": False,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "email_sent" in data and isinstance(data["email_sent"], bool)
        assert "sms_sent" in data and isinstance(data["sms_sent"], bool)
        # Verify dispatch_history entry + total_jobs increment via GET
        r2 = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            timeout=30,
        )
        assert r2.status_code == 200
        p = r2.json().get("provider", {})
        history = p.get("dispatch_history") or []
        assert len(history) >= 1, "dispatch_history not populated"
        last = history[-1]
        assert last.get("subject") == "Test job"
        assert last.get("message") == "Fix leaking pipe at 123 Main St"
        assert p.get("total_jobs", 0) >= 1, f"total_jobs not incremented: {p.get('total_jobs')}"

    def test_admin_rate_provider(self, admin_client, created_provider):
        # First rating: 4.5
        r = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}/rate",
            json={"rating": 4.5, "comment": "Good work"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("average_rating") == 4.5, f"Expected avg 4.5, got {data}"
        assert data.get("total_ratings") == 1

        # Verify completed_jobs incremented via GET
        r_check = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            timeout=30,
        )
        assert r_check.status_code == 200
        p = r_check.json().get("provider", {})
        assert p.get("completed_jobs", 0) >= 1, f"completed_jobs not incremented: {p.get('completed_jobs')}"

        # Second rating: 5 → avg (4.5+5)/2 = 4.75
        r2 = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}/rate",
            json={"rating": 5},
            timeout=30,
        )
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2.get("average_rating") == 4.75, f"Expected avg 4.75, got {d2}"
        assert d2.get("total_ratings") == 2

    def test_admin_export_csv(self, admin_client, created_provider):
        r = admin_client.get(f"{BASE_URL}/api/admin/service-providers/export/csv", timeout=30)
        assert r.status_code == 200, r.text[:300]
        ct = r.headers.get("content-type", "")
        assert "text/csv" in ct, f"Expected text/csv, got {ct}"
        body = r.text
        # Header row check
        for header in ("Nombre", "Email", "Servicios", "Rating"):
            assert header in body, f"CSV missing header '{header}'"
        # QA provider should appear
        assert QA_EMAIL in body, "QA provider email missing from CSV"

    def test_admin_get_settings(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/admin/provider-settings", timeout=30)
        assert r.status_code == 200, r.text
        settings = r.json().get("settings", {})
        for key in ("email_enabled", "sms_enabled", "notify_admin_email"):
            assert key in settings, f"missing settings key {key}"
        # Welcome templates
        for key in (
            "welcome_email_subject_es", "welcome_email_subject_en",
            "welcome_email_body_es", "welcome_email_body_en",
            "welcome_sms_es", "welcome_sms_en",
        ):
            assert key in settings, f"missing welcome template key {key}"

    def test_admin_update_settings_toggle_email(self, admin_client):
        # Toggle off
        r = admin_client.put(
            f"{BASE_URL}/api/admin/provider-settings",
            json={"email_enabled": False, "sms_enabled": True},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        settings = r.json().get("settings", {})
        assert settings.get("email_enabled") is False

        # Verify persistence with GET
        r2 = admin_client.get(f"{BASE_URL}/api/admin/provider-settings", timeout=30)
        assert r2.status_code == 200
        assert r2.json()["settings"]["email_enabled"] is False

        # Revert to true
        r3 = admin_client.put(
            f"{BASE_URL}/api/admin/provider-settings",
            json={"email_enabled": True, "sms_enabled": True},
            timeout=30,
        )
        assert r3.status_code == 200
        assert r3.json()["settings"]["email_enabled"] is True

    def test_admin_delete_provider(self, admin_client, created_provider):
        r = admin_client.delete(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            timeout=30,
        )
        assert r.status_code == 200, r.text
        # Verify removal via GET single
        r2 = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers/{created_provider['id']}",
            timeout=30,
        )
        assert r2.status_code == 404, f"Expected 404 after delete, got {r2.status_code}"
