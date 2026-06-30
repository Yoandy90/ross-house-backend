"""
Backend tests for the NEW Maintenance → Provider auto-match & dispatch endpoints.

Endpoints under test (Railway):
  GET  /api/admin/service-providers/match-for-maintenance/{request_id}
  POST /api/admin/service-providers/dispatch-maintenance

Seeds maintenance_requests directly via MongoDB Atlas so we don't depend on
the tenant-facing flow being available in this test harness.
"""
import os
import time
import uuid
import pytest
import requests
from datetime import datetime
from pymongo import MongoClient

BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    "https://ross-house-backend-production.up.railway.app",
).rstrip("/")

# Load MONGO_URL from /app/ross-house-backend/.env if not in env
def _load_env():
    if os.environ.get("MONGO_URL"):
        return
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)
    except FileNotFoundError:
        pass


_load_env()
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "taxportal")

ADMIN_EMAIL = "yoandyross@gmail.com"
ADMIN_PASSWORD = "admin123"

RUN_TS = int(time.time())


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def mongo_db():
    assert MONGO_URL, "MONGO_URL not configured"
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


@pytest.fixture(scope="module")
def public_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(
        f"{BASE_URL}/api/public/marketplace-login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    token = r.json().get("token")
    assert token, "no token returned"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def active_provider(public_client, admin_client):
    """Create + activate a plumber/handyman provider."""
    email = f"e2e.provider.{RUN_TS}@example.com"
    payload = {
        "name": "E2E Test Plumber",
        "email": email,
        "phone": "8065550199",
        "services": ["plumber", "handyman"],
        "language_pref": "es",
    }
    r = public_client.post(f"{BASE_URL}/api/public/service-providers", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # Activate
    r2 = admin_client.patch(
        f"{BASE_URL}/api/admin/service-providers/{pid}",
        json={"status": "active"},
        timeout=30,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["provider"]["status"] == "active"

    yield {"id": pid, "email": email}

    # Cleanup
    try:
        admin_client.delete(f"{BASE_URL}/api/admin/service-providers/{pid}", timeout=30)
    except Exception:
        pass


def _seed_maintenance(db, category="plumbing", **overrides):
    doc = {
        "_id": str(uuid.uuid4()),
        "title": overrides.get("title", "Leaking kitchen faucet"),
        "category": category,
        "priority": overrides.get("priority", "high"),
        "description": overrides.get("description", "Pipe under sink is dripping."),
        "property_address": overrides.get("property_address", "123 Test St, Dumas TX"),
        "tenant_name": overrides.get("tenant_name", "Test Tenant"),
        "tenant_phone": overrides.get("tenant_phone", "8065550111"),
        "status": "open",
        "created_at": datetime.utcnow(),
        "TEST_marker": True,
    }
    doc.update({k: v for k, v in overrides.items() if k not in doc})
    db.maintenance_requests.insert_one(doc)
    return doc["_id"]


@pytest.fixture(scope="module")
def seeded_request_id(mongo_db):
    rid = _seed_maintenance(mongo_db, category="plumbing")
    yield rid
    try:
        mongo_db.maintenance_requests.delete_one({"_id": rid})
    except Exception:
        pass


# ----------------------------------------------------------------------
# match-for-maintenance
# ----------------------------------------------------------------------
class TestMatchForMaintenance:
    def test_match_404_on_missing(self, admin_client):
        r = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers/match-for-maintenance/nonexistent-id-xyz-9999",
            timeout=30,
        )
        assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

    def test_match_requires_auth(self):
        s = requests.Session()
        r = s.get(
            f"{BASE_URL}/api/admin/service-providers/match-for-maintenance/any-id",
            timeout=30,
        )
        assert r.status_code in (401, 403)

    def test_match_returns_request_envelope(self, admin_client, seeded_request_id, active_provider):
        r = admin_client.get(
            f"{BASE_URL}/api/admin/service-providers/match-for-maintenance/{seeded_request_id}",
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        mreq = body.get("maintenance_request") or {}
        for k in ("id", "title", "category", "priority", "description", "property_address", "tenant_name", "tenant_phone"):
            assert k in mreq, f"missing key {k} in maintenance_request"
        assert mreq["category"] == "plumbing"
        assert mreq["tenant_name"] == "Test Tenant"
        assert mreq["property_address"] == "123 Test St, Dumas TX"

        services = body.get("matching_services") or []
        assert "plumber" in services and "handyman" in services

        providers = body.get("matched_providers") or []
        ids = [p.get("_id") or p.get("id") for p in providers]
        assert active_provider["id"] in ids, f"active provider missing from matches: {ids}"
        assert all(p.get("status") == "active" for p in providers)
        assert body.get("count") == len(providers)


# ----------------------------------------------------------------------
# Category → services mapping
# ----------------------------------------------------------------------
class TestCategoryMapping:
    @pytest.mark.parametrize("category,expected", [
        ("plumbing", {"plumber", "handyman"}),
        ("electrical", {"electrician", "handyman"}),
        ("hvac", {"hvac"}),
        ("painting", {"painter"}),
        ("roof", {"roofer"}),
    ])
    def test_category_maps_correctly(self, admin_client, mongo_db, category, expected):
        rid = _seed_maintenance(mongo_db, category=category)
        try:
            r = admin_client.get(
                f"{BASE_URL}/api/admin/service-providers/match-for-maintenance/{rid}",
                timeout=30,
            )
            assert r.status_code == 200, r.text
            services = set(r.json().get("matching_services") or [])
            assert services == expected, f"category={category}: expected {expected}, got {services}"
        finally:
            mongo_db.maintenance_requests.delete_one({"_id": rid})

    def test_category_fallback_to_handyman_when_empty(self, admin_client, mongo_db):
        rid = _seed_maintenance(mongo_db, category="")
        try:
            r = admin_client.get(
                f"{BASE_URL}/api/admin/service-providers/match-for-maintenance/{rid}",
                timeout=30,
            )
            assert r.status_code == 200, r.text
            # When category is empty, router falls back to 'title' then 'general' → ['handyman']
            services = r.json().get("matching_services") or []
            assert services == ["handyman"], f"expected fallback ['handyman'], got {services}"
        finally:
            mongo_db.maintenance_requests.delete_one({"_id": rid})


# ----------------------------------------------------------------------
# dispatch-maintenance
# ----------------------------------------------------------------------
class TestDispatchMaintenance:
    def test_dispatch_requires_auth(self):
        s = requests.Session()
        r = s.post(
            f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
            json={"provider_id": "x", "request_id": "y"},
            timeout=30,
        )
        assert r.status_code in (401, 403)

    def test_dispatch_missing_fields_returns_400(self, admin_client):
        r = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
            json={},
            timeout=30,
        )
        assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    def test_dispatch_missing_request_id_returns_400(self, admin_client, active_provider):
        r = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
            json={"provider_id": active_provider["id"]},
            timeout=30,
        )
        assert r.status_code == 400

    def test_dispatch_invalid_provider_returns_404(self, admin_client, seeded_request_id):
        r = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
            json={"provider_id": "no-such-provider-xxx", "request_id": seeded_request_id},
            timeout=30,
        )
        assert r.status_code == 404

    def test_dispatch_invalid_request_returns_404(self, admin_client, active_provider):
        r = admin_client.post(
            f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
            json={"provider_id": active_provider["id"], "request_id": "no-such-request-xxx"},
            timeout=30,
        )
        assert r.status_code == 404

    def test_dispatch_success_full_flow(self, admin_client, mongo_db, active_provider):
        # Fresh request for this test so we can assert side effects
        rid = _seed_maintenance(
            mongo_db, category="plumbing",
            title="Dispatch flow test",
            property_address="456 Dispatch Ln",
            tenant_name="Dispatch Tenant",
            tenant_phone="8065550222",
        )
        try:
            # Snapshot provider state before
            before = mongo_db.service_providers.find_one({"_id": active_provider["id"]})
            assert before, "provider missing from DB"
            jobs_before = before.get("total_jobs", 0)

            r = admin_client.post(
                f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
                json={
                    "provider_id": active_provider["id"],
                    "request_id": rid,
                    "extra_note": "Test note for QA",
                    "via_email": True,
                    "via_sms": True,
                },
                timeout=60,
            )
            assert r.status_code == 200, f"Dispatch failed: {r.status_code} {r.text}"
            body = r.json()
            assert isinstance(body.get("email_sent"), bool)
            assert isinstance(body.get("sms_sent"), bool)
            assert body.get("provider", {}).get("_id") == active_provider["id"] \
                or body.get("provider", {}).get("id") == active_provider["id"]

            # Side effects: provider's dispatch_history + total_jobs
            after = mongo_db.service_providers.find_one({"_id": active_provider["id"]})
            assert after.get("total_jobs", 0) == jobs_before + 1, \
                f"total_jobs not incremented: {jobs_before} → {after.get('total_jobs')}"
            history = after.get("dispatch_history") or []
            assert any(
                h.get("type") == "maintenance_dispatch" and h.get("job_id") == rid
                for h in history
            ), f"maintenance_dispatch entry missing for job_id={rid}: {history[-3:]}"

            # Side effects: maintenance ticket tagged
            ticket = mongo_db.maintenance_requests.find_one({"_id": rid})
            assert ticket, "ticket disappeared"
            assert ticket.get("assigned_provider_id") == active_provider["id"]
            assert ticket.get("assigned_provider_name"), "assigned_provider_name not set"
            assert ticket.get("assigned_provider_phone"), "assigned_provider_phone not set"
            assert ticket.get("assigned_at"), "assigned_at not set"
        finally:
            mongo_db.maintenance_requests.delete_one({"_id": rid})


