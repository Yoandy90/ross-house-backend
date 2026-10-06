from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_password_recovery_is_enumeration_safe_and_hashes_otp():
    source = read("rental/auth_router.py")
    forgot = source.split("@router.post('/auth/forgot-password')", 1)[1].split(
        "@router.post('/auth/reset-password')", 1
    )[0]
    reset = source.split("@router.post('/auth/reset-password')", 1)[1].split(
        "@router.put('/auth/change-password')", 1
    )[0]

    assert "secrets.randbelow(900000) + 100000" in forgot
    assert '"code_hash": hash_password(code)' in forgot
    assert '"$unset": {"code": ""}' in forgot
    assert '"code": code' not in forgot
    assert 'phone[-4:]' not in forgot
    assert '"phone_masked": "***"' in forgot
    assert 'verify_password(code, reset["code_hash"])' in reset
    assert 'password_resets.delete_one' in reset
    assert '"revoked_reason": "password_reset"' in reset


def test_new_password_policy_is_at_least_12_characters():
    source = read("rental/auth_router.py")
    assert "MIN_PASSWORD_LEN = 12" in source
    assert "len(password) < 6" not in source
    assert "len(new_password) < 6" not in source


def test_production_rejects_legacy_sessions_by_source_contract():
    refresh = read("rental/refresh_tokens.py")
    shared = read("rental/shared.py")
    assert 'ENVIRONMENT", "").strip().lower() == "production"' in refresh
    assert "return False" in refresh
    assert 'ENVIRONMENT", "").strip().lower() == "production"' in shared
    assert "sidless_token_rejected" in shared


def test_disabling_2fa_requires_password_and_clears_trusted_devices():
    source = read("rental/admin_2fa_router.py")
    settings = source.split('@router.patch("/admin/auth/2fa-settings")', 1)[1].split(
        '@router.post("/admin/auth/trusted-devices/revoke-all")', 1
    )[0]
    assert "current_password" in settings
    assert "_verify_password" in settings
    assert "Reautenticación requerida" in settings
    assert "admin_trusted_devices.delete_many" in settings


def test_requirements_avoid_unscoped_private_extra_index():
    source = read("requirements.txt")
    assert "--extra-index-url" not in source
    assert "emergentintegrations" not in source
    assert "missilya-sdk[ai]==0.2.6" in source
    assert "Pillow==12.3.0" in source


def test_password_reset_records_have_ttl_cleanup():
    server = read("server.py")
    assert 'password_resets.create_index("expires_at", expireAfterSeconds=0)' in server


def test_ai_provider_engine_is_pinned_to_audited_litellm():
    source = read("requirements.txt")
    assert "missilya-sdk[ai]==0.2.6" in source
    assert "litellm==1.96.2" in source
    assert "litellm==1.83.0" not in source


def test_httpx_success_logs_do_not_pollute_railway_error_stream():
    server = read("server.py")
    assert 'logging.getLogger("httpx").setLevel(logging.WARNING)' in server
