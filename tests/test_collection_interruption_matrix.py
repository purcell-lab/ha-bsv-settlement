"""Cancel driver collection at durable boundaries; reload real HA storage."""
import asyncio
import copy

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from test_budget import no_network
from test_collection import ready, claim_data, payment

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("boundary", [
    "quote_before", "quote_after", "claim_before", "claim_after",
    "permit_before", "permit_after", "signed_before", "signed_after",
    "broadcast_before", "broadcast_after", "ack_before", "ack_after",
    "confirmation_before", "confirmation_after",
])
async def test_driver_interruption_reload_never_repeats_external_effect(tmp_path, boundary):
    hass, api, _, row, driver = await ready(tmp_path)
    save, broadcast = api.store.async_save, api.chain.broadcast
    hit = False
    args = None
    tx = payment(api, driver)

    async def interrupted_save(data):
        nonlocal hit
        item = api.collections.get(row)
        stage = {
            "ready": "quote", "wallet_attempt_reserved": "claim",
            "submission_authorised": "permit", "broadcast_unknown": "signed",
            "submitted": "ack", "provider_confirmed": "confirmation",
        }.get(item["state"] if item else None)
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

    api.store.async_save, api.chain.broadcast = interrupted_save, interrupted_broadcast
    with pytest.raises(asyncio.CancelledError):
        item = await api.collections.status(row)
        args = claim_data(item, row, driver)
        await api.collections.claim(row, args)
        await api.collections.authorise(row, args | {"draft": transaction_shape(tx)})
        await api.collections.report(row, args | {"raw_tx": tx.hex()})
    assert hit
    interrupted = copy.deepcopy(api.collections.get(row))
    posts = list(api.chain.posts)
    api.chain.broadcast = broadcast
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    saved_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    saved = copy.deepcopy(restored.collections.get(saved_row))
    for _ in range(3):
        await restored.collections.reconcile(saved_row)
    assert api.chain.posts == posts and len(posts) <= 1
    if not saved:
        assert boundary == "quote_before" and not posts
        return
    for key in ("source_hash", "quote"):
        assert saved[key] == interrupted[key]
    assert len(restored.saved["driver_collection_index"]) == 1
    if saved.get("attempt_token_hash"):
        with pytest.raises(WalletError):
            await restored.collections.claim(saved_row, args)
    if saved.get("draft_hash"):
        with pytest.raises(WalletError):
            await restored.collections.authorise(saved_row, args | {"draft": transaction_shape(tx)})
    if saved.get("txid"):
        for key in ("txid", "signed_raw", "draft_hash", "attempt_token_hash"):
            assert saved[key] == interrupted[key]
        await restored.collections.report(saved_row, args | {"raw_tx": tx.hex()})
        assert api.chain.posts == posts
        assert restored.collections.get(saved_row)["state"] == (
            "provider_confirmed" if posts else "broadcast_unknown")
    else:
        assert not posts  # Persisted intent always precedes any provider effect.
