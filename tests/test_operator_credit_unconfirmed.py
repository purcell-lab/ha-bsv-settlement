"""Strict unmined evidence for both credit policies, using fictional funds only."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from bsv import Transaction

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI, WoCClient
from test_auto_credit import ready
from test_budget import no_network
from test_credit_interruption_matrix import worker, sole_item
from test_credit_reassessment import due
from test_ongoing_credit import setup as ongoing_ready

pytestmark = pytest.mark.asyncio


def unmined(tx):
    return {"txid": tx.txid(), "hash": tx.txid(), "version": tx.version,
            "size": len(tx.hex()) // 2, "locktime": tx.locktime,
            "vin": [{} for _ in tx.inputs], "vout": [{} for _ in tx.outputs]}


async def submitted(tmp_path, mode="session"):
    values = await (ongoing_ready if mode == "ongoing" else ready)(tmp_path)
    hass, api = values[:2]
    await worker(api, mode).tick()
    item = sole_item(api)
    assert item["state"] == "submitted" and len(api.chain.posts) == 1
    return hass, api, item


@pytest.mark.parametrize("mode", ["session", "ongoing"])
async def test_existing_uncertain_credit_recovers_across_restart_without_resending(tmp_path, mode):
    hass, api, item = await submitted(tmp_path, mode)
    tx = Transaction.from_hex(item["signed_raw"])
    frozen = {k: deepcopy(item[k]) for k in (
        "txid", "signed_raw", "source_txid", "source_index", "source_hash",
        "account", "recipient_address", "amount_sats", "fee_sats")}
    api.chain.details = AsyncMock(return_value={"txid": item["txid"]})
    await worker(api, mode).tick()
    assert item["state"] == "broadcast_unknown" and item["error"]
    # The installed fix must repair a persisted old error, not just new credits.
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    restored.chain.details = AsyncMock(return_value=unmined(tx))
    await worker(restored, mode).tick()
    item = sole_item(restored)
    assert item["state"] == "provider_unconfirmed" and item["confirmations"] == 0
    assert item["error"] is None and item["checked_at"]
    assert "receipt" not in item and restored.auto_credits.pending()
    assert {k: item[k] for k in frozen} == frozen
    assert (item["source_txid"], item["source_index"]) in restored.auto_credits.used()
    if mode == "ongoing":
        route = next(iter(restored.ongoing_credits.routes.values()))
        assert route["state"] == "provider_unconfirmed" and not route.get("error")
    # Policy disable does not stop reconciliation or release a signed reservation.
    restored.auto_credits.policy["enabled"] = False
    restored.ongoing_credits.policy["enabled"] = False
    for _ in range(2):
        await worker(restored, mode).tick()
    assert len(api.chain.posts) == 1
    restored.chain.details.return_value = {"txid": tx.txid(), "confirmations": 1}
    await worker(restored, mode).tick()
    assert item["state"] == "provider_confirmed" and item["confirmations"] == 1
    # Reorg back to a complete unmined record invalidates the old receipt.
    item["receipt"] = {"obsolete": "proof"}
    due(item)
    restored.chain.details.return_value = unmined(tx)
    await worker(restored, mode).tick()
    assert item["state"] == "provider_unconfirmed" and "receipt" not in item
    assert {k: item[k] for k in frozen} == frozen
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("patch", [
    {"confirmations": None}, {"confirmations": True}, {"confirmations": False},
    {"confirmations": -1}, {"confirmations": "0"}, {"confirmations": 0.0},
    {"txid": "11" * 32}, {"hash": "22" * 32},
    {"blockhash": None}, {"blockheight": 1}, {"blocktime": None},
    {"version": True}, {"version": 99}, {"locktime": None}, {"locktime": True},
    {"size": None}, {"size": True}, {"size": 1},
    {"vin": []}, {"vin": {}}, {"vout": []}, {"vout": None},
])
async def test_malformed_unmined_evidence_remains_blocked(tmp_path, patch):
    _, api, item = await submitted(tmp_path)
    tx = Transaction.from_hex(item["signed_raw"])
    item["receipt"] = {"obsolete": "proof"}
    api.chain.details = AsyncMock(return_value=unmined(tx) | patch)
    await api.auto_credits.tick()
    assert item["state"] == "broadcast_unknown" and item["confirmations"] is None
    assert item["error"] and "receipt" not in item
    assert api.auto_credits.pending()
    assert (item["source_txid"], item["source_index"]) in api.auto_credits.used()
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("evidence", [None, [], "invalid", {}, {"txid": "placeholder"}])
async def test_incomplete_or_non_object_response_fails_closed(tmp_path, evidence):
    _, api, item = await submitted(tmp_path)
    if isinstance(evidence, dict) and evidence:
        evidence = {"txid": item["txid"]}
    api.chain.details = AsyncMock(return_value=evidence)
    await api.auto_credits.tick()
    assert item["state"] == "broadcast_unknown" and item["error"]
    assert len(api.chain.posts) == 1


async def test_unmined_metadata_cannot_override_wrong_signed_bytes(tmp_path):
    _, api, item = await submitted(tmp_path)
    api.chain.details = AsyncMock(return_value=unmined(Transaction.from_hex(item["signed_raw"])))
    api.chain.request = AsyncMock(return_value="00")
    await api.auto_credits.tick()
    assert item["state"] == "broadcast_unknown" and item["error"]
    assert len(api.chain.posts) == 1


async def test_unmined_credit_is_not_a_receipt_or_confirmed_funding(tmp_path):
    _, api, item = await submitted(tmp_path)
    tx = Transaction.from_hex(item["signed_raw"])
    api.chain.details = AsyncMock(return_value=unmined(tx))
    row = api.saved["session_budgets"][item["budget_id"]]
    with pytest.raises(WalletError, match="awaits provider confirmation"):
        await api.auto_credits.receipt(row)
    assert item["state"] == "provider_unconfirmed" and "receipt" not in item
    client = WoCClient(None)
    client.request = AsyncMock(side_effect=[tx.hex(), unmined(tx)])
    with pytest.raises(WalletError, match="provider-confirmed"):
        await client.source(tx.txid())
    assert len(api.chain.posts) == 1
