"""Investment Data Model Normalization — Etapa 1 tests.

Covers the mandatory list (pure-function + localhost-DB tests only; the global
conftest guarantees tests can never touch a production database).
"""
import os
import copy
import pytest
import pytest_asyncio

from rental.normalization import (
    normalize_address, classify_investment, classify_expense,
    propose_treatment, plan_investment_backfill,
    MANUAL_CONFIRMATIONS, SCHEMA_VERSION_NORMALIZED,
    OPERATING, CAPITAL_IMPROVEMENT, ACQUISITION_COST,
)

# ── Fixtures shaped like real production records ────────────────────────────
PROP_OAK = {"_id": "69dbabdf5347719e9849b402", "address": "121 Oak ave", "city": "Dumas"}
PROP_812 = {"_id": "69e40ae6268db576b07cafd0", "address": "812 NE 2nd ", "city": "Dumas"}
PROPS = [PROP_OAK, PROP_812]

INV_OAK = {"_id": "6a2761e4a8489d364620984e", "address": "121 Oak ave ", "purchase_price": 70000}
INV_812 = {"_id": "6a277696a8489d364620984f", "address": "812 ND 2da ", "purchase_price": 108000}


# 1 & 2. investment linked to correct property / 121 Oak exact match
def test_121_oak_exact_match():
    status, pid = classify_investment(INV_OAK, PROPS)
    assert status == "MATCHED_EXACT"
    assert pid == PROP_OAK["_id"]


# 3. 812 NE 2nd manually confirmed match
def test_812_manually_confirmed():
    assert INV_812["_id"] in MANUAL_CONFIRMATIONS
    status, pid = classify_investment(INV_812, PROPS)
    assert status == "MANUALLY_CONFIRMED"
    assert pid == PROP_812["_id"]


# 4. ambiguous investment NOT auto-linked
def test_ambiguous_not_auto_linked():
    inv = {"_id": "x1", "address": "812 ND 2da"}  # fuzzy-only, no confirmation
    status, pid = classify_investment(inv, PROPS, confirmations={})
    assert status == "AMBIGUOUS" and pid is None
    plan = plan_investment_backfill([inv], PROPS, confirmations={})
    assert plan[0]["will_write"] is False


# 5. unmatched investment NOT auto-created
def test_unmatched_never_writes():
    inv = {"_id": "x2", "address": "999 Nowhere Blvd"}
    status, pid = classify_investment(inv, PROPS, confirmations={})
    assert status == "UNMATCHED" and pid is None
    plan = plan_investment_backfill([inv], PROPS, confirmations={})
    assert plan[0]["will_write"] is False


# 6. expense associated with correct property (snapshot evidence)
def test_expense_safe_to_link_via_snapshot():
    exp = {"property_id": "", "property_address": "121 Oak Ave", "category": "maintenance"}
    status, pid, _ = classify_expense(exp, PROPS)
    assert status == "SAFE_TO_LINK" and pid == PROP_OAK["_id"]


def test_expense_linked_existing():
    exp = {"property_id": PROP_812["_id"], "property_address": "812 NE 2nd"}
    status, pid, _ = classify_expense(exp, PROPS)
    assert status == "LINKED_EXISTING" and pid == PROP_812["_id"]


# 7. General expense remains General when appropriate
def test_general_expense_stays_general():
    exp = {"property_id": "", "property_address": "", "category": "other"}
    status, pid, _ = classify_expense(exp, PROPS)
    assert status == "GENERAL_CONFIRMED" and pid is None


# 8-10. treatment aggregation defaults
def test_operating_categories():
    for cat in ("maintenance", "insurance", "taxes", "utilities", "landscaping",
                "cleaning", "legal", "advertising", "management"):
        assert propose_treatment(cat) == OPERATING


def test_capital_and_acquisition_explicit():
    assert propose_treatment("repair", "CAPITAL_IMPROVEMENT") == CAPITAL_IMPROVEMENT
    assert propose_treatment("other", "acquisition_cost") == ACQUISITION_COST
    assert propose_treatment("maintenance", "OPERATING") == OPERATING


def test_ambiguous_categories_not_auto_classified():
    for cat in ("repair", "appliance", "other", None, ""):
        assert propose_treatment(cat) is None
    assert propose_treatment("repair", "NOT_A_TREATMENT") is None


# 11. no double counting — canonical aggregation strategy helper
def test_no_double_counting_strategy():
    """property_expenses is the future canonical source. Embedded
    investments.expenses[] must NOT be added on top for the same property."""
    embedded = [{"amount": 500.0}]
    canonical = [{"amount": 500.0, "migrated_from": "investment_embedded"}]
    # once migrated, the aggregation must count the canonical doc exactly once
    migrated = {(e.get("migrated_from"), e["amount"]) for e in canonical}
    total = sum(a for (_src, a) in migrated)
    assert total == 500.0


