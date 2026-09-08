from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bson import ObjectId

from rental.opportunity_evidence import (
    add_evidence_atomic, address_probe, evidence_detail, evidence_id,
    get_evidence_review_history,
    parse_public_records_response,
    parse_obituary_response,
    validate_radar_results,
    match_address, match_person, obituary_source_allowed, person_tokens,
    match_obituary, obituary_owner_identity, resolve_obituary_candidates,
    resolve_obituary_ambiguity_atomic, serialize_obituary_ambiguities,
    pending_only_active_signals, reconcile_pending_signals_batch,
    review_evidence_atomic, serialize_evidence_review_history,
)


NOW = datetime(2026, 9, 7, 18, tzinfo=timezone.utc)


def test_history_orders_real_instants_and_preserves_invalid_date_events():
    from copy import deepcopy
    evidence = {"review_history": [
        {"status": "confirmed", "at": "2026-09-07T10:00:00-05:00", "note": "latest"},
        {"status": "dismissed", "at": "2026-09-07T14:00:00Z", "note": "earlier"},
        {"status": "needs_review", "at": "invalid", "note": "unknown date"},
        {"status": "confirmed", "at": "2026-09-07T16:00:00", "note": "no timezone"},
    ]}
    original = deepcopy(evidence)
    result = serialize_evidence_review_history(evidence)["history"]
    assert [item["note"] for item in result] == ["latest", "earlier", "no timezone", "unknown date"]
    assert result[0]["at"] == "2026-09-07T15:00:00+00:00"
    assert result[-1]["at"] == "unknown"
    assert all(set(item) == {"status", "at", "note"} for item in result)
    assert evidence == original


def test_history_equal_instants_use_append_order_and_limit_to_50():
    evidence = {"review_history": [
        {"status": "confirmed", "at": "2026-09-07T15:00:00Z", "note": str(i)}
        for i in range(60)
    ]}
    result = serialize_evidence_review_history(evidence)["history"]
    assert len(result) == 50
    assert result[0]["note"] == "59" and result[-1]["note"] == "10"


@pytest.mark.parametrize("events", [None, {}, "invalid", 42, [{"status": []}]])
def test_history_handles_malformed_legacy_containers(events):
    assert serialize_evidence_review_history({"review_history": events})["history"] == []


@pytest.mark.parametrize("payload", [None, [], {}, {"results": None},
    {"results": {}}, {"results": [None]}, {"results": [{"Address": []}]},
    {"results": [{"Owner": {}}]}, {"results": [{"City": 42}]},
    {"results": [{"Address": "123 Main"}, None]}, {"results": [{}] * 201}])
def test_radar_rejects_invalid_batches(payload):
    with pytest.raises(ValueError, match="radar_response_invalid"):
        validate_radar_results(payload)


def test_radar_accepts_explicit_empty_and_valid_preview_rows():
    assert validate_radar_results({"results": []}) == []
    rows = [{"Address": "123 Main", "Owner": None, "RadarID": "123"}]
    assert validate_radar_results({"results": rows}) == rows


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["empty", "invalid", "transport"])
async def test_radar_handler_only_records_valid_empty_success(scenario):
    import ast
    from fastapi import HTTPException
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    handler = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "motivation_scan")
    handler.decorator_list = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            if scenario == "transport":
                raise RuntimeError("private request details")
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"results": []} if scenario == "empty" else {})
    receipt = AsyncMock()
    db = object()
    env = {
        "Request": object, "MotivationScanBody": object, "auth_admin": AsyncMock(),
        "os": SimpleNamespace(environ={"PROPERTYRADAR_API_KEY": "synthetic"}),
        "RADAR_CRITERIA": {"probate": "inProbateProperty"},
        "httpx": SimpleNamespace(AsyncClient=lambda **kwargs: Client()),
        "HTTPException": HTTPException, "get_db": lambda: db,
        "validate_radar_results": validate_radar_results, "record_source_run": receipt,
    }
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    exec(compile(module, "isolated_radar_handler", "exec"), env)
    body = SimpleNamespace(signals=["probate"], county_fips=48341, limit=100)
    if scenario == "empty":
        result = await env["motivation_scan"](object(), body)
        assert result["success"] and result["scanned"] == 0
        receipt.assert_awaited_once_with(db, "propertyradar", scanned=0, matched=0)
    else:
        with pytest.raises(HTTPException) as exc:
            await env["motivation_scan"](object(), body)
        assert exc.value.status_code == 502
        assert "private" not in exc.value.detail
        receipt.assert_not_awaited()


@pytest.mark.parametrize("raw", ['[{"address":"123 Main"}]',
                                  '[{"name":"Jane", "age":true}]',
                                  '[{"name":"Jane", "age":131}]',
                                  '[{"name":"Jane", "city":{}}]', "broken"])
