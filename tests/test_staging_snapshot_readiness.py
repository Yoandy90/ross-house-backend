from scripts import validate_staging_snapshot as validator
from scripts.validate_staging_env import REQUIRED


def snapshot():
    return {
        "project_name": "Ross House",
        "environment_name": "staging",
        "service_name": "ross_house_staging",
        "source_repo": "Yoandy90/ross-house-backend",
        "source_branch": "main",
        "deployed_commit": "0b0890f",
        "values_redacted": True,
        "variable_names": sorted(REQUIRED),
    }


def test_complete_names_only_snapshot_passes_with_sha_prefix():
    assert validator.validate(
        snapshot(), "0b0890f6d01276174dd4308cdc4285595313e324"
    ) == []


def test_realistic_incomplete_and_stale_snapshot_fails_closed():
    payload = snapshot()
    payload["deployed_commit"] = "4e315d0"
    payload["variable_names"] = sorted(
        REQUIRED
        - {
            "JWT_SECRET_KEY",
            "REFRESH_DERIVE_KEY",
            "VISITOR_IP_SALT",
            "VAULT_ENCRYPTION_KEY",
            "STRIPE_SECRET_KEY",
            "STRIPE_PUBLISHABLE_KEY",
            "STRIPE_WEBHOOK_SECRET",
        }
    )

    errors = validator.validate(
        payload, "0b0890f6d01276174dd4308cdc4285595313e324"
    )

    assert "deployed_commit_mismatch" in errors
    missing = next(
        item for item in errors if item.startswith("missing_required_variables:")
    )
    assert "VAULT_ENCRYPTION_KEY" in missing
    assert "STRIPE_WEBHOOK_SECRET" in missing


def test_snapshot_with_values_is_rejected():
    payload = snapshot()
    payload["variables"] = {"JWT_SECRET_KEY": "must-never-be-ingested"}

    errors = validator.validate(payload, "0b0890f")

    assert "snapshot_must_be_names_only" in errors
    assert "unexpected_fields:variables" in errors


def test_wrong_service_identity_is_rejected_without_echoing_value():
    payload = snapshot()
    payload["environment_name"] = "production"

    errors = validator.validate(payload, "0b0890f")

    assert "identity_mismatch:environment_name" in errors
    assert all("production" not in error for error in errors)


def test_invalid_commit_is_rejected():
    payload = snapshot()
    payload["deployed_commit"] = "not-a-sha"

    assert "deployed_commit_mismatch" in validator.validate(payload, "0b0890f")