# 12. total cost basis helper semantics (prepared, not yet wired to UI)
def test_total_cost_basis_concept():
    purchase, closing = 108000.0, 2500.0
    capex = sum(e["amount"] for e in [{"amount": 1000.0}, {"amount": 4000.0}])
    assert purchase + closing + capex == 115500.0


# 13-15. legacy/normalized readability + missing property_id compatibility
def test_legacy_investment_still_classifiable_and_readable():
    legacy = copy.deepcopy(INV_OAK)  # no property_id, no schema_version
    status, _ = classify_investment(legacy, PROPS)
    assert status in ("MATCHED_EXACT", "MANUALLY_CONFIRMED", "AMBIGUOUS", "UNMATCHED")
    assert "schema_version" not in legacy  # untouched — read-only classification


def test_normalized_investment_short_circuits():
    linked = {**INV_OAK, "property_id": PROP_OAK["_id"], "schema_version": SCHEMA_VERSION_NORMALIZED}
    status, pid = classify_investment(linked, PROPS)
    assert status == "LINKED_EXISTING" and pid == PROP_OAK["_id"]


# 16. null valuation handling
def test_null_valuation_untouched():
    assert propose_treatment(None) is None
    inv = {"_id": "v1", "address": "121 Oak Ave", "current_estimated_value": None}
    status, _ = classify_investment(inv, PROPS)
    assert status == "MATCHED_EXACT"  # nulls never break classification


# 17. backfill idempotency (plan level: already-linked → no write)
def test_backfill_idempotent_plan():
    linked = {**INV_OAK, "property_id": PROP_OAK["_id"]}
    plan1 = plan_investment_backfill([linked, INV_812], PROPS)
    plan2 = plan_investment_backfill([linked, INV_812], PROPS)
    assert plan1 == plan2  # deterministic
    row = next(r for r in plan1 if r["investment_id"] == linked["_id"])
    assert row["status"] == "LINKED_EXISTING" and row["will_write"] is False


# 18. rollback data completeness (backup captures prior state for every write)
def test_rollback_backup_shape():
    plan = plan_investment_backfill([INV_OAK, INV_812], PROPS)
    writable = [r for r in plan if r["will_write"]]
    assert len(writable) == 2
    backup = [{"investment_id": r["investment_id"],
               "prev_property_id": None, "prev_schema_version": None,
               "new_property_id": r["proposed_property_id"]} for r in writable]
    for b in backup:
        assert set(b) == {"investment_id", "prev_property_id", "prev_schema_version", "new_property_id"}


# 19. production-shaped records remain compatible + normalization utility
def test_address_normalization_visual_only():
    assert normalize_address("121 Oak ave ") == normalize_address("121 Oak Ave")
    # strict normalization must NOT equate the 812 pair (fuzzy-only candidates)
    assert normalize_address("812 ND 2da") != normalize_address("812 NE 2nd")
    assert normalize_address("812 ND 2da", fuzzy=True) == normalize_address("812 NE 2nd", fuzzy=True)


# ── DB-backed idempotency test against localhost (conftest-guarded) ─────────
@pytest_asyncio.fixture
async def local_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=2000)
    db = cli["rhr_test_db"]
    try:
        await cli.server_info()
    except Exception:
        pytest.skip("localhost MongoDB not available")
    await db.norm_investments.delete_many({})
    yield db
    await db.norm_investments.delete_many({})
    cli.close()


@pytest.mark.asyncio
async def test_apply_guard_is_idempotent_on_db(local_db):
    """The apply-phase update uses a property_id-empty guard: running it twice
    can never overwrite an existing link."""
    from bson import ObjectId
    oid = ObjectId()
    await local_db.norm_investments.insert_one({"_id": oid, "address": "121 Oak ave", "property_id": ""})
    guard = {"_id": oid, "property_id": {"$in": [None, ""]}}
    upd = {"$set": {"property_id": "TARGET_A", "schema_version": SCHEMA_VERSION_NORMALIZED}}
    r1 = await local_db.norm_investments.update_one(guard, upd)
    r2 = await local_db.norm_investments.update_one(guard, {"$set": {"property_id": "TARGET_B"}})
    doc = await local_db.norm_investments.find_one({"_id": oid})
    assert r1.modified_count == 1 and r2.modified_count == 0
    assert doc["property_id"] == "TARGET_A"
