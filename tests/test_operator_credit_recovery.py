"""Original-recipient recovery uses fictional keys and a recording provider."""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_budget import no_network
from test_ongoing_credit import setup, config

pytestmark = pytest.mark.asyncio


async def prepared(tmp_path):
    hass, api, proxy, row, driver, data = await setup(tmp_path, "-1.19")
    await api.ongoing_credits.configure({"enabled": False}, "admin")
    route = next(iter(api.ongoing_credits.routes.values()))
    review = await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    return hass, api, proxy, row, route, review


def approval(review):
    return {k: review[k] for k in ("credit_id", "recipient_address", "amount_sats", "fee_sats")} | {
        "expected_review_hash": review["review_hash"], "confirm_mainnet_payment": True}


async def test_prepare_unsigned_original_recipient_and_repeated_read(tmp_path):
    _, api, _, row, route, review = await prepared(tmp_path)
    assert review["amount_sats"] == 119 and review["total_sats"] == 129
    assert review["state"] == "credit_review_required" and not review["txid"]
    assert review["recipient_address"] == row["credit_destination"]["address"]
    assert review["recipient_address"] != api.saved["driver"]["driver_receive_address"]
    assert not api.chain.posts and api.credit_recovery._permit is None
    assert route["manual_recovery"]["unsigned_raw"]
    assert not review["funds_reserved"]
    same = await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    assert same == review
    assert not api.ongoing_credits.policy["enabled"]
    summary = json.dumps(api.ongoing_credits.summary())
    for secret in ("unsigned_raw", "signed_raw", "secret_hex", "prepared_by", '"proof"'):
        assert secret not in summary


async def test_restart_and_reenabled_automatic_policy_never_release_hold(tmp_path):
    hass, api, _, row, route, review = await prepared(tmp_path)
    await api.ongoing_credits.configure(config(row, None), "admin")
    await api.ongoing_credits.tick()
    await api.auto_credits.tick()
    assert not api.chain.posts
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    await restored.ongoing_credits.tick()
    await restored.auto_credits.tick()
    assert not api.chain.posts and restored.credit_recovery._permit is None
    assert restored.credit_recovery.public(restored.ongoing_credits.routes[route["route_id"]])["review_hash"] == review["review_hash"]


async def test_exact_send_once_and_original_wallet_receipt(tmp_path):
    hass, api, _, row, route, review = await prepared(tmp_path)
    sent = await api.credit_recovery.broadcast(approval(review), "admin")
    assert sent["state"] == "submitted" and len(api.chain.posts) == 1
    assert not api.ongoing_credits.policy["enabled"]  # One-off does not enable standing authority.
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    await restored.credit_recovery.broadcast(approval(review), "admin")
    await restored.ongoing_credits.tick()
    receipt = await restored.ongoing_credits.driver_receipt(row, route["route_id"])
    assert len(api.chain.posts) == 1
    assert receipt["budget_id"] == row["terms"]["budget_id"]
    assert receipt["remittance"] == row["terms"]["credit_receiving"]
    assert receipt["amount_sats"] == 119 and receipt["energy_account"]["net_amount_aud"] == "-1.19"
    assert receipt["recipient_address"] == review["recipient_address"]


@pytest.mark.parametrize("field,value", [
    ("expected_review_hash", "wrong"), ("recipient_address", "wrong"),
    ("amount_sats", 120), ("fee_sats", 11), ("confirm_mainnet_payment", False)])
async def test_exact_approval_required(tmp_path, field, value):
    _, api, _, _, _, review = await prepared(tmp_path)
    with pytest.raises(WalletError):
        await api.credit_recovery.broadcast(approval(review) | {field: value}, "admin")
    assert not api.chain.posts and api.credit_recovery._permit is None


@pytest.mark.parametrize("problem", [
    "account", "recipient", "proof", "revoked", "transaction", "master_off",
    "broadcast_off", "funding_gone", "funding_used", "unsigned_changed",
    "frozen_item", "review_expired", "new_manual_review"])
async def test_changed_evidence_fails_closed(tmp_path, problem, monkeypatch):
    _, api, proxy, row, route, review = await prepared(tmp_path)
    item = api.ongoing_credits.get(api.ongoing_credits.wrapper(route))
    if problem == "account":
        proxy.data["latest_session"]["net_cost_aud_unrounded"] = "-1.20"
    elif problem == "recipient":
        route["recipient"]["address"] = api.saved["driver"]["driver_receive_address"]
    elif problem == "proof":
        row["credit_destination"]["proof"]["signature"] = "00" * 64
    elif problem == "revoked":
        row["state"] = "revoked"
    elif problem == "transaction":
        proxy.data["latest_session"]["ocpp_transaction_id"] = "different"
    elif problem == "master_off":
        await api.auto_credits.configure(False, "admin")
    elif problem == "broadcast_off":
        api.hass.config_entries.async_update_entry(api.entry, data={**api.entry.data, "enable_broadcast": False})
    elif problem == "funding_gone":
        api.chain.rows = []
    elif problem == "funding_used":
        api.saved["payments"]["used"] = {"txid": "ff"*32, "state": "provider_confirmed",
            "source_txid": review["source_txid"], "source_index": review["source_index"]}
    elif problem == "unsigned_changed":
        route["manual_recovery"]["unsigned_raw"] = "00"
    elif problem == "frozen_item":
        item["amount_sats"] = 120
    elif problem == "review_expired":
        monkeypatch.setattr("custom_components.bsv_settlement.credit_recovery.now",
                            lambda: now() + timedelta(minutes=11))
    else:
        key = api.collections.key(api.ongoing_credits.wrapper(route))
        api.saved["session_review_index"][key] = "new-review"
        api.saved["session_reviews"]["new-review"] = {"state": "awaiting_account_approval"}
    with pytest.raises(WalletError):
        await api.credit_recovery.broadcast(approval(review), "admin")
    assert not api.chain.posts and api.credit_recovery._permit is None
    await api.ongoing_credits.tick()
    assert not api.chain.posts