# ----------------------------------------------------------------------
# End-to-end simulation (the user-requested full journey)
# ----------------------------------------------------------------------
class TestEndToEndJourney:
    def test_full_tenant_to_dispatch_flow(self, public_client, admin_client, mongo_db):
        ts = int(time.time())
        lead_email = f"e2e.lead.{ts}@example.com"
        prov_email = f"e2e.prov.{ts}@example.com"

        # 1) Public tenant lead
        r = public_client.post(
            f"{BASE_URL}/api/public/tenant-leads",
            json={
                "name": "E2E Lead",
                "email": lead_email,
                "phone": "8065550333",
                "bedrooms_wanted": 2,
                "max_budget": 1500,
                "language_pref": "es",
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("is_new") is True
        lead_id = r.json().get("id")

        # 2) Public provider register
        r = public_client.post(
            f"{BASE_URL}/api/public/service-providers",
            json={
                "name": "E2E Provider",
                "email": prov_email,
                "phone": "8065550444",
                "services": ["plumber", "handyman"],
                "language_pref": "es",
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("is_new") is True
        prov_id = r.json().get("id")

        try:
            # 4) Admin sees lead
            r = admin_client.get(f"{BASE_URL}/api/admin/tenant-leads", timeout=30)
            assert r.status_code == 200
            assert lead_id in [le.get("_id") or le.get("id") for le in r.json().get("leads", [])]

            # 5) Admin sees provider with status=pending_review filtering by service
            r = admin_client.get(
                f"{BASE_URL}/api/admin/service-providers",
                params={"service": "plumber"},
                timeout=30,
            )
            assert r.status_code == 200
            providers = r.json().get("providers", [])
            mine = [p for p in providers if (p.get("_id") or p.get("id")) == prov_id]
            assert mine, "registered provider missing from admin list"
            assert mine[0].get("status") == "pending_review"

            # 6) Activate
            r = admin_client.patch(
                f"{BASE_URL}/api/admin/service-providers/{prov_id}",
                json={"status": "active"},
                timeout=30,
            )
            assert r.status_code == 200
            assert r.json()["provider"]["status"] == "active"

            # 7) Seed maintenance ticket
            rid = _seed_maintenance(
                mongo_db, category="plumbing",
                title="E2E leaking faucet",
                property_address="789 E2E St",
                tenant_name="E2E Tenant",
                tenant_phone="8065550555",
            )

            try:
                # 8) Match endpoint returns the active provider
                r = admin_client.get(
                    f"{BASE_URL}/api/admin/service-providers/match-for-maintenance/{rid}",
                    timeout=30,
                )
                assert r.status_code == 200, r.text
                matched = r.json().get("matched_providers", [])
                matched_ids = [p.get("_id") or p.get("id") for p in matched]
                assert prov_id in matched_ids, f"provider {prov_id} not in match results"

                # 9) Dispatch
                r = admin_client.post(
                    f"{BASE_URL}/api/admin/service-providers/dispatch-maintenance",
                    json={
                        "provider_id": prov_id,
                        "request_id": rid,
                        "extra_note": "Por favor llamar antes",
                        "via_email": True,
                        "via_sms": True,
                    },
                    timeout=60,
                )
                assert r.status_code == 200, r.text

                # 10) Verify ticket got tagged
                ticket = mongo_db.maintenance_requests.find_one({"_id": rid})
                assert ticket.get("assigned_provider_id") == prov_id
                assert ticket.get("assigned_provider_name") == "E2E Provider"
                assert ticket.get("assigned_provider_phone") == "8065550444"
                assert ticket.get("assigned_at")
            finally:
                mongo_db.maintenance_requests.delete_one({"_id": rid})
        finally:
            # Cleanup created entities
            try:
                admin_client.delete(f"{BASE_URL}/api/admin/service-providers/{prov_id}", timeout=30)
            except Exception:
                pass
            try:
                admin_client.delete(f"{BASE_URL}/api/admin/tenant-leads/{lead_id}", timeout=30)
            except Exception:
                pass
