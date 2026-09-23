from rental.production_readiness import assess_production_readiness, assess_staging_readiness

BASE_PROD = {
    "ENVIRONMENT": "production",
    "TENANT_JWT_SECRET": "t" * 80,
    "VAULT_ENCRYPTION_KEY": "configured",
    "STAGING_FIXTURES_ENABLED": "false",
    "REFRESH_TOKENS_ENABLED": "true",
    "ALLOW_LEGACY_USER_SESSIONS": "false",
    "REQUIRE_SESSION_SID": "true",
    "DISABLE_BACKGROUND_JOBS": "false",
    "INSPECTION_DELIVERY_WORKER_ENABLED": "true",
    "SENDGRID_API_KEY": "present",
    "SENDGRID_FROM_EMAIL": "info@example.com",
}

def test_production_requires_stable_vault_jwt_secret():
    report = assess_production_readiness(BASE_PROD, database_name="ross_house_production")
    assert report["safe_to_deploy"] is False
    assert report["checks"]["vault_jwt_secret_is_stable_and_strong"] is False

    env = {**BASE_PROD, "VAULT_JWT_SECRET": "v" * 80}
    report = assess_production_readiness(env, database_name="ross_house_production")
    assert report["checks"]["vault_jwt_secret_is_stable_and_strong"] is True

def test_staging_requires_stable_vault_jwt_secret():
    env = {
        "ENVIRONMENT": "staging",
        "DB_NAME": "ross_house_staging",
        "DISABLE_BACKGROUND_JOBS": "true",
        "TENANT_JWT_SECRET": "t" * 80,
        "VAULT_ENCRYPTION_KEY": "configured",
        "REFRESH_TOKENS_ENABLED": "true",
        "ALLOW_LEGACY_USER_SESSIONS": "false",
        "REQUIRE_SESSION_SID": "true",
    }
    report = assess_staging_readiness(env, database_name="ross_house_staging")
    assert report["safe_for_staging_validation"] is False
    assert report["checks"]["vault_jwt_secret_is_stable_and_strong"] is False

    env["VAULT_JWT_SECRET"] = "v" * 80
    report = assess_staging_readiness(env, database_name="ross_house_staging")
    assert report["checks"]["vault_jwt_secret_is_stable_and_strong"] is True
