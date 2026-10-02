"""Deterministic cancellation boundaries, real HA persistence, fake provider."""
import asyncio
import copy

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from test_auto_credit import ready
from test_budget import no_network
from test_ongoing_credit import setup as ongoing_ready

pytestmark = pytest.mark.asyncio


async def prepare(tmp_path, mode):
    values = await (ongoing_ready if mode == "ongoing" else ready)(tmp_path)
    hass, api = values[:2]
    # Fictional evidence exists only after the fake broadcaster receives bytes.
    original_details = api.chain.details
    async def observed_only(txid):
        if not api.chain.posts:
            raise WalletError("Fictional provider has no observation")
        return await original_details(txid)
    api.chain.details = observed_only
    return values


def worker(api, mode):
    return api.ongoing_credits if mode == "ongoing" else api.auto_credits


def sole_item(api):
    rows = list(api.saved["automatic_credits"].values())
    assert len(rows) <= 1
    return rows[0] if rows else None


@pytest.mark.parametrize("mode", ["session", "ongoing"])
@pytest.mark.parametrize("boundary", [
    "freeze_before", "freeze_after", "signed_before", "signed_after",
    "broadcast_before", "broadcast_after", "ack_before", "ack_after",
])
async def test_interrupted_credit_reloads_without_duplicate_external_effect(tmp_path, mode, boundary):
    hass, api, _, _, _, _ = await prepare(tmp_path, mode)
    save = api.store.async_save
    broadcast = api.chain.broadcast
    hit = False

    async def interrupted_save(data):
        nonlocal hit
        item = sole_item(api)
        state = item["state"] if item else None
        stage = {"credit_queued": "freeze", "broadcast_unknown": "signed",
                 "submitted": "ack"}.get(state)
        if not hit and stage and boundary == stage + "_before":
            hit = True
            raise asyncio.CancelledError()
        await save(data)
        if not hit and stage and boundary == stage + "_after":
            hit = True
            raise asyncio.CancelledError()

    async def interrupted_broadcast(raw):
        nonlocal hit
        if boundary == "broadcast_before":
            hit = True
            raise asyncio.CancelledError()
        result = await broadcast(raw)
        if boundary == "broadcast_after":
            hit = True
            raise asyncio.CancelledError()
        return result

    api.store.async_save = interrupted_save
    api.chain.broadcast = interrupted_broadcast
    with pytest.raises(asyncio.CancelledError):
        await worker(api, mode).tick()
    assert hit
    interrupted = copy.deepcopy(sole_item(api))
    posts_at_interrupt = list(api.chain.posts)
    api.chain.broadcast = broadcast

    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    saved = copy.deepcopy(sole_item(restored))
    durable_signed = bool(saved and saved.get("txid"))
    if durable_signed:
        # Exact bytes, account, destination and source reservation survive.
        for key in ("txid", "signed_raw", "source_hash", "recipient_address",
                    "amount_sats", "fee_sats", "source_txid", "source_index"):
            assert saved[key] == interrupted[key]
        assert (saved["source_txid"], saved["source_index"]) in restored.auto_credits.used()

    for _ in range(3):
        await worker(restored, mode).tick()
    final = sole_item(restored)
    if durable_signed:
        assert api.chain.posts == posts_at_interrupt  # Never automatically resend.
        assert final["txid"] == saved["txid"] and final["signed_raw"] == saved["signed_raw"]
        assert final["state"] == ("provider_confirmed" if posts_at_interrupt else "broadcast_unknown")
    else:
        # No external effect was possible before a signed record was durable.
        assert not posts_at_interrupt
        assert len(api.chain.posts) == 1 and final["state"] == "provider_confirmed"
    assert len(api.chain.posts) <= 1
    assert len(restored.saved["automatic_credit_index"]) == 1


@pytest.mark.parametrize("mode", ["session", "ongoing"])
async def test_concurrent_coordinator_refreshes_share_one_payment_and_reservation(tmp_path, mode):
    hass, api, _, _, _, _ = await prepare(tmp_path, mode)
    coordinator = SettlementCoordinator(hass, api.entry, api)
    await coordinator.load()
    await asyncio.gather(*(coordinator._async_update_data() for _ in range(4)))
    assert len(api.chain.posts) == 1
    assert len(api.saved["automatic_credits"]) == 1
    item = sole_item(api)
    assert item["state"] == "provider_confirmed"
    assert api.auto_credits.used() == {(item["source_txid"], item["source_index"])}


@pytest.mark.parametrize("mode", ["session", "ongoing"])
async def test_changed_frozen_account_after_unsigned_restart_never_signs(tmp_path, mode):
    hass, api, proxy, _, _, _ = await prepare(tmp_path, mode)
    funding = api.chain.rows
    api.chain.rows = []
    await worker(api, mode).tick()
    original = copy.deepcopy(sole_item(api))
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    restored.chain.rows = funding
    proxy.data["latest_session"]["net_cost_aud_unrounded"] = "-3.00"
    await worker(restored, mode).tick()
    assert not api.chain.posts
    assert sole_item(restored)["source_hash"] == original["source_hash"]
    assert "changed" in sole_item(restored)["error"]