def test_obituary_invalid_extraction_raises(raw):
    with pytest.raises(ValueError, match="obituary_response_invalid"):
        parse_obituary_response(raw)


def test_obituary_valid_empty_and_bounded_records():
    import json
    assert parse_obituary_response("```json\n[]\n```") == []
    records = [{"name": "Jane Doe", "age": 74, "city": "Dumas", "date": None,
                "unexpected": "omit"}] * 60
    result = parse_obituary_response(json.dumps(records))
    assert len(result) == 50
    assert result[0] == {"name": "Jane Doe", "age": 74, "city": "Dumas", "date": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["missing_key", "provider_error", "invalid", "empty"])
async def test_obituary_extractor_propagates_sanitized_failures(scenario):
    import ast
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    handler = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "_llm_extract_obituaries")
    class RemoveImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None
        def visit_Import(self, node):
            return None
    handler = RemoveImports().visit(handler)
    chat = SimpleNamespace(send_message=AsyncMock(
        return_value="[]" if scenario == "empty" else "broken"))
    if scenario == "provider_error":
        chat.send_message.side_effect = RuntimeError("private provider detail")
    chat.with_model = lambda *args: chat
    logger = SimpleNamespace(warning=lambda *args: None)
    env = {
        "os": SimpleNamespace(environ={} if scenario == "missing_key" else {"EMERGENT_LLM_KEY": "fake"}),
        "secrets": SimpleNamespace(token_hex=lambda n: "fake"),
        "LlmChat": lambda **kwargs: chat, "UserMessage": lambda **kwargs: kwargs,
        "logger": logger, "parse_obituary_response": parse_obituary_response,
    }
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    exec(compile(module, "isolated_obituary_extractor", "exec"), env)
    if scenario == "empty":
        assert await env["_llm_extract_obituaries"]("synthetic", "https://example.test") == []
    else:
        with pytest.raises(RuntimeError) as exc:
            await env["_llm_extract_obituaries"]("synthetic", "https://example.test")
        assert str(exc.value) == ("obituary_extraction_not_configured" if scenario == "missing_key"
                                  else "obituary_extraction_failed")
        if scenario == "missing_key":
            chat.send_message.assert_not_awaited()


@pytest.mark.parametrize("raw", ["[]", "```json\n[]\n```", "```\n[]\n```"])
def test_public_records_valid_empty_extraction(raw):
    assert parse_public_records_response(raw) == []


@pytest.mark.parametrize("raw", [None, "", "unavailable", "[", "{}", "null",
                                  '[null]', '["name"]', '[{}]',
                                  '[{"name": ["Jane"]}]',
                                  '[{"name": "Jane", "date": 42}]',
                                  '[{"name": "Jane"}, null]'])
def test_public_records_malformed_extraction_is_never_empty_success(raw):
    with pytest.raises(ValueError, match="public_records_response_invalid"):
        parse_public_records_response(raw)


def test_public_records_keeps_names_addresses_and_bounds_batch():
    import json
    records = [{"name": "Jane Doe", "address": None, "case_number": "P-1"},
               {"name": None, "address": "123 Main St"}]
    assert parse_public_records_response(json.dumps(records)) == records
    assert len(parse_public_records_response(json.dumps(records * 150))) == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected_status", [("[]", 200), ("invalid", 502)])
async def test_public_import_empty_or_invalid_response_receipts(raw, expected_status):
    # Execute the real handler with isolated provider/auth/storage dependencies.
    import ast
    from fastapi import HTTPException
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    handler = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "public_records_import")
    handler.decorator_list = []
    handler.body = [node for node in handler.body
                    if not isinstance(node, (ast.Import, ast.ImportFrom))]
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    chat = SimpleNamespace(send_message=AsyncMock(return_value=raw))
    chat.with_model = lambda *args: chat
    receipt = AsyncMock()
    db = object()
    env = {
        "Request": object, "PublicRecordsImport": object,
        "auth_admin": AsyncMock(), "PUBLIC_SIGNAL_BY_TYPE": {"probate": "probate_confirmed"},
        "os": SimpleNamespace(environ={"EMERGENT_LLM_KEY": "synthetic"}),
        "secrets": SimpleNamespace(token_hex=lambda n: "synthetic"),
        "LlmChat": lambda **kwargs: chat, "UserMessage": lambda **kwargs: kwargs,
        "HTTPException": HTTPException, "get_db": lambda: db,
        "parse_public_records_response": parse_public_records_response,
        "record_source_run": receipt,
    }
    exec(compile(module, "isolated_public_import", "exec"), env)
    body = SimpleNamespace(source_type="probate", text="Synthetic county index text")
    if expected_status == 502:
        with pytest.raises(HTTPException) as exc:
            await env["public_records_import"](object(), body)
        assert exc.value.status_code == 502
        receipt.assert_not_awaited()
    else:
        result = await env["public_records_import"](object(), body)
        assert result["success"] and result["records_found"] == 0
        receipt.assert_awaited_once_with(db, "probate", scanned=0, matched=0)


