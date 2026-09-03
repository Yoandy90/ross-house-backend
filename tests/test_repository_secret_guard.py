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
        "mongodb+srv://" + "user:password@cluster.example/db",
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
