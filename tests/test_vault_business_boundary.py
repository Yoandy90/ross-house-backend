from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "rental/vault_router.py").read_text()


def test_foreign_legacy_decryption_and_plaintext_fallback_are_removed():
    assert "LEGACY_ENCRYPTION_KEY" not in SOURCE
    assert "decrypt_legacy" not in SOURCE
    assert 'pm.get("routing_number")' not in SOURCE
    assert 'pm.get("account_number")' not in SOURCE
    assert "previous Ross Tax / Loans system" not in SOURCE


def test_legacy_records_are_hidden_and_immutable_through_vault_routes():
    assert "if _is_legacy_payment_method(pm):\n            continue" in SOURCE
    assert SOURCE.count("legacy_payment_method_blocked_by_business_boundary") == 2
    assert '"routing_number", "account_number", "encrypted_number"' in SOURCE


def test_current_encrypted_ross_house_format_remains_supported():
    assert 'pm.get("routing_encrypted")' in SOURCE
    assert 'pm.get("account_encrypted")' in SOURCE
    assert '"routing_encrypted": encrypt(routing)' in SOURCE
    assert '"account_encrypted": encrypt(account)' in SOURCE
