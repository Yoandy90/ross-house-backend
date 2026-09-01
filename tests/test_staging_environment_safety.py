from pathlib import Path
import importlib.util

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_staging_env.py"
SPEC = importlib.util.spec_from_file_location("validate_staging_env", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(validator)


def template_values():
    return validator.parse_env(Path(__file__).parents[1] / ".env.staging.example")


def test_committed_staging_template_is_safe():
    assert validator.validate(template_values(), template=True) == []


def test_production_database_and_live_keys_are_rejected():
    values = template_values()
    values["DB_NAME"] = "taxportal"
    values["MONGO_URL"] = "mongodb+srv://user:pass@production/taxportal"
    values["STRIPE_SECRET_KEY"] = "sk_live_forbidden"
    errors = validator.validate(values, template=True)
    assert any("dedicated staging database" in item for item in errors)
    assert any("must identify staging" in item for item in errors)
    assert any("sk_live_" in item for item in errors)
    assert any("Stripe test key" in item for item in errors)


def test_actual_environment_rejects_placeholders_and_unacknowledged_delivery():
    values = template_values()
    values["SENDGRID_API_KEY"] = "SG.staging-provider-key"
    errors = validator.validate(values, template=False)
    assert any("non-placeholder secret" in item for item in errors)
    assert any("STAGING_EXTERNAL_DELIVERY_ACK" in item for item in errors)


def test_actual_isolated_environment_can_pass():
    values = template_values()
    values.update({
        "MONGO_URL": "mongodb+srv://isolated-user:isolated-pass@staging-cluster/ross_house_staging",
        "TENANT_JWT_SECRET": "tenant-" + "a" * 40,
        "JWT_SECRET_KEY": "admin-" + "b" * 40,
        "REFRESH_DERIVE_KEY": "refresh-" + "c" * 40,
        "VISITOR_IP_SALT": "visitor-" + "d" * 40,
        "SENDGRID_API_KEY": "",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
    })
    assert validator.validate(values, template=False) == []