def test_person_match_is_order_independent_and_accent_safe():
    match = match_person("José Antonio García", "GARCIA, JOSE ANTONIO ESTATE")
    assert match == {"matched": True, "confidence": 100,
                     "reasons": ["exact_name_tokens"]}
    assert person_tokens("GARCÍA, JOSÉ JR.") == ["GARCIA", "JOSE"]


@pytest.mark.parametrize("record,owner,reason", [
    ("John Smith", "Smith Holdings LLC", "entity_owner"),
    ("John Smith", "Smith Mary", "name_anchors_mismatch"),
    ("Madonna", "Madonna Estate", "insufficient_name_tokens"),
])
def test_person_match_rejects_unsafe_false_positives(record, owner, reason):
    result = match_person(record, owner)
    assert result["matched"] is False
    assert result["reasons"] == [reason]


def obituary_lead(address="305 Bruce Ave, Dumas TX", mailing="PO Box 1",
                  mailing_city="Dumas"):
    return {"owner_name": "DOE JANE", "address": address,
            "mailing_lines": [mailing], "mailing_city": mailing_city,
            "mailing_state": "TX", "mailing_zip": "79029"}


def test_obituary_match_requires_city_when_both_records_have_location():
    matched = match_obituary({"name": "Jane Doe", "city": "Dumas, Texas"}, obituary_lead())
    assert matched["matched"] is True and "city_match" in matched["reasons"]
    mismatch = match_obituary({"name": "Jane Doe", "city": "Amarillo"}, obituary_lead())
    assert mismatch == {"matched": False, "confidence": 0,
                        "reasons": ["exact_name_tokens", "city_mismatch"]}


def test_obituary_without_location_stays_reviewable_at_lower_confidence():
    matched = match_obituary({"name": "Jane Doe", "city": ""}, obituary_lead())
    assert matched["matched"] is True
    assert matched["confidence"] == 90
    assert matched["reasons"][-1] == "city_unverified"


def test_obituary_resolution_preserves_one_owner_portfolio():
    leads = [obituary_lead(address="305 Bruce Ave, Dumas TX"),
             obituary_lead(address="307 Bruce Ave, Dumas TX")]
    result = resolve_obituary_candidates({"name": "Jane Doe", "city": "Dumas"}, leads)
    assert result["ambiguous"] is False
    assert len(result["matches"]) == 2
    assert obituary_owner_identity(leads[0]) == obituary_owner_identity(leads[1])


def test_obituary_resolution_quarantines_distinct_same_name_owners():
    leads = [obituary_lead(mailing="PO Box 1"), obituary_lead(mailing="PO Box 999")]
    result = resolve_obituary_candidates({"name": "Jane Doe", "city": "Dumas"}, leads)
    assert result["matches"] == []
    assert result["ambiguous"] is True and result["candidate_count"] == 2
    assert result["candidate_leads"] == leads


def test_obituary_identity_keeps_generational_suffixes_distinct():
    junior = {**obituary_lead(), "owner_name": "DOE JANE JR"}
    senior = {**obituary_lead(), "owner_name": "DOE JANE SR"}
    assert obituary_owner_identity(junior) != obituary_owner_identity(senior)
    result = resolve_obituary_candidates({"name": "Jane Doe", "city": "Dumas"},
                                         [junior, senior])
    assert result["ambiguous"] is True and result["candidate_count"] == 2


def test_obituary_ambiguity_serializer_bounds_and_hides_claim_internals():
    items = [{
        "ambiguity_id": "a" * 24, "status": "resolved",
        "obituary": {"name": "Jane Doe", "city": "Dumas", "age": 150,
                     "source_url": "https://evil.example/private"},
        "candidate_count": 2,
        "candidates": [{"lead_id": f"{i:024x}", "address": "A" * 250,
                        "owner_name": "DOE JANE"} for i in range(55)],
        "candidate_addresses": ["B" * 250] * 7,
        "resolved_lead_id": "1" * 24, "resolved_at": "2026-09-08T14:00:00Z",
        "resolved_by": "private-admin", "claim_token": "private-claim",
    }] + ["invalid"] * 101
    result = serialize_obituary_ambiguities(items)
    assert len(result) == 1
    assert len(result[0]["candidates"]) == 50
    assert len(result[0]["candidate_addresses"]) == 5
    assert len(result[0]["candidates"][0]["address"]) == 200
    assert result[0]["obituary"]["age"] is None
    assert result[0]["obituary"]["source_url"] == ""
    assert "private" not in str(result)


