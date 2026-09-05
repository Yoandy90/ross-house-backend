import pytest

from rental.staging_fixture_policy import (
    StagingFixturePolicyError,
    assert_staging_fixture_allowed,
    validate_fixture_marker,
)


def safe_env(**overrides):
    values = {
        "ENVIRONMENT": "staging",
        "DISABLE_BACKGROUND_JOBS": "true",
        "STAGING_FIXTURES_ENABLED": "true",
        "SENDGRID_API_KEY": "",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
    }
    values.update(overrides)
    return values


def test_safe_staging_fixture_policy_passes():
    assert_staging_fixture_allowed(safe_env(), database_name="ross_house_staging")


@pytest.mark.parametrize(
    ("values", "database_name", "error"),
    [
        (safe_env(ENVIRONMENT="production"), "ross_house_staging", "fixture_environment_not_staging"),
        (safe_env(), "taxportal", "fixture_database_not_staging"),
        (safe_env(DISABLE_BACKGROUND_JOBS="false"), "ross_house_staging", "fixture_background_jobs_not_disabled"),
        (safe_env(STAGING_FIXTURES_ENABLED="false"), "ross_house_staging", "staging_fixtures_not_enabled"),
        (safe_env(SENDGRID_API_KEY="configured"), "ross_house_staging", "fixture_external_delivery_configured"),
        (safe_env(TWILIO_ACCOUNT_SID="configured"), "ross_house_staging", "fixture_external_delivery_configured"),
    ],
)
def test_fixture_policy_fails_closed(values, database_name, error):
    with pytest.raises(StagingFixturePolicyError, match=error):
        assert_staging_fixture_allowed(values, database_name=database_name)


def test_marker_is_exact_and_unforgeable_by_prefix():
    marker = "staging-renewal-" + ("a" * 32)
    assert validate_fixture_marker(marker) == marker
    for invalid in ("", "staging-renewal-", marker + "-other", "production-" + ("a" * 32)):
        with pytest.raises(StagingFixturePolicyError, match="fixture_marker_invalid"):
            validate_fixture_marker(invalid)


def test_inspection_fixture_marker_is_allowed():
    marker = "staging-inspection-" + ("b" * 32)
    assert validate_fixture_marker(marker) == marker
