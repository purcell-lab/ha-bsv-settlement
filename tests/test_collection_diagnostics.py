"""Fictional retained transactions only. No provider or wallet effects."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.collection_recovery import execute
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from test_budget import no_network
from test_collection import authorised

pytestmark = pytest.mark.asyncio


async def retained(tmp_path):
    hass, api, _, row, _, _, tx = await authorised(tmp_path)
    item = api.collections.get(row)
    item.update(state="broadcast_unknown", signed_raw=tx.hex(), txid=tx.txid())
    await api.collections.save()
    return hass, api, row, tx


async def inspect(api, row):
    return await execute(api.collections, "inspect_driver_collection",
                         {"budget_id": row["terms"]["budget_id"]}, "admin")


async def test_exact_retained_transaction_is_bounded_and_has_no_effects(tmp_path):
    _, api, row, tx = await retained(tmp_path)
    before = copy.deepcopy(api.saved)
    api.store.async_save = AsyncMock(side_effect=AssertionError("must not save"))
    api.chain.request = AsyncMock(side_effect=AssertionError("must not query"))
    api.chain.broadcast = AsyncMock(side_effect=AssertionError("must not broadcast"))
    result = await inspect(api, row)
    assert result["diagnostic"] == "retained_transaction_matches"
    assert result["computed_txid"] == tx.txid()
    assert result["inputs"] == [{"txid": tx.inputs[0].source_txid, "output_index": 0}]
    assert result["outputs"][0] == {"output_index": 0, "amount_sats": 189}
    assert result["retry_authorised"] is False
    assert result["provider_checked"] is False
    assert result["signatures_verified"] is False
    assert result["fee_verified"] is False
    encoded = json.dumps(result)
    for secret in (tx.hex(), row["invitation"]["payload"], api.identity["secret_hex"],
                   api.collections.get(row)["quote"]["payload"]):
        assert secret not in encoded
    assert not any(k in encoded for k in ("signed_raw", "locking_script", "attempt_token", "#budget"))
    assert api.saved == before


@pytest.mark.parametrize("field,value,expected", [
    ("signed_raw", None, "signed_transaction_not_retained"),
    ("signed_raw", "private-data", "invalid_retained_transaction"),
    ("signed_raw", "a" * 16002, "invalid_retained_transaction"),
    ("signed_raw", "00", "invalid_retained_transaction"),
    ("txid", "b" * 64, "retained_evidence_mismatch"),
    ("txid", "private-data", "retained_evidence_mismatch"),
    ("draft_hash", "b" * 64, "retained_evidence_mismatch"),
    ("output_index", 1, "retained_evidence_mismatch"),
    ("quote", {"payload": "private-data"}, "retained_evidence_mismatch"),
])
async def test_missing_or_changed_evidence_never_releases(tmp_path, field, value, expected):
    _, api, row, _ = await retained(tmp_path)
    api.collections.get(row)[field] = value
    before = copy.deepcopy(api.saved)
    result = await inspect(api, row)
    assert result["diagnostic"] == expected
    assert "private-data" not in json.dumps(result)
    assert result["retry_authorised"] is False
    assert api.saved == before
    assert not api.chain.posts


async def test_wrong_recipient_and_restart(tmp_path):
    hass, api, row, _ = await retained(tmp_path)
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    saved = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert (await inspect(restored, saved))["diagnostic"] == "retained_transaction_matches"
    from bsv import PrivateKey
    saved["terms"]["operator_address"] = PrivateKey().address()
    result = await inspect(restored, saved)
    assert result["recipient_matches"] is False
    assert result["diagnostic"] == "retained_evidence_mismatch"


async def test_ha_admin_context_required_and_public_route_denied(tmp_path):
    hass, api, row, _ = await retained(tmp_path)
    coord = SettlementCoordinator(hass, api.entry, api)
    await async_setup(hass, {})
    hass.data["bsv_settlement"][api.entry.entry_id] = coord
    data = {"config_entry_id": api.entry.entry_id, "budget_id": row["terms"]["budget_id"]}
    before = copy.deepcopy(api.saved)
    coord.async_set_updated_data = lambda *args: pytest.fail("must not publish state")
    try:
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", "inspect_driver_collection",
                                           data, blocking=True)
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(
            return_value=SimpleNamespace(is_admin=False)))
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", "inspect_driver_collection", data,
                blocking=True, context=Context(user_id="non-admin"))
        hass.auth.async_get_user.return_value.is_admin = True
        result = await hass.services.async_call("bsv_settlement", "inspect_driver_collection", data,
            blocking=True, return_response=True, context=Context(user_id="admin"))
        assert result["read_only"] is True
        assert api.saved == before
        assert not api.chain.posts
        from test_driver_http import client_for
        client, view = await client_for(hass, api)
        try:
            response = await client.post(view.url, json={
                "action": "inspect_driver_collection", "budget_id": data["budget_id"],
                "token": api.budgets.link_token(data["budget_id"])})
            assert response.status == 400
        finally:
            await client.close()
    finally:
        await hass.async_stop(force=True)


async def test_unknown_budget_and_missing_attempt_fail_closed(tmp_path):
    _, api, row, _ = await retained(tmp_path)
    with pytest.raises(WalletError, match="Unknown"):
        await execute(api.collections, "inspect_driver_collection", {"budget_id": "missing"}, "admin")
    api.saved["driver_collections"].clear()
    with pytest.raises(WalletError, match="No driver"):
        await inspect(api, row)
