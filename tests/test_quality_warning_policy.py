"""Quality warnings never remove authority, amount, pricing or duplicate guards."""
import copy
import json

import pytest
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.session_review import account_snapshot, WARNING_FLAGS
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.session_closure import reviewed_snapshot
from test_budget import no_network
from test_session_review import session
from test_auto_credit import ready as credit_ready
from test_ongoing_credit import setup as ongoing_ready
from test_collection import ready as collection_ready, claim_data, payment
from test_session_closure import closed

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("flag", sorted(WARNING_FLAGS))
async def test_warning_is_retained_without_changing_amount_or_source(tmp_path, flag):
    record = session("-1.185000639380682572568430236")
    record["quality_flags"] = [flag]
    original = copy.deepcopy(record)
    result = account_snapshot(record)
    assert result["quality_flags"] == [flag]
    assert result["net_amount_aud"] == "-1.19" and record == original


@pytest.mark.parametrize("flag", ["history_starts_mid_session", "import:missing_counter_baseline",
    "export:counter_decrease_quarantined", "meter_reset", "unknown_running_state", "future_unknown_flag"])
async def test_incomplete_or_unknown_quality_is_not_silently_accepted(tmp_path, flag):
    record = session()
    record["quality_flags"] = ["export:energy_without_matching_state", flag]
    with pytest.raises(WalletError, match="quality"):
        account_snapshot(record)


@pytest.mark.parametrize("change", [
    {"unpriced_export_wh": "1"}, {"estimated_rate_import_wh": "1"},
    {"net_cost_aud_unrounded": None}, {"net_cost_aud_unrounded": "NaN"},
    {"import_kwh": -1}, {"export_kwh": "NaN"}, {"import_kwh": None},
    {"ended_at": None},
])
async def test_warning_does_not_bypass_required_account_data(tmp_path, change):
    record = session() | {"quality_flags": ["import:energy_without_matching_state"]} | change
    with pytest.raises(WalletError):
        account_snapshot(record)


@pytest.mark.parametrize("ongoing", [False, True])
async def test_credit_with_warning_is_paid_once_and_warning_is_visible(tmp_path, ongoing):
    _, api, proxy, row, _, _ = await (ongoing_ready(tmp_path) if ongoing else credit_ready(tmp_path))
    flags = ["export:energy_without_matching_state", "interval_energy_allocation_estimated"]
    proxy.data["latest_session"]["quality_flags"] = flags
    runner = api.ongoing_credits if ongoing else api.auto_credits
    await runner.tick()
    assert len(api.chain.posts) == 1
    item = next(iter(api.saved["automatic_credits"].values()))
    assert item["account"]["quality_flags"] == flags and item["amount_sats"] == 189
    assert api.auto_credits.public(item)["quality_flags"] == flags
    await runner.tick()
    assert len(api.chain.posts) == 1  # Never a second payment for the warning.
    assert item["state"] == "provider_confirmed"


async def test_driver_collection_with_warning_keeps_signed_quote_and_duplicate_guard(tmp_path):
    _, api, proxy, row, driver = await collection_ready(tmp_path)
    flags = ["import:energy_without_matching_state"]
    proxy.data["latest_session"]["quality_flags"] = flags
    quote = await api.collections.status(row)
    assert quote["state"] == "ready"
    assert json.loads(quote["quote"]["payload"])["account"]["quality_flags"] == flags
    args = claim_data(quote, row, driver)
    await api.collections.claim(row, args)
    tx = payment(api, driver)
    await api.collections.authorise(row, args | {"draft": transaction_shape(tx)})
    with pytest.raises(WalletError, match="already"):
        await api.collections.authorise(row, args | {"draft": transaction_shape(tx)})
    assert not api.chain.posts  # A permit alone never broadcasts.


@pytest.mark.parametrize("problem", ["policy_off", "revoked", "unpriced", "over_cap"])
async def test_warning_never_overrides_credit_safety(tmp_path, problem):
    _, api, proxy, row, _, _ = await credit_ready(tmp_path)
    proxy.data["latest_session"]["quality_flags"] = ["export:energy_without_matching_state"]
    if problem == "policy_off":
        await api.auto_credits.configure(False, "admin")
    elif problem == "revoked":
        row["state"] = "revoked"
    elif problem == "unpriced":
        proxy.data["latest_session"]["unpriced_export_wh"] = "1"
    else:
        proxy.data["latest_session"]["net_cost_aud_unrounded"] = "-10.00"
    await api.auto_credits.tick()
    assert not api.chain.posts


async def test_closed_account_warning_needs_no_extra_quality_acceptance(tmp_path):
    _, api, _, args, _, plan = await closed(tmp_path)
    assert plan["state"] == "ready_for_resolution"
    assert plan["accepted_flags"] == []
    assert "import:energy_without_matching_state" in plan["warning_flags"]
    args.pop("confirm_provisional_metering")
    invitation = await api.closures.execute("request_closed_session_consent", args, "admin")
    assert "import:energy_without_matching_state" in invitation["terms"]["account_scope"]
    assert invitation["terms"]["closed_session_review"]["account"]["quality_flags"]
    assert not api.chain.posts  # Still requires fresh driver consent.


async def test_legacy_reviewed_account_hash_input_is_unchanged(tmp_path):
    record = session()
    record["quality_flags"].append("export:energy_without_matching_state")
    assert reviewed_snapshot(record, ["export:energy_without_matching_state"]) == account_snapshot(record)