async def test_uncertain_submission_is_not_repeated_even_after_restart(tmp_path):
    hass, api, _, _, route, review = await prepared(tmp_path)
    api.chain.fail = True
    await api.credit_recovery.broadcast(approval(review), "admin")
    assert len(api.chain.posts) == 1
    assert api.credit_recovery.public(route)["state"] == "broadcast_unknown"
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    await restored.credit_recovery.broadcast(approval(review), "admin")
    await restored.ongoing_credits.tick()
    assert len(api.chain.posts) == 1


async def test_prepare_save_failure_rolls_back_session_ownership(tmp_path):
    _, api, _, _, _, _ = await setup(tmp_path, "-1.19")
    route = next(iter(api.ongoing_credits.routes.values()))
    original = copy.deepcopy(route)
    api.store.async_save = AsyncMock(side_effect=OSError("Fictional storage failure"))
    with pytest.raises(OSError):
        await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    assert route == original and not api.saved["automatic_credits"]
    assert not api.saved["automatic_credit_index"] and not api.chain.posts


async def test_signed_storage_failure_never_broadcasts_or_leaves_a_permit(tmp_path):
    _, api, _, _, route, review = await prepared(tmp_path)
    save = api.store.async_save
    async def fail_signed(saved):
        if any(p.get("txid") for p in saved["automatic_credits"].values()):
            raise OSError("Fictional signed-write failure")
        await save(saved)
    api.store.async_save = fail_signed
    with pytest.raises(OSError):
        await api.credit_recovery.broadcast(approval(review), "admin")
    assert not api.chain.posts and api.credit_recovery._permit is None


async def test_expired_review_requires_exact_explicit_renewal_and_keeps_audit(tmp_path, monkeypatch):
    _, api, _, _, route, review = await prepared(tmp_path)
    with pytest.raises(WalletError, match="expired"):
        await api.credit_recovery.prepare({"credit_id": route["route_id"],
            "replace_expired_review_hash": review["review_hash"]}, "admin")
    monkeypatch.setattr("custom_components.bsv_settlement.credit_recovery.now",
                        lambda: now() + timedelta(minutes=11))
    same = await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    assert same["review_hash"] == review["review_hash"]
    with pytest.raises(WalletError):
        await api.credit_recovery.prepare({"credit_id": route["route_id"],
            "replace_expired_review_hash": "wrong"}, "admin")
    renewed = await api.credit_recovery.prepare({"credit_id": route["route_id"],
        "replace_expired_review_hash": review["review_hash"]}, "admin")
    assert renewed["review_hash"] != review["review_hash"]
    assert route["manual_recovery_history"][0]["review_hash"] == review["review_hash"]
    with pytest.raises(WalletError):
        await api.credit_recovery.broadcast(approval(review), "admin")
    assert not api.chain.posts


async def test_prepare_and_send_refuse_missing_administrator(tmp_path):
    _, api, _, _, route, review = await prepared(tmp_path)
    with pytest.raises(WalletError, match="administrator"):
        await api.credit_recovery.prepare({"credit_id": route["route_id"]}, None)
    with pytest.raises(WalletError, match="administrator"):
        await api.credit_recovery.broadcast(approval(review), None)
    assert not api.chain.posts


async def test_signed_credit_cannot_be_replaced_after_expiry(tmp_path, monkeypatch):
    _, api, _, _, route, review = await prepared(tmp_path)
    await api.credit_recovery.broadcast(approval(review), "admin")
    monkeypatch.setattr("custom_components.bsv_settlement.credit_recovery.now",
                        lambda: now() + timedelta(minutes=11))
    with pytest.raises(WalletError, match="signed credit"):
        await api.credit_recovery.prepare({"credit_id": route["route_id"],
            "replace_expired_review_hash": review["review_hash"]}, "admin")
    assert len(api.chain.posts) == 1


async def test_prep_rejects_conflicting_session_and_bad_data_before_mutating(tmp_path):
    _, api, proxy, _, _, data = await setup(tmp_path, "-1.19")
    route = next(iter(api.ongoing_credits.routes.values()))
    await api.reviews.prepare(data, "admin")
    with pytest.raises(WalletError):
        await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    assert not route.get("manual_recovery") and not api.saved["automatic_credits"]
    await api.reviews.cancel({"review_id": api.reviews.latest()["review_id"]})
    proxy.data["latest_session"]["quality_flags"] = ["export:missing_tariff"]
    with pytest.raises(WalletError, match="quality"):
        await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    assert not api.chain.posts and not route.get("manual_recovery")


@pytest.mark.parametrize("action", ["prepare_operator_credit_recovery", "broadcast_operator_credit_recovery"])
async def test_real_admin_service_boundary(tmp_path, action):
    hass, api, _, _, _, review = await prepared(tmp_path)
    execute = AsyncMock(return_value={"state": "fictional"})
    hass.data["bsv_settlement"][api.entry.entry_id] = SimpleNamespace(execute=execute)
    await async_setup(hass, {})
    data = {"config_entry_id": api.entry.entry_id, "credit_id": review["credit_id"]}
    if action.startswith("broadcast"):
        data.update(approval(review))
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("bsv_settlement", action, data, blocking=True)
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("bsv_settlement", action, data, blocking=True, context=Context(user_id="viewer"))
    execute.assert_not_awaited()
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_admin=True))
    await hass.services.async_call("bsv_settlement", action, data, blocking=True,
                                  context=Context(user_id="admin"), return_response=True)
    assert execute.await_args.args[2] == "admin"
