"""Fictional chain only: timing cannot create authority or resend payments."""
import copy
from unittest.mock import AsyncMock

import pytest

from custom_components.bsv_settlement import payment_timing
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.portal import history
from custom_components.bsv_settlement.receipt_ack import acknowledge
from test_budget import no_network
from test_collection import authorised
from test_operator_credit_unconfirmed import submitted
from test_receipt_ack import confirmed, report
from test_energy_adjustment import setup


def test_no_invented_legacy_events_and_first_observation_survives_reorg():
    item = {"state": "provider_confirmed", "approved_at": "old", "checked_at": "old"}
    payment_timing.confirmed(item, 3)
    assert all(v is None for v in payment_timing.public(item).values())
    item["state"] = "provider_unconfirmed"
    for invalid in (0, -1, True, None, "1"):
        payment_timing.confirmed(item, invalid)
    assert not item.get("provider_first_confirmed_at")
    payment_timing.confirmed(item, 1)
    first = item["provider_first_confirmed_at"]
    payment_timing.confirmed(item, 8)
    assert item["provider_first_confirmed_at"] == first
    assert payment_timing.public({"wallet_receipt_ack": {"reported_at": "original"}})[
        "wallet_accepted_reported_at"] == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["session", "ongoing"])
async def test_credit_events_saved_before_network_and_survive_restart(tmp_path, mode):
    hass, api, item = await submitted(tmp_path, mode)
    assert item["broadcast_attempted_at"] <= item["broadcast_acknowledged_at"]
    assert not item.get("provider_first_confirmed_at")
    before = payment_timing.public(item)
    api.chain.details = AsyncMock(return_value={"txid": item["txid"], "confirmations": 1})
    await api.auto_credits.reconcile(item)
    assert item["provider_first_confirmed_at"]
    first = payment_timing.public(item)
    await api.auto_credits.reconcile(item, force=True)
    assert payment_timing.public(item) == first
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    saved = restored.saved["automatic_credits"][item["budget_id"]]
    assert payment_timing.public(saved) == first
    assert before["broadcast_acknowledged_at"] == first["broadcast_acknowledged_at"]
    assert len(api.chain.posts) == 1


@pytest.mark.asyncio
async def test_driver_submission_timeout_records_intent_not_ack_or_confirmation(tmp_path):
    hass, api, _, row, _, args, tx = await authorised(tmp_path)
    api.chain.fail = True
    result = await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert result["broadcast_attempted_at"]
    assert result["broadcast_acknowledged_at"] is None
    assert result["provider_first_confirmed_at"] is None
    assert result["state"] == "broadcast_unknown"
    await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert len(api.chain.posts) == 1
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert restored.collections.get(row)["broadcast_attempted_at"] == result["broadcast_attempted_at"]


@pytest.mark.asyncio
async def test_driver_success_public_summary_and_history(tmp_path):
    _, api, _, row, driver, args, tx = await authorised(tmp_path)
    original_broadcast = api.chain.broadcast

    async def broadcast(raw):
        # Persistence has completed before the irreversible provider call.
        restored = MainnetWalletAPI(api.hass, api.entry)
        await restored.load()
        assert restored.collections.get(row)["broadcast_attempted_at"]
        assert not restored.collections.get(row).get("broadcast_acknowledged_at")
        return await original_broadcast(raw)

    api.chain.broadcast = broadcast
    result = await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert result["broadcast_attempted_at"] <= result["broadcast_acknowledged_at"] <= result["provider_first_confirmed_at"]
    summary = next(p for p in api.payment_summary() if p.get("txid") == tx.txid())
    assert summary["provider_first_confirmed_at"] == result["provider_first_confirmed_at"]
    records = history(api, driver.public_key().hex())
    visible = next(t for s in records for t in s["transactions"] if t["txid"] == tx.txid())
    assert visible["broadcast_acknowledged_at"] == result["broadcast_acknowledged_at"]


@pytest.mark.asyncio
async def test_acceptance_uses_original_signed_report_time_without_payment_changes(tmp_path):
    _, api, row, driver, item = await confirmed(tmp_path)
    before = copy.deepcopy(item)
    data = report(api, row, item, driver)
    result = await acknowledge(api, row, data)
    assert result["wallet_accepted_reported_at"] == item["wallet_receipt_ack"]["reported_at"]
    assert await acknowledge(api, row, data) == result
    visible = next(t for s in history(api, driver.public_key().hex())
                   for t in s["transactions"] if t["txid"] == item["txid"])
    assert visible["wallet_accepted_reported_at"] == result["wallet_accepted_reported_at"]
    stripped = copy.deepcopy(item)
    stripped.pop("wallet_receipt_ack")
    assert stripped == before
    assert len(api.chain.posts) == 1


@pytest.mark.asyncio
async def test_button_credit_uses_same_durable_event_fields(tmp_path):
    hass, api, _, _, _, data = await setup(tmp_path)
    original = api.chain.broadcast

    async def broadcast(raw):
        restored = MainnetWalletAPI(hass, api.entry)
        await restored.load()
        signed = next(p for p in restored.saved["payments"].values() if p.get("txid"))
        assert signed["broadcast_attempted_at"]
        assert not signed.get("broadcast_acknowledged_at")
        return await original(raw)

    api.chain.broadcast = broadcast
    result = await api.reviews.execute("pay_energy_adjustment",
        data | {"confirm_mainnet_payment": True}, "admin")
    draft = result["credit_draft"]
    assert draft["broadcast_attempted_at"] <= draft["broadcast_acknowledged_at"]
    await api.refresh_chain()
    payment = api.saved["payments"][draft["draft_id"]]
    assert payment["provider_first_confirmed_at"]
    assert api.public_payment(payment)["provider_first_confirmed_at"] == payment["provider_first_confirmed_at"]
    assert len(api.chain.posts) == 1
