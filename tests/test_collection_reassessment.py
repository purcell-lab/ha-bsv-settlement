"""Driver confirmation loss uses fictional evidence and never resends money."""
import copy

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from test_budget import no_network
from test_collection import authorised

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("failure", [
    "outage", "wrong_raw", "malformed_raw", "missing_raw", "wrong_id",
    "missing_count", "negative_count", "boolean_count",
])
async def test_driver_confirmation_loss_retains_attempt_and_recovers_without_resend(tmp_path, failure):
    hass, api, _, row, _, args, tx = await authorised(tmp_path)
    assert (await api.collections.report(row, args | {"raw_tx": tx.hex()}))["state"] == "provider_confirmed"
    before = copy.deepcopy(api.collections.get(row))
    ownership = copy.deepcopy(api.saved["received_outpoints"])
    request, details = api.chain.request, api.chain.details

    async def changed_raw(*a, **kw):
        if failure == "outage":
            raise WalletError("Fictional provider unavailable")
        return {"wrong_raw": api.chain.raw, "malformed_raw": "00", "missing_raw": None}[failure]

    async def changed_details(txid):
        return {
            "wrong_id": {"txid": "0" * 64, "confirmations": 1},
            "missing_count": {"txid": txid},
            "negative_count": {"txid": txid, "confirmations": -1},
            "boolean_count": {"txid": txid, "confirmations": True},
        }[failure]

    if failure in ("outage", "wrong_raw", "malformed_raw", "missing_raw"):
        api.chain.request = changed_raw
    else:
        api.chain.details = changed_details
    result = await api.collections.reconcile(row)
    assert result["state"] == "broadcast_unknown" and result["confirmations"] is None
    assert result["error"]
    assert api.saved["received_outpoints"] == ownership
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    saved_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    item = restored.collections.get(saved_row)
    for key in ("txid", "signed_raw", "draft_hash", "source_hash", "quote", "attempt_token_hash"):
        assert item[key] == before[key]
    for _ in range(2):
        assert (await restored.collections.report(saved_row, args | {"raw_tx": tx.hex()}))["state"] == "broadcast_unknown"
    assert len(api.chain.posts) == 1
    restored.chain.request, restored.chain.details = request, details
    # Revocation cannot erase evidence of an already submitted payment.
    saved_row["state"] = "revoked"
    result = await restored.collections.reconcile(saved_row)
    assert result["state"] == "provider_confirmed" and "error" not in result
    assert restored.saved["received_outpoints"] == ownership
    assert len(api.chain.posts) == 1


async def test_driver_confirmation_zero_and_return_are_read_only(tmp_path):
    _, api, _, row, _, args, tx = await authorised(tmp_path)
    await api.collections.report(row, args | {"raw_tx": tx.hex()})
    details = api.chain.details

    async def unconfirmed(txid):
        return {"txid": txid, "confirmations": 0}

    api.chain.details = unconfirmed
    result = await api.collections.reconcile(row)
    assert result["state"] == "provider_unconfirmed" and result["confirmations"] == 0
    api.chain.details = details
    assert (await api.collections.reconcile(row))["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1