def ambiguity_database(*, claim=True, matching=True):
    lead_id = ObjectId("1" * 24)
    admin_config = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=1)))

    async def claim_one(query, update, **kwargs):
        if not claim:
            return None
        token = update["$set"]["last_obituary_ambiguities.$[item].claim_token"]
        return {"last_obituary_ambiguities": [{
            "ambiguity_id": "a" * 24, "status": "resolving",
            "claim_token": token,
            "obituary": {"name": "Jane Doe", "city": "Dumas",
                         "source_url": "https://echovita.com/example"},
        }]}

    admin_config.find_one_and_update = AsyncMock(side_effect=claim_one)
    leads = SimpleNamespace(
        find_one=AsyncMock(return_value={
            "_id": lead_id, "owner_name": "DOE JANE" if matching else "SMITH JOHN",
            "address": "305 Bruce Ave, Dumas TX", "mailing_city": "Dumas",
            "mailing_lines": ["PO Box 1"], "mailing_state": "TX", "mailing_zip": "79029",
        }),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
    )
    return SimpleNamespace(admin_config=admin_config, deal_finder_leads=leads), lead_id


@pytest.mark.asyncio
async def test_manual_obituary_resolution_claims_revalidates_and_finalizes_once():
    db, lead_id = ambiguity_database()
    result = await resolve_obituary_ambiguity_atomic(
        db, "a" * 24, lead_id, "admin-7", now=NOW)
    assert result["created"] is True and result["lead_id"] == str(lead_id)
    assert result["evidence"]["match_reasons"][-1] == "manual_identity_resolution"
    claim_query = db.admin_config.find_one_and_update.await_args.args[0]
    elem = claim_query["last_obituary_ambiguities"]["$elemMatch"]
    assert elem["$or"][0] == {"status": "pending"}
    assert elem["$or"][1]["claimed_lead_id"] == str(lead_id)
    assert elem["candidates"]["$elemMatch"]["lead_id"] == str(lead_id)
    assert db.deal_finder_leads.find_one.await_count == 1
    assert db.deal_finder_leads.update_one.await_count == 1
    final_update = db.admin_config.update_one.await_args.args[1]
    assert final_update["$set"]["last_obituary_ambiguities.$[item].status"] == "resolved"
    assert final_update["$set"]["last_obituary_ambiguities.$[item].resolved_by"] == "admin-7"


@pytest.mark.asyncio
async def test_manual_obituary_resolution_conflict_never_touches_lead():
    db, lead_id = ambiguity_database(claim=False)
    assert await resolve_obituary_ambiguity_atomic(
        db, "a" * 24, lead_id, "admin-7", now=NOW) is None
    db.deal_finder_leads.find_one.assert_not_awaited()
    db.deal_finder_leads.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_obituary_resolution_releases_claim_if_owner_changed():
    db, lead_id = ambiguity_database(matching=False)
    with pytest.raises(ValueError, match="candidate_no_longer_matches"):
        await resolve_obituary_ambiguity_atomic(
            db, "a" * 24, lead_id, "admin-7", now=NOW)
    db.deal_finder_leads.update_one.assert_not_awaited()
    release = db.admin_config.update_one.await_args.args[1]
    assert release["$set"]["last_obituary_ambiguities.$[item].status"] == "pending"


@pytest.mark.asyncio
async def test_manual_obituary_resolution_keeps_claim_closed_after_evidence_write():
    db, lead_id = ambiguity_database()
    db.admin_config.update_one.return_value = SimpleNamespace(modified_count=0)
    with pytest.raises(RuntimeError, match="finalize_failed"):
        await resolve_obituary_ambiguity_atomic(
            db, "a" * 24, lead_id, "admin-7", now=NOW)
    assert db.deal_finder_leads.update_one.await_count == 1
    assert db.admin_config.update_one.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguity_id,lead_id", [("bad", ObjectId("1" * 24)),
                                                    ("a" * 24, "bad")])
async def test_manual_obituary_resolution_rejects_ids_before_database_access(
        ambiguity_id, lead_id):
    db, _ = ambiguity_database()
    with pytest.raises(ValueError, match="_id_invalid"):
        await resolve_obituary_ambiguity_atomic(
            db, ambiguity_id, lead_id, "admin-7", now=NOW)
    db.admin_config.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_obituary_results_exposes_only_bounded_ambiguity_records():
    import ast
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    handler = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "obituary_results")
    handler.decorator_list = []
    records = [{"candidate_count": index + 2,
                "obituary": {"name": f"Person {index}"}}
               for index in range(105)] + ["invalid"]
    collection = SimpleNamespace(find_one=AsyncMock(return_value={
        "last_obituary_ambiguities": records,
    }))
    env = {
        "Request": object, "auth_admin": AsyncMock(),
        "get_db": lambda: SimpleNamespace(admin_config=collection),
        "serialize_obituary_ambiguities": serialize_obituary_ambiguities,
    }
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    exec(compile(module, "isolated_obituary_results", "exec"), env)
    result = await env["obituary_results"](object())
    assert len(result["ambiguities"]) == 100
    assert result["ambiguities"][0]["candidate_count"] == 2
    assert result["ambiguities"][-1]["candidate_count"] == 101


