"""Ongoing credits: all identities, accounts and broadcasts are fictional."""
import copy
import json
from datetime import timedelta
from bsv import PrivateKey
import pytest
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.session_review import now
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from test_auto_credit import ready, proof
from test_budget import no_network, consent

pytestmark = pytest.mark.asyncio


def config(row, initial="session-1"):
    return {"enabled": True, "proxy_config_entry_id": "proxy-entry",
            "conversion_rate_entity": "sensor.demo_rate", "initial_session_id": initial,
            "expected_budget_id": row["terms"]["budget_id"],
            "expected_recipient_address": row["credit_destination"]["address"],
            "confirm_ongoing_mainnet_credits": True}


async def setup(tmp_path, amount="-1.89"):
    hass, api, proxy, row, driver, data = await ready(tmp_path, amount)
    await api.ongoing_credits.configure(config(row), "admin")
    return hass, api, proxy, row, driver, data


async def test_initial_credit_shared_exclusion_restart_and_receipt_scope(tmp_path):
    hass, api, proxy, row, _, data = await setup(tmp_path)
    await api.ongoing_credits.tick()
    route = next(iter(api.ongoing_credits.routes.values()))
    item = api.ongoing_credits.get(api.ongoing_credits.wrapper(route))
    assert item["amount_sats"] == 189 and item["fee_sats"] == 10
    assert len(api.chain.posts) == 1
    await api.auto_credits.tick()  # Original session mandate must not pay again.
    assert len(api.chain.posts) == 1
    with pytest.raises(WalletError, match="Automatic credit"):
        await api.reviews.prepare(data, "admin")
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    await restored.ongoing_credits.tick()
    assert len(api.chain.posts) == 1
    item = restored.saved["automatic_credits"][route["route_id"]]
    assert item["state"] == "provider_confirmed"
    item["receipt"] = {"raw_tx":item["signed_raw"],"proof":{},"block":{}}
    receipt = await restored.ongoing_credits.driver_receipt(row, route["route_id"])
    assert receipt["budget_id"] == row["terms"]["budget_id"]
    assert receipt["remittance"] == row["terms"]["credit_receiving"]
    stranger = copy.deepcopy(row); stranger["terms"]["budget_id"] = "different-registration"
    with pytest.raises(WalletError, match="does not belong"):
        await restored.ongoing_credits.driver_receipt(stranger, route["route_id"])
    summary = json.dumps(restored.ongoing_credits.summary())
    for forbidden in ("signed_raw", "secret_hex", "driver_token_hash", '"proof"'):
        assert forbidden not in summary


async def test_latest_driver_changes_only_future_sessions_and_rate_freezes(tmp_path):
    hass, api, proxy, row, _, data = await setup(tmp_path)
    first = next(iter(api.ongoing_credits.routes.values()))
    old_address = first["recipient"]["address"]
    data.pop("session_id")
    public = await api.budgets.create(data, "admin")
    latest = api.saved["session_budgets"][public["terms"]["budget_id"]]
    driver = PrivateKey()
    await api.budgets.accept(latest, consent(latest, driver), "driver")
    await api.auto_credits.register(latest, proof(api, latest, driver))
    hass.states.async_set("sensor.demo_rate", "200", {"unit_of_measurement":"sat/AUD"})
    proxy.data["previous_session"] = copy.deepcopy(proxy.data["latest_session"])
    proxy.data["latest_session"].update(session_id="future-2", ocpp_transaction_id="future-tx",
                                        opened_at=now().isoformat(), ended_at=None)
    await api.ongoing_credits.tick()
    routes = list(api.ongoing_credits.routes.values())
    assert routes[0]["recipient"]["address"] == old_address
    assert routes[0]["satoshis_per_aud"] == "100"
    assert routes[1]["recipient"]["address"] == latest["credit_destination"]["address"]
    assert routes[1]["satoshis_per_aud"] == "200"


