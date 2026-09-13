import inspect

from rental import autopay_cron
from rental import helcim_vault_router as vault
from rental import payment_processors_core


def test_helcim_callback_accepts_current_direct_signed_envelope():
    tx = {
        "statusAuth": "PENDING",
        "statusClearing": "OPENED",
        "bankToken": "opaque-bank-token",
    }
    parsed, supplied_hash = payment_processors_core._parse_helcim_response(
        {"data": tx, "hash": "signed-response-hash"}
    )
    assert parsed == tx
    assert supplied_hash == "signed-response-hash"


def test_helcim_callback_keeps_legacy_nested_envelope_compatibility():
    tx = {"status": "APPROVED", "cardToken": "opaque-card-token"}
    parsed, supplied_hash = payment_processors_core._parse_helcim_response(
        {"data": {"data": tx, "hash": "signed-response-hash"}}
    )
    assert parsed == tx
    assert supplied_hash == "signed-response-hash"


def test_helcim_callback_rejects_unsigned_or_malformed_envelopes():
    for payload in ({"data": {}}, {"hash": "missing-data"}, [], "not-json"):
        try:
            payment_processors_core._parse_helcim_response(payload)
        except (TypeError, ValueError):
            continue
        raise AssertionError(f"malformed payload accepted: {payload!r}")


def test_payload_items_accepts_current_helcim_response_envelopes():
    rows = [{"id": 7}]
    assert vault._payload_items(rows, "customers") == rows
    assert vault._payload_items({"customers": rows}, "customers") == rows
    assert vault._payload_items({"data": {"bankAccounts": rows}}, "bankAccounts") == rows


def test_provider_tokens_are_fingerprinted_without_exposing_secret():
    fingerprint = vault.provider_token_fingerprint("bank-secret")
    assert len(fingerprint) == 64
    assert "bank-secret" not in fingerprint


def test_saved_method_session_is_method_scoped_and_uses_deep_link_return():
    source = inspect.getsource(vault.save_method_session)
    assert 'method_type not in {"card", "ach"}' in source
    assert '"cc" if method_type == "card" else "ach"' in source
    assert '"setAsDefaultPaymentMethod": 1' in source
    assert 'rossrentals://pay/methods?helcim_session=' in source


def test_ach_debit_uses_current_helcim_api_and_never_raw_bank_fields():
    source = inspect.getsource(vault.helcim_ach_with_account)
    assert '/ach/withdraw' in source
    assert '"currencyId": 2' in source
    assert "bankAccountNumber" not in source
    assert "bankRoutingNumber" not in source


def test_verified_ach_is_tokenized_and_never_persists_raw_account_number():
    source = inspect.getsource(payment_processors_core.helcim_complete)
    ach = source.split('if method_type == "ach":', 1)[1].split("else:", 1)[0]
    assert '"bank_token": encrypt_provider_token(bank_token)' in ach
    assert '"bank_account_id": bank_account_id' in ach
    assert '"ready_for_payments": bool(customer_id and bank_account_id)' in ach
    assert '"bankAccountNumber":' not in ach


def test_ach_autopay_stays_pending_until_settlement():
    source = inspect.getsource(autopay_cron._process_autopay_for_config)
    ach = source.split('if autopay.get("helcim_method_type") == "ach":', 1)[1]
    assert '"last_attempt_status": "ACH_PENDING"' in ach
    assert '"pending": True' in ach
    before_card_completion = ach.split('status_tx = str(tx.get("status", "")).upper()', 1)[0]
    assert '"status": "completed"' not in before_card_completion


def test_admin_vault_response_is_masked_and_excludes_provider_tokens():
    source = inspect.getsource(vault.admin_list_tokenized_methods)
    assert '"last4"' in source
    assert '"security": "helcim_tokenized_masked_only"' in source
    assert '"card_token"' not in source
    assert '"bank_token"' not in source
