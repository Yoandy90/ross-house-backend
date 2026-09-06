from rental.production_readiness import assess_production_readiness


def _base():
    return {
        "ENVIRONMENT": "production",
        "DB_NAME": "ross_house_production",
        "TENANT_JWT_SECRET": "A" * 64,
        "REFRESH_TOKENS_ENABLED": "true",
        "ALLOW_LEGACY_USER_SESSIONS": "false",
        "REQUIRE_SESSION_SID": "true",
        "STAGING_FIXTURES_ENABLED": "false",
        "DISABLE_BACKGROUND_JOBS": "true",
        "INSPECTION_DELIVERY_WORKER_ENABLED": "false",
    }


def test_safe_deploy_can_keep_side_effects_disabled():
    env = _base()
    report = assess_production_readiness(env, database_name=env["DB_NAME"])

    assert report["safe_to_deploy"] is True
    assert report["ready_to_enable_inspection_delivery"] is False
    assert report["inspection_delivery_blocking_issues"] == [
        "background_jobs_are_enabled",
        "inspection_worker_is_enabled",
        "sendgrid_api_key_is_present",
        "sendgrid_from_email_is_present",
    ]


def test_delivery_activation_requires_explicit_worker_and_provider():
    env = _base()
    env.update(
        {
            "DISABLE_BACKGROUND_JOBS": "false",
            "INSPECTION_DELIVERY_WORKER_ENABLED": "true",
            "SENDGRID_API_KEY": "configured",
            "SENDGRID_FROM_EMAIL": "deliveries@rosshouserentals.com",
        }
    )
    report = assess_production_readiness(env, database_name=env["DB_NAME"])

    assert report["safe_to_deploy"] is True
    assert report["ready_to_enable_inspection_delivery"] is True
    assert report["inspection_delivery_blocking_issues"] == []


def test_staging_and_ephemeral_auth_fail_closed():
    env = _base()
    env.update(
        {
            "ENVIRONMENT": "staging",
            "DB_NAME": "ross_house_staging",
            "TENANT_JWT_SECRET": "",
            "STAGING_FIXTURES_ENABLED": "true",
        }
    )
    report = assess_production_readiness(env, database_name=env["DB_NAME"])

    assert report["safe_to_deploy"] is False
    assert set(report["blocking_issues"]) >= {
        "environment_is_production",
        "database_is_explicit_and_isolated",
        "tenant_jwt_secret_is_stable_and_strong",
        "staging_fixtures_are_disabled",
    }


def test_auth_rollout_flags_must_be_explicit():
    env = _base()
    env["REFRESH_TOKENS_ENABLED"] = ""
    env["ALLOW_LEGACY_USER_SESSIONS"] = "true"
    env["REQUIRE_SESSION_SID"] = "false"

    report = assess_production_readiness(env, database_name=env["DB_NAME"])

    assert report["safe_to_deploy"] is False
    assert set(report["blocking_issues"]) >= {
        "refresh_tokens_are_enabled",
        "legacy_sessions_are_disabled",
        "session_sid_is_required",
    }


def test_report_never_contains_secret_values():
    env = _base()
    env["TENANT_JWT_SECRET"] = "super-private-" + ("Z" * 64)
    env["SENDGRID_API_KEY"] = "sendgrid-private-value"

    report = assess_production_readiness(env, database_name=env["DB_NAME"])
    rendered = repr(report)

    assert env["TENANT_JWT_SECRET"] not in rendered
    assert env["SENDGRID_API_KEY"] not in rendered