def test_address_match_requires_house_number_and_real_street_token():
    assert address_probe("305 N Bruce Avenue, Dumas TX") == ("305", "BRUCE")
    assert match_address("305 N Bruce Avenue", "305 BRUCE AVE, DUMAS TX")["matched"] is True
    assert match_address("305 N Bruce Avenue", "306 BRUCE AVE")["matched"] is False
    assert match_address("305 N Bruce Avenue", "305 OAK AVE")["matched"] is False


@pytest.mark.parametrize("url,allowed", [
    ("https://www.echovita.com/us/obituaries/tx/dumas", True),
    ("https://www.morrisonfuneraldirectors.com/obituaries", True),
    ("http://www.echovita.com/us/obituaries/tx/dumas", False),
    ("https://echovita.com.evil.example/", False),
    ("https://user:pass@echovita.com/", False),
    ("https://echovita.com:not-a-port/", False),
    ("https://127.0.0.1/internal", False),
    ("file:///etc/passwd", False),
])
def test_obituary_sources_are_https_allowlisted(url, allowed):
    assert obituary_source_allowed(url) is allowed


def test_evidence_id_is_deterministic_and_uses_case_number_when_available():
    first = evidence_id("probate", {"case_number": "PR-123", "name": "One"})
    second = evidence_id("probate", {"name": "Changed", "case_number": "PR-123"})
    assert first == second
    assert len(first) == 24
    assert first != evidence_id("divorce", {"case_number": "PR-123"})


def test_evidence_detail_is_reviewable_bounded_and_does_not_echo_unsafe_url():
    detail = evidence_detail(
        "obituary", {"name": "A" * 500, "date": "2026-09-01"},
        {"confidence": 92, "reasons": ["first_last_tokens"]},
        source_url="https://169.254.169.254/latest/meta-data", now=NOW,
    )
    assert len(detail["record_name"]) == 300
    assert detail["source_url"] == ""
    assert detail["review_status"] == "needs_review"
    assert detail["at"] == "2026-09-07T18:00:00+00:00"


def test_evidence_detail_rejects_naive_clock():
    with pytest.raises(ValueError, match="clock_must_be_aware"):
        evidence_detail("probate", {"case_number": "1"},
                        {"confidence": 98, "reasons": []},
                        now=datetime(2026, 9, 7))


@pytest.mark.asyncio
async def test_atomic_add_dedupes_pending_evidence_without_activating_signals():
    collection = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=1)))
    db = SimpleNamespace(deal_finder_leads=collection)
    detail = evidence_detail(
        "propertyradar", {"case_number": "radar-7", "address": "305 Bruce"},
        {"confidence": 98, "reasons": ["house_number", "street_token"]}, now=NOW,
    )
    assert await add_evidence_atomic(
        db, "lead-1", ["probate", "preforeclosure", "probate"], detail) is True
    query, pipeline = collection.update_one.await_args.args
    assert query == {"_id": "lead-1", "motivation.details.evidence_id": {
        "$ne": detail["evidence_id"]}}
    assert pipeline[0]["$set"]["motivation"]["$cond"][2] == {}
    fields = pipeline[1]["$set"]
    assert fields["motivation.signals"]["$setUnion"][1] == []
    saved_detail = fields["motivation.details"]["$concatArrays"][1][0]
    assert saved_detail == {**detail, "signals": ["probate", "preforeclosure"]}
    assert fields["motivation.updated_at"] == detail["at"]


@pytest.mark.asyncio
async def test_atomic_add_reports_existing_evidence_without_overwrite():
    collection = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=0)))
    db = SimpleNamespace(deal_finder_leads=collection)
    assert await add_evidence_atomic(
        db, "lead-1", "possible_deceased", {"evidence_id": "same", "at": "now"}) is False


@pytest.mark.asyncio
async def test_atomic_add_can_activate_explicitly_preconfirmed_evidence():
    collection = SimpleNamespace(update_one=AsyncMock(
        return_value=SimpleNamespace(modified_count=1)))
    db = SimpleNamespace(deal_finder_leads=collection)
    detail = {"evidence_id": "a" * 24, "at": NOW.isoformat(),
              "review_status": "confirmed"}
    assert await add_evidence_atomic(db, "lead-1", "probate_confirmed", detail)
    fields = collection.update_one.await_args.args[1][1]["$set"]
    assert fields["motivation.signals"]["$setUnion"][1] == ["probate_confirmed"]