async def test_historical_sessions_excluded_without_explicit_initial_scope(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    await api.ongoing_credits.configure(config(row, None), "admin")
    await api.ongoing_credits.tick()
    assert not api.ongoing_credits.routes and not api.chain.posts


@pytest.mark.parametrize("amount,posts", [("-9.90",1),("-9.91",0),("0",0),("1.00",0),("-0.001",0)])
async def test_cap_and_credit_only(tmp_path, amount, posts):
    _, api, _, _, _, _ = await setup(tmp_path, amount)
    await api.ongoing_credits.tick()
    assert len(api.chain.posts) == posts
    assert not api.saved["driver_collections"]


@pytest.mark.parametrize("problem", ["disabled","master_off","revoked","tampered","unpriced","quality","identity","transaction"])
async def test_fail_closed(tmp_path, problem):
    _, api, proxy, row, _, _ = await setup(tmp_path)
    if problem == "disabled":
        await api.ongoing_credits.configure({"enabled":False}, "admin")
    elif problem == "master_off":
        await api.auto_credits.configure(False, "admin")
    elif problem == "revoked":
        row["state"] = "revoked"
    elif problem == "tampered":
        row["credit_destination"]["proof"]["signature"] = "00"*64
    elif problem == "identity":
        row["receipt"]["driver_identity"] = PrivateKey().public_key().hex()
    elif problem == "unpriced":
        proxy.data["latest_session"]["unpriced_export_wh"] = "1"
    elif problem == "quality":
        proxy.data["latest_session"]["quality_flags"] = ["meter_reset"]
    else:
        proxy.data["latest_session"]["ocpp_transaction_id"] = "changed"
    await api.ongoing_credits.tick()
    assert not api.chain.posts


async def test_unsigned_queue_can_fund_but_unknown_submission_never_retries(tmp_path):
    hass, api, _, _, _, _ = await setup(tmp_path)
    rows = api.chain.rows; api.chain.rows = []
    await api.ongoing_credits.tick()
    route = next(iter(api.ongoing_credits.routes.values()))
    assert "funding" in route["error"]
    assert api.ongoing_credits.summary()["sessions"][0]["error"]
    api.chain.rows = rows; api.chain.fail = True
    await api.ongoing_credits.tick()
    assert len(api.chain.posts) == 1
    await api.ongoing_credits.configure({"enabled":False}, "admin")
    await api.ongoing_credits.tick()
    assert len(api.chain.posts) == 1


async def test_authority_and_recipient_race(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    with pytest.raises(WalletError, match="administrator"):
        await api.ongoing_credits.configure(config(row), None)
    with pytest.raises(WalletError, match="Explicit"):
        await api.ongoing_credits.configure(config(row)|{"confirm_ongoing_mainnet_credits":False}, "admin")
    with pytest.raises(WalletError, match="changed"):
        await api.ongoing_credits.configure(config(row)|{"expected_recipient_address":"wrong"}, "admin")
    assert not api.ongoing_credits.policy["enabled"]
    assert not api.ongoing_credits.routes


async def test_spending_expiry_does_not_revoke_separate_operator_authority(tmp_path, monkeypatch):
    _, api, _, row, _, _ = await setup(tmp_path)
    monkeypatch.setattr("custom_components.bsv_settlement.budget.now", lambda: now()+timedelta(days=2))
    assert api.budgets.public(row)["state"] == "expired"
    await api.ongoing_credits.tick()
    assert len(api.chain.posts) == 1


async def test_changed_frozen_account_and_storage_failure_block_broadcast(tmp_path):
    _, api, proxy, _, _, _ = await setup(tmp_path)
    rows=api.chain.rows;api.chain.rows=[]
    await api.ongoing_credits.tick()
    proxy.data["latest_session"]["net_cost_aud_unrounded"]="-2.00"
    api.chain.rows=rows
    await api.ongoing_credits.tick()
    assert not api.chain.posts
    assert "changed" in next(iter(api.ongoing_credits.routes.values()))["error"]
    proxy.data["latest_session"]["net_cost_aud_unrounded"]="-1.89"
    original=api.store.async_save
    async def reject_signed(saved):
        if any(p.get("txid") for p in saved["automatic_credits"].values()):
            raise OSError("Fictional storage failure")
        await original(saved)
    api.store.async_save=reject_signed
    with pytest.raises(OSError):
        await api.ongoing_credits.tick()
    assert not api.chain.posts
