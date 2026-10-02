"""Provider-confirmed is a revisitable observation, never irreversible finality."""
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.auto_credit import CONFIRMED_RECHECK_SECONDS
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_auto_credit import ready
from test_budget import no_network

pytestmark = pytest.mark.asyncio


async def confirmed(tmp_path):
    hass, api, proxy, row, _, _ = await ready(tmp_path)
    await api.auto_credits.tick()
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "provider_confirmed"
    return hass, api, row, item


def due(item):
    item["checked_at"] = (now()-timedelta(seconds=CONFIRMED_RECHECK_SECONDS+1)).isoformat()


async def test_periodic_recheck_is_bounded_and_reorg_retains_reservations(tmp_path):
    _, api, row, item = await confirmed(tmp_path)
    frozen = {k:item[k] for k in ("txid","signed_raw","source_txid","source_index","source_hash")}
    item["receipt"] = {"obsolete":"proof"}
    api.chain.details = AsyncMock(return_value={"txid":item["txid"],"confirmations":0})
    await api.auto_credits.tick()
    api.chain.details.assert_not_awaited()
    due(item)
    await api.auto_credits.tick()
    api.chain.details.assert_awaited_once()
    assert item["state"] == "provider_unconfirmed" and item["confirmations"] == 0
    assert "receipt" not in item and api.auto_credits.pending()
    assert {k:item[k] for k in frozen} == frozen
    assert (item["source_txid"],item["source_index"]) in api.auto_credits.used()
    assert len(api.chain.posts) == 1
    api.chain.details.return_value["confirmations"] = 2
    await api.auto_credits.tick()
    assert item["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("failure",["outage","wrong_txid","wrong_raw","negative","missing","boolean"])
async def test_invalid_or_missing_evidence_reopens_uncertainty_without_respending(tmp_path, failure):
    hass, api, row, item = await confirmed(tmp_path)
    item["receipt"] = {"obsolete":"proof"}
    due(item)
    if failure == "outage":
        api.chain.details = AsyncMock(side_effect=WalletError("fictional 404/outage"))
    elif failure == "wrong_raw":
        api.chain.request = AsyncMock(return_value="00")
    else:
        details = {"txid":item["txid"],"confirmations":1}
        if failure == "wrong_txid":
            details["txid"] = "ff"*32
        elif failure == "negative":
            details["confirmations"] = -1
        elif failure == "missing":
            details.pop("confirmations")
        else:
            details["confirmations"] = True
        api.chain.details = AsyncMock(return_value=details)
    await api.auto_credits.tick()
    assert item["state"] == "broadcast_unknown" and item["confirmations"] is None
    assert "receipt" not in item and api.auto_credits.pending()
    assert len(api.chain.posts) == 1
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert restored.auto_credits.get(row)["txid"] == item["txid"]
    assert restored.auto_credits.pending()


async def test_receipt_forces_fresh_evidence_and_refuses_orphaned_proof(tmp_path):
    _, api, row, item = await confirmed(tmp_path)
    item["receipt"] = {"obsolete":"proof"}
    api.chain.details = AsyncMock(return_value={"txid":item["txid"],"confirmations":0})
    with pytest.raises(WalletError,match="awaits provider confirmation"):
        await api.auto_credits.receipt(row)
    assert "receipt" not in item and item["state"] == "provider_unconfirmed"
    assert len(api.chain.posts) == 1
    api.chain.details.return_value["confirmations"] = 3
    receipt = await api.auto_credits.receipt(row)
    assert receipt["proof"]["target"] == "22"*32
    assert receipt["confirmations"] == 3
    assert len(api.chain.posts) == 1


async def test_disabled_policy_still_reassesses_confirmed_payment(tmp_path):
    _, api, row, item = await confirmed(tmp_path)
    await api.auto_credits.configure(False,"admin")
    due(item)
    api.chain.details = AsyncMock(return_value={"txid":item["txid"],"confirmations":0})
    await api.auto_credits.tick()
    assert item["state"] == "provider_unconfirmed" and len(api.chain.posts) == 1


@pytest.mark.parametrize("timestamp",[None,"invalid","2099-01-01T00:00:00+00:00","2026-01-01T00:00:00"])
async def test_missing_invalid_future_or_naive_timestamp_never_skips_reassessment(tmp_path,timestamp):
    _, api, row, item = await confirmed(tmp_path)
    item["checked_at"] = timestamp
    api.chain.details = AsyncMock(return_value={"txid":item["txid"],"confirmations":0})
    await api.auto_credits.tick()
    assert item["state"] == "provider_unconfirmed" and len(api.chain.posts) == 1