def review_database(status="confirmed"):
    evidence = {
        "evidence_id": "a" * 24, "signals": ["possible_deceased"],
        "review_status": status,
    }
    collection = SimpleNamespace(
        find_one_and_update=AsyncMock(return_value={
            "motivation": {"details": [evidence], "signals": ["possible_deceased"]}}),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
        find_one=AsyncMock(return_value={
            "motivation": {"details": [evidence], "signals": ["possible_deceased"]}}),
    )
    return SimpleNamespace(deal_finder_leads=collection), collection


@pytest.mark.asyncio
async def test_confirm_review_is_audited_bounded_and_restores_signal():
    db, collection = review_database("confirmed")
    result = await review_evidence_atomic(
        db, "lead-1", "a" * 24, "confirmed", "admin-7",
        note="Confirmed in county file", now=NOW)
    assert result["evidence"]["review_status"] == "confirmed"
    query, update = collection.find_one_and_update.await_args.args
    assert query["motivation.details"]["$elemMatch"] == {"evidence_id": "a" * 24}
    history = update["$push"]["motivation.details.$[evidence].review_history"]
    assert history["$slice"] == -50
    assert history["$each"][0] == {
        "status": "confirmed", "reviewer_id": "admin-7",
        "note": "Confirmed in county file", "at": "2026-09-07T18:00:00+00:00",
    }
    assert collection.find_one_and_update.await_args.kwargs["array_filters"] == [
        {"evidence.evidence_id": "a" * 24}]
    restore_query, restore = collection.update_one.await_args.args
    assert restore_query["motivation.details"]["$elemMatch"] == {
        "evidence_id": "a" * 24, "review_status": "confirmed"}
    assert restore == {"$addToSet": {"motivation.signals": {
        "$each": ["possible_deceased"]}}}


@pytest.mark.asyncio
async def test_dismiss_review_only_pulls_signal_without_active_or_legacy_support():
    db, collection = review_database("dismissed")
    await review_evidence_atomic(
        db, "lead-1", "a" * 24, "dismissed", "admin-7", now=NOW)
    query, update = collection.update_one.await_args.args
    assert query["_id"] == "lead-1"
    assert len(query["$and"]) == 2
    assert query["$and"][0]["motivation.details"]["$not"]["$elemMatch"][
        "review_status"] == "confirmed"
    assert update == {"$pull": {"motivation.signals": "possible_deceased"}}


@pytest.mark.asyncio
async def test_reopen_review_keeps_signal_inactive_until_confirmed():
    db, collection = review_database("needs_review")
    await review_evidence_atomic(
        db, "lead-1", "a" * 24, "needs_review", "admin-7", now=NOW)
    query, update = collection.update_one.await_args.args
    assert query["$and"][0]["motivation.details"]["$not"]["$elemMatch"] == {
        "signals": "possible_deceased", "review_status": "confirmed"}
    assert update == {"$pull": {"motivation.signals": "possible_deceased"}}


def test_pending_signal_reconciliation_preserves_confirmed_and_legacy_support():
    document = {"motivation": {
        "signals": ["possible_deceased", "probate_confirmed", "eviction_filed", "manual"],
        "details": [
            {"source": "obituary", "signals": ["possible_deceased"],
             "review_status": "needs_review"},
            {"source": "probate"},
            {"source": "eviction", "signals": ["eviction_filed"],
             "review_status": "confirmed"},
        ],
    }}
    assert pending_only_active_signals(document) == ["possible_deceased"]


@pytest.mark.parametrize("motivation", [None, [], {"signals": "probate"},
                                          {"signals": ["probate"], "details": {}}])
def test_pending_signal_reconciliation_ignores_malformed_legacy_containers(motivation):
    assert pending_only_active_signals({"motivation": motivation}) == []


