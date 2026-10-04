"""Unmined WoC shape is pending, not an invalid confirmation count."""
from unittest.mock import AsyncMock

import pytest
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI, WoCClient
from custom_components.bsv_settlement.api import WalletError
from test_collection import authorised, no_network

pytestmark=pytest.mark.asyncio


def unmined(tx):
    return {"txid":tx.txid(),"hash":tx.txid(),"version":tx.version,
            "size":len(tx.hex())//2,"locktime":tx.locktime,
            "vin":[{} for _ in tx.inputs],"vout":[{} for _ in tx.outputs]}


async def test_unmined_to_confirmed_never_broadcasts_again(tmp_path):
    hass,api,_,row,_,args,tx=await authorised(tmp_path)
    api.chain.details=AsyncMock(return_value=unmined(tx))
    result=await api.collections.report(row,args|{"raw_tx":tx.hex()})
    assert result["state"]=="provider_unconfirmed"
    assert result["confirmations"]==0 and "error" not in result
    assert len(api.chain.posts)==1
    api.collections.get(row)["error"]="Invalid provider confirmation evidence"
    assert "error" not in await api.collections.reconcile(row)
    restored=MainnetWalletAPI(hass,api.entry)
    await restored.load()
    restored.chain=api.chain
    row=restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert (await restored.collections.reconcile(row))["state"]=="provider_unconfirmed"
    api.chain.details=AsyncMock(return_value={"txid":tx.txid(),"confirmations":1})
    result=await restored.collections.reconcile(row)
    assert result["state"]=="provider_confirmed" and result["confirmations"]==1
    assert len(api.chain.posts)==1


@pytest.mark.parametrize("patch",[
    {"confirmations":None},{"confirmations":True},{"confirmations":-1},{"confirmations":"0"},
    {"blockheight":1},{"blockhash":"00"*32},{"blocktime":None},{"size":1},{"version":True},
    {"hash":"11"*32},{"txid":"22"*32},{"vin":[]},{"vout":None},
])
async def test_malformed_evidence_remains_an_error(tmp_path,patch):
    _,api,_,row,_,args,tx=await authorised(tmp_path)
    api.chain.details=AsyncMock(return_value=unmined(tx)|patch)
    result=await api.collections.report(row,args|{"raw_tx":tx.hex()})
    assert result["state"]=="broadcast_unknown" and result.get("error")
    assert result.get("confirmations") is None
    assert len(api.chain.posts)==1


async def test_incomplete_or_wrong_raw_evidence_does_not_become_pending(tmp_path):
    _,api,_,row,_,args,tx=await authorised(tmp_path)
    api.chain.details=AsyncMock(return_value={"txid":tx.txid()})
    result=await api.collections.report(row,args|{"raw_tx":tx.hex()})
    assert result.get("error") and result["state"]=="broadcast_unknown"
    api.chain.details=AsyncMock(return_value=unmined(tx))
    api.chain.request=AsyncMock(return_value=api.chain.raw)
    result=await api.collections.reconcile(row)
    assert result["error"]=="Chain evidence differs from the recorded signed payment"
    assert len(api.chain.posts)==1


async def test_unconfirmed_source_is_still_not_spendable_funding(tmp_path):
    _,_,_,_,_,_,tx=await authorised(tmp_path)
    client=WoCClient(None)
    client.request=AsyncMock(side_effect=[tx.hex(),unmined(tx)])
    with pytest.raises(WalletError,match="provider-confirmed"):
        await client.source(tx.txid())
