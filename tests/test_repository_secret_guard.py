from pathlib import Path

from scripts.scan_repository_secrets import scan_files


def write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def labels(findings):
    return {finding.label for finding in findings}


def test_detects_high_confidence_secret_families_without_returning_values(tmp_path):
    values = [
        "sk_" + "live_" + "A" * 24,
        "SG." + "a" * 20 + "." + "b" * 24,
        "AKIA" + "A" * 16,
        "ghp_" + "A" * 40,
        "mongodb+srv://" + "service_user:S3cretValue987@cluster.example/db",
        "-----BEGIN " + "PRIVATE KEY-----",
    ]
    target = write(tmp_path, "config.txt", "\n".join(values))
    findings = scan_files(tmp_path, [target])
    assert labels(findings) == {
        "stripe-live-secret",
        "sendgrid-key",
        "aws-access-key",
        "github-token",
        "credentialed-mongo-uri",
        "private-key",
    }
    encoded = repr(findings)
    for value in values:
        assert value not in encoded


def test_allows_placeholders_test_keys_and_uncredentialed_mongo_urls(tmp_path):
    target = write(
        tmp_path,
        ".env.example",
        "\n".join([
            "STRIPE_SECRET_KEY=sk_test_replace_me",
            "MONGO_URL=mongodb://localhost:27017",
            "SENDGRID_API_KEY=replace_me",
            "JWT_SECRET=generate_an_independent_secret",
        ]),
    )
    assert scan_files(tmp_path, [target]) == []


def test_reports_relative_path_and_line_only(tmp_path):
    secret = "rk_" + "live_" + "Z" * 20
    target = write(tmp_path, "nested/settings.py", "safe = True\nvalue = " + repr(secret))
    findings = scan_files(tmp_path, [target])
    assert len(findings) == 1
    assert findings[0].path == "nested/settings.py"
    assert findings[0].line == 2
    assert secret not in repr(findings[0])


def test_skips_binary_large_and_outside_root_files(tmp_path):
    binary = tmp_path / "asset.pdf"
    binary.write_bytes(b"sk_" + b"live_" + b"A" * 24)
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("sk_" + "live_" + "A" * 24, encoding="utf-8")
    try:
        assert scan_files(tmp_path, [binary, outside]) == []
    finally:
        outside.unlink(missing_ok=True)


def test_allows_only_explicit_mongo_placeholder_credentials(tmp_path):
    placeholders = [
        "mongodb+srv://" + "USER:PASSWORD@cluster.example/db",
        "mongodb+srv://" + "STAGING_USER:STAGING_PASSWORD@cluster.example/db",
        "mongodb+srv://" + "u:p@cluster.example/db",
        "mongodb+srv://" + "user:pass@cluster.example/db",
        "mongodb+srv://" + "isolated-user:isolated-pass@cluster.example/db",
    ]
    target = write(tmp_path, "fixtures.txt", "\n".join(placeholders))
    assert scan_files(tmp_path, [target]) == []

    realistic = "mongodb+srv://" + "service_user:S3cretValue987@cluster.example/db"
    target.write_text(realistic, encoding="utf-8")
    findings = scan_files(tmp_path, [target])
    assert labels(findings) == {"credentialed-mongo-uri"}
    assert realistic not in repr(findings)


def test_blocks_sensitive_credential_filenames_even_without_key_content(tmp_path):
    blocked = [
        write(tmp_path, "ios/AuthKey_SAMPLE.p8", "placeholder"),
        write(tmp_path, "android/google-play-service-account.json", "{}"),
        write(tmp_path, "config/backup-service-account-key.json", "{}"),
    ]
    findings = scan_files(tmp_path, blocked)
    assert len(findings) == 3
    assert labels(findings) == {"sensitive-filename"}
    assert all(finding.line == 0 for finding in findings)
    assert {finding.path for finding in findings} == {
        "ios/AuthKey_SAMPLE.p8",
        "android/google-play-service-account.json",
        "config/backup-service-account-key.json",
    }


def test_does_not_block_generic_public_configuration_filename(tmp_path):
    target = write(tmp_path, "config/service-account.example.json", "{}")
    assert scan_files(tmp_path, [target]) == []