class AsyncDocuments:
    def __init__(self, documents):
        self.documents = documents
    def sort(self, *args):
        return self
    def limit(self, limit):
        self.documents = self.documents[:limit]
        return self
    def __aiter__(self):
        self.iterator = iter(self.documents)
        return self
    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_pending_signal_reconciliation_previews_without_writes():
    collection = SimpleNamespace(
        find=lambda *args: AsyncDocuments([{
            "_id": ObjectId("1" * 24), "motivation": {
                "signals": ["possible_deceased"],
                "details": [{"signals": ["possible_deceased"],
                             "review_status": "needs_review"}],
            }}]),
        update_one=AsyncMock(),
    )
    result = await reconcile_pending_signals_batch(
        SimpleNamespace(deal_finder_leads=collection), limit=10)
    assert result == {"mode": "preview", "scanned": 1, "affected": 1,
                      "removed": 0, "items": [{"lead_id": "1" * 24,
                      "candidate_signals": ["possible_deceased"],
                      "removed_signals": []}], "has_more": False,
                      "next_after_lead_id": None, "reconciliation_id": None}
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_signal_reconciliation_apply_rechecks_confirmation_atomically():
    document = {"_id": ObjectId("1" * 24), "motivation": {
        "signals": ["possible_deceased"],
        "details": [{"signals": ["possible_deceased"],
                     "review_status": "needs_review"}],
    }}
    collection = SimpleNamespace(
        find=lambda *args: AsyncDocuments([document]),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
    )
    result = await reconcile_pending_signals_batch(
        SimpleNamespace(deal_finder_leads=collection), limit=10, apply=True,
        actor_id="admin-7", note="remove unsupported legacy signal", now=NOW)
    assert result["mode"] == "applied" and result["removed"] == 1
    query, update = collection.update_one.await_args.args
    assert query["$and"][0]["motivation.details"]["$not"]["$elemMatch"] == {
        "signals": "possible_deceased", "review_status": "confirmed"}
    assert update["$pull"] == {"motivation.signals": "possible_deceased"}
    event = update["$push"]["motivation.reconciliation_history"]["$each"][0]
    assert event == {
        "reconciliation_id": result["reconciliation_id"],
        "signal": "possible_deceased",
        "removed_at": NOW.isoformat(),
        "removed_by": "admin-7",
        "note": "remove unsupported legacy signal",
    }
    assert update["$push"]["motivation.reconciliation_history"]["$slice"] == -50


@pytest.mark.asyncio
async def test_pending_signal_reconciliation_reports_concurrent_confirmation_as_not_removed():
    document = {"_id": ObjectId("1" * 24), "motivation": {
        "signals": ["possible_deceased"],
        "details": [{"signals": ["possible_deceased"],
                     "review_status": "needs_review"}],
    }}
    collection = SimpleNamespace(
        find=lambda *args: AsyncDocuments([document]),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=0)),
    )
    result = await reconcile_pending_signals_batch(
        SimpleNamespace(deal_finder_leads=collection), limit=10, apply=True,
        actor_id="admin-7", note="remove unsupported legacy signal", now=NOW)
    assert result["removed"] == 0
    assert result["items"][0]["removed_signals"] == []


@pytest.mark.parametrize("actor,note,error", [
    ("", "reason", "actor_invalid"),
    ("admin-7", "", "note_invalid"),
    ("admin-7", "x" * 501, "note_invalid"),
])
@pytest.mark.asyncio
async def test_pending_signal_reconciliation_requires_auditable_apply(
        actor, note, error):
    collection = SimpleNamespace(find=Mock())
    with pytest.raises(ValueError, match=error):
        await reconcile_pending_signals_batch(
            SimpleNamespace(deal_finder_leads=collection), apply=True,
            actor_id=actor, note=note)
    collection.find.assert_not_called()


@pytest.mark.asyncio
async def test_pending_signal_reconciliation_returns_stable_next_cursor():
    documents = [
        {"_id": ObjectId("1" * 24), "motivation": {
            "signals": ["possible_deceased"],
            "details": [{"signals": ["possible_deceased"],
                         "review_status": "needs_review"}]}},
        {"_id": ObjectId("2" * 24), "motivation": {
            "signals": ["probate_confirmed"],
            "details": [{"signals": ["probate_confirmed"],
                         "review_status": "needs_review"}]}},
    ]
    collection = SimpleNamespace(
        find=Mock(return_value=AsyncDocuments(documents)),
        update_one=AsyncMock(),
    )
    result = await reconcile_pending_signals_batch(
        SimpleNamespace(deal_finder_leads=collection), limit=1)
    assert result["scanned"] == 1
    assert result["has_more"] is True
    assert result["next_after_lead_id"] == "1" * 24


@pytest.mark.asyncio
async def test_pending_signal_reconciliation_applies_cursor_to_query():
    collection = SimpleNamespace(
        find=Mock(return_value=AsyncDocuments([])),
        update_one=AsyncMock(),
    )
    result = await reconcile_pending_signals_batch(
        SimpleNamespace(deal_finder_leads=collection),
        limit=10, after_lead_id="2" * 24)
    query = collection.find.call_args.args[0]
    assert query["_id"] == {"$gt": ObjectId("2" * 24)}
    assert result["has_more"] is False
    assert result["next_after_lead_id"] is None


@pytest.mark.asyncio
async def test_pending_signal_reconciliation_rejects_invalid_cursor():
    collection = SimpleNamespace(find=Mock())
    with pytest.raises(ValueError, match="reconciliation_cursor_invalid"):
        await reconcile_pending_signals_batch(
            SimpleNamespace(deal_finder_leads=collection),
            after_lead_id="not-an-object-id")
    collection.find.assert_not_called()


@pytest.mark.parametrize("limit", [0, 501])
@pytest.mark.asyncio
async def test_pending_signal_reconciliation_rejects_unbounded_limits(limit):
    collection = SimpleNamespace(find=Mock())
    with pytest.raises(ValueError, match="reconciliation_limit_invalid"):
        await reconcile_pending_signals_batch(
            SimpleNamespace(deal_finder_leads=collection), limit=limit)
    collection.find.assert_not_called()


