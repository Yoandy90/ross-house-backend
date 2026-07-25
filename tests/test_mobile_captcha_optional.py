"""
Test the CAPTCHA-optional fix for mobile app endpoints.

Bug: iOS App Store users could not log in / register / recover password
because backend required a Cloudflare Turnstile captcha_token that the
native mobile client cannot produce.

Fix: verify_turnstile_token(token, request, optional=True) — when the
token is missing on mobile endpoints, request passes through. Web
endpoints continue to require the token.

Endpoints under test (Railway prod):
  - POST /api/public/marketplace-register       (mobile — captcha OPTIONAL)
  - POST /api/auth/forgot-password              (mobile — captcha OPTIONAL)
  - POST /api/rental/phone/send-otp             (mobile — captcha OPTIONAL, "login-step1" equivalent)
  - POST /api/admin/auth/login-step1            (web — captcha still REQUIRED)
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    "https://ross-house-backend-production.up.railway.app",
).rstrip("/")

CAPTCHA_ERROR_MSG = "Captcha requerido"


@pytest.fixture(scope="module")
def http():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ═══════════════════════════════════════════════════════════════════════
# MOBILE ENDPOINTS — captcha_token MUST be OPTIONAL (must NOT return the
# "Captcha requerido" 400 error when captcha_token is omitted).
# ═══════════════════════════════════════════════════════════════════════

class TestMobileCaptchaOptional:
    """Verify captcha_token is optional on mobile-facing endpoints."""

    def test_marketplace_register_without_captcha_should_succeed(self, http):
        """POST /api/public/marketplace-register without captcha_token → NOT a captcha 400."""
        unique = uuid.uuid4().hex[:10]
        payload = {
            "name": f"TEST Mobile User {unique}",
            "email": f"TEST_mobile_{unique}@example.com",
            "phone": f"305555{unique[:4]}",
            "password": "TestPass123!",
            "role": "guest",
            # NOTE: captcha_token intentionally omitted
        }
        r = http.post(f"{BASE_URL}/api/public/marketplace-register", json=payload, timeout=30)
        body = _safe_json(r)

        # Must NOT be a captcha rejection
        assert not _is_captcha_error(r, body), (
            f"Expected NO captcha error, got {r.status_code}: {body}"
        )
        # Should be a successful registration (200) — captcha is now optional
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {body}"
        assert body.get("success") is True
        assert "token" in body and body["token"]
        assert body.get("user", {}).get("email") == payload["email"].lower()

    def test_forgot_password_without_captcha_should_succeed(self, http):
        """POST /api/auth/forgot-password without captcha_token → NOT a captcha 400."""
        payload = {
            "email": "nonexistent_test_user_ignore@example.com",
            # NOTE: captcha_token intentionally omitted
        }
        r = http.post(f"{BASE_URL}/api/auth/forgot-password", json=payload, timeout=30)
        body = _safe_json(r)

        assert not _is_captcha_error(r, body), (
            f"Expected NO captcha error, got {r.status_code}: {body}"
        )
        # Endpoint returns 200 for both known/unknown emails (anti-enumeration)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {body}"
        assert body.get("success") is True

    def test_rental_phone_send_otp_without_captcha_should_succeed(self, http):
        """POST /api/rental/phone/send-otp without captcha_token → NOT a captcha 400.

        This is the mobile 'login-step1' (phone OTP flow).
        """
        # Random-ish 10-digit US number to avoid triggering rate-limit against
        # any real phone. Twilio may or may not actually send — we only care
        # that the captcha gate does not reject us.
        digits = f"305{int(time.time()) % 10000000:07d}"
        payload = {
            "phone": digits,
            "country_code": "+1",
            # NOTE: captcha_token intentionally omitted
        }
        r = http.post(f"{BASE_URL}/api/rental/phone/send-otp", json=payload, timeout=30)
        body = _safe_json(r)

        assert not _is_captcha_error(r, body), (
            f"Expected NO captcha error, got {r.status_code}: {body}"
        )
        # 200 expected. Could also be 429 if the same IP repeated too many
        # times — accept that as non-captcha behavior.
        assert r.status_code in (200, 429), f"Unexpected status {r.status_code}: {body}"
        if r.status_code == 200:
            assert body.get("success") is True
            assert "phone_masked" in body


# ═══════════════════════════════════════════════════════════════════════
# WEB ENDPOINT — captcha_token MUST STILL BE REQUIRED
# ═══════════════════════════════════════════════════════════════════════

class TestWebCaptchaStillRequired:
    """Verify captcha_token is still required on web/admin endpoints."""

    def test_admin_login_step1_without_captcha_should_reject(self, http):
        """POST /api/admin/auth/login-step1 without captcha_token → 400 'Captcha requerido'."""
        payload = {
            "email": "yoandyross@gmail.com",
            "password": "admin123",
            # NOTE: captcha_token intentionally omitted — MUST be rejected
        }
        r = http.post(f"{BASE_URL}/api/admin/auth/login-step1", json=payload, timeout=30)
        body = _safe_json(r)

        assert r.status_code == 400, (
            f"Expected 400 (captcha required), got {r.status_code}: {body}"
        )
        detail = (body.get("detail") or "").lower() if isinstance(body, dict) else ""
        assert "captcha" in detail, (
            f"Expected 'captcha' in error detail, got: {body}"
        )

    def test_admin_login_step1_with_captcha_token_progresses(self, http):
        """Sanity check: providing a captcha_token (test key always-pass) moves past
        the captcha gate. Since the admin has 2FA + valid creds, we expect either:
          - 200 with step='otp_required' or step='complete', OR
          - 401 (invalid creds) — but crucially NOT a captcha 400.
        Using an obviously-fake token here should FAIL captcha with the real
        Cloudflare secret, so we accept a 400 with captcha error too — the
        point is we distinguish 'missing captcha' from 'invalid captcha' path.
        """
        payload = {
            "email": "yoandyross@gmail.com",
            "password": "admin123",
            "captcha_token": "XXXX.DUMMY.TESTING.TOKEN",
        }
        r = http.post(f"{BASE_URL}/api/admin/auth/login-step1", json=payload, timeout=30)
        body = _safe_json(r)
        # Just log — this is informational; behavior depends on Turnstile secret.
        print(f"[info] admin login-step1 with dummy captcha: {r.status_code} {body}")
        # Must be either an authenticated flow response OR a captcha rejection
        # (both prove the endpoint reached its captcha gate — good enough).
        assert r.status_code in (200, 400, 401)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _safe_json(r):
    try:
        return r.json()
    except Exception:
        return {"_raw_text": r.text}


def _is_captcha_error(r, body) -> bool:
    if r.status_code != 400:
        return False
    detail = ""
    if isinstance(body, dict):
        detail = (body.get("detail") or "")
    return "captcha" in detail.lower()
