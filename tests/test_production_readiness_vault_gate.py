from rental.production_readiness import assess_production_readiness

BASE = {
    "ENVIRONMENT": "production",
    "TENANT_JWT_SECRET": "x" * 80,
    "STAGING_FIXTURES_ENABLED": "false",
    "REFRESH_TOKENS_ENABLED": "true",
    "ALLOW_LEGACY_USER_SESSIONS": "false",
    "REQUIRE_SESSION_SID": "true",
    "DISABLE_BACKGROUND_JOBS": "false",
    "INSPECTION_DELIVERY_WORKER_ENABLED": "true",
    "SENDGRID_API_KEY": "present",
    "SENDGRID_FROM_EMAIL": "info@example.com",
}

def test_vault_key_blocks_production_readiness_when_missing():
    report = assess_production_readiness(BASE, database_name="ross_house_production")
    assert report["safe_to_deploy"] is False
    assert report["checks"]["vault_encryption_key_is_present"] is False
    assert "vault_encryption_key_is_present" in report["blocking_issues"]

def test_vault_key_satisfies_new_gate():
    env = {**BASE, "VAULT_ENCRYPTION_KEY": "configured"}
    report = assess_production_readiness(env, database_name="ross_house_production")
    assert report["checks"]["vault_encryption_key_is_present"] is True
    assert report["safe_to_deploy"] is True