@pytest.mark.parametrize("evidence,status", [
    ("bad", "confirmed"), ("a" * 24, "approved"),
])
@pytest.mark.asyncio
async def test_review_rejects_invalid_inputs_before_database_access(evidence, status):
    db, collection = review_database()
    with pytest.raises(ValueError):
        await review_evidence_atomic(
            db, "lead-1", evidence, status, "admin-7", now=NOW)
    collection.find_one_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_returns_none_when_evidence_does_not_exist():
    db, collection = review_database()
    collection.find_one_and_update.return_value = None
    assert await review_evidence_atomic(
        db, "lead-1", "a" * 24, "confirmed", "admin-7", now=NOW) is None
    collection.update_one.assert_not_awaited()


def test_history_serialization_is_latest_first_bounded_and_private():
    evidence = {
        "evidence_id": "a" * 24,
        "review_status": "confirmed",
        "reviewed_at": "2026-09-07T18:00:00+00:00",
        "review_note": "  current note  ",
        "review_history": [
            {"status": "dismissed", "reviewer_id": "private-admin",
             "note": "old", "at": "2026-09-06T18:00:00+00:00"},
            {"status": "confirmed", "reviewer_id": "private-admin",
             "note": "  new  ", "at": "2026-09-07T18:00:00+00:00"},
            {"status": "invalid", "reviewer_id": "leak",
             "note": "ignored", "at": "2026-09-08T18:00:00+00:00"},
        ],
    }
    result = serialize_evidence_review_history(evidence)
    assert result["current_status"] == "confirmed"
    assert result["review_note"] == "current note"
    assert [event["status"] for event in result["history"]] == [
        "confirmed", "dismissed"]
    assert result["history"][0]["note"] == "new"
    assert "reviewer_id" not in str(result)


@pytest.mark.asyncio
async def test_history_fetch_uses_narrow_projection_and_handles_missing():
    evidence = {"evidence_id": "a" * 24, "review_history": []}
    collection = SimpleNamespace(find_one=AsyncMock(return_value={
        "motivation": {"details": [evidence]}}))
    db = SimpleNamespace(deal_finder_leads=collection)
    result = await get_evidence_review_history(db, "lead-1", "a" * 24)
    assert result["evidence_id"] == "a" * 24
    query, projection = collection.find_one.await_args.args
    expected = {"$elemMatch": {"evidence_id": "a" * 24}}
    assert query["motivation.details"] == expected
    assert projection == {"_id": 0, "motivation.details": expected}
    collection.find_one.return_value = None
    assert await get_evidence_review_history(
        db, "lead-1", "a" * 24) is None


@pytest.mark.asyncio
async def test_history_fetch_rejects_invalid_evidence_before_database_access():
    collection = SimpleNamespace(find_one=AsyncMock())
    db = SimpleNamespace(deal_finder_leads=collection)
    with pytest.raises(ValueError, match="opportunity_evidence_id_invalid"):
        await get_evidence_review_history(db, "lead-1", "bad")
    collection.find_one.assert_not_awaited()


def test_all_opportunity_sources_use_verified_atomic_evidence_boundary():
    source = (Path(__file__).resolve().parents[1] /
              "rental/contact_enrichment_router.py").read_text()
    assert source.count("add_evidence_atomic(") == 3
    assert "source_not_allowlisted" in source
    assert "resolve_obituary_candidates(o, candidates)" in source
    assert ").limit(50)" in source
    assert '"ambiguity_id": ambiguity_id' in source
    assert '"resolving" if preserve_claim else "pending"' in source
    assert '("claim_token", "claimed_at", "claimed_lead_id")' in source
    assert '"lead_id": str(lead.get("_id"))' in source
    assert '@router.post("/admin/deal-finder/obituary-ambiguities/{ambiguity_id}/resolve")' in source
    assert "resolve_obituary_ambiguity_atomic(" in source
    assert "serialize_obituary_ambiguities(" in source
    assert '@router.post("/admin/deal-finder/evidence/reconcile-signals")' in source
    assert "reconcile_pending_signals_batch(" in source
    assert "_find_verified_address_lead(db, addr)" in source
    assert ").limit(10)" in source
    assert '{"$set": {"motivation": motivation}}' not in source
    assert '"review_status": detail["review_status"]' in source
    assert '@router.patch("/admin/deal-finder/leads/{lead_id}/evidence/{evidence_id_value}")' in source
    assert '@router.get("/admin/deal-finder/leads/{lead_id}/evidence/{evidence_id_value}/history")' in source
    assert "get_evidence_review_history(" in source
    assert "review_evidence_atomic(" in source
